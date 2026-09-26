# Syncthing Integrity Monitor

A lightweight notification monitor for a Syncthing-based backup workflow using **Receive Only folders** on a Synology NAS.

The monitor watches four aspects of backup integrity and operational health:

1. **Receive Only divergence** caused by unexpected local changes on the NAS.
2. **Bursts of remote file deletions** that may indicate an accidental or destructive deletion event on the authoritative source.
3. **Backup health and configuration integrity**, including folder health, filesystem-watcher status, Receive Only mode, versioning, and authoritative-device membership.
4. **Operational verification health**, including authoritative-source connectivity and consecutive failures of complete backup-verification cycles.

The monitor is intentionally **notification-only**. It does not revert files, stop Syncthing, delete files, or perform automated recovery.

## Design Principle

The monitor intentionally favors a simple and defensible model:

> Detect suspicious state, preserve evidence, notify the operator, and leave recovery decisions to a human.

This complements Receive Only replication and file versioning without turning an integrity monitor into an automated recovery system.

## Protection Model

In this environment, exactly one remote Syncthing device is authoritative.
The monitor derives that device from Syncthing's configured device topology
rather than from a configured display name.

The Synology NAS receives those files through Syncthing folders configured as:

- **Receive Only**
- **Staggered File Versioning**
- Version retention: **365 days**

The NAS is therefore intended to behave as a protected replica rather than a peer whose local changes should propagate back to source systems.

The integrity monitor adds detection and notification around that model.

It is **not itself a backup system**. Syncthing provides replication and file versioning; this monitor provides additional visibility into conditions that may require investigation or recovery.

## Architecture

The monitor runs as a small Python container alongside Syncthing on the Synology NAS.

```text
Authoritative source
        |
        | Syncthing
        v
+---------------------------+
| Synology NAS              |
|                           |
| Syncthing                 |
|   Receive Only folders    |
|   Staggered Versioning    |
|            |              |
|            | REST API     |
|            v              |
| Syncthing Integrity       |
| Monitor                   |
|            |              |
|            v              |
| Email notifications       |
+---------------------------+
```

The runtime monitor combines two detection paths with periodic backup-health
verification:

- `/rest/db/status` polling detects persistent Receive Only divergence.
- The Syncthing event stream provides path-level remote-deletion audit data.
- Periodic REST checks verify protected-folder configuration and folder health.
- Source-connectivity state is observed and persisted for operational evidence.
- Each complete verification cycle records whether all required Syncthing
  observations succeeded, providing a consecutive API-failure streak.

Automated regression coverage lives under `tests/` and exercises configuration,
state persistence, notification handling, Receive Only evaluation, remote-delete
event processing, folder health, source connectivity, and backup-verification
cycle behavior independently of the deployed container.

### Receive Only state polling

The monitor queries every monitored folder every 60 seconds:

```text
/rest/db/status?folder=<folder-id>
```

The Receive Only counters returned by this endpoint are the authoritative
indication that the NAS copy has diverged locally.

The production polling interval is:

```text
STATUS_INTERVAL=60
```

Testing on the DS224+ measured idle `/rest/db/status` calls at approximately
0.0012–0.0013 seconds each. A stress test at roughly 240 status calls per
minute showed no concerning sustained idle load.

The current verification cycle makes two `/rest/db/status` calls per monitored
folder: one for Receive Only divergence and one for folder health. With four
folders and `STATUS_INTERVAL=60`, the configured polling rate is approximately
eight status calls per minute, so the stress test exercised roughly 30 times
that configured status-call rate. Because the main loop also performs a
long-polling event request, verification cycles are not guaranteed to begin
exactly every 60 seconds. Status-call cost during active synchronization
remains unresolved.

### Syncthing event stream

The monitor consumes the combined Syncthing event stream for:

```text
RemoteChangeDetected
```

The event cursor is persisted across monitor restarts.

Event batches are processed in memory and persisted once after the batch.
Invalid `RemoteChangeDetected` data encountered during event processing is
logged and skipped so that a bad event payload does not indefinitely block
later events. Event cursor advancement still requires a valid event ID.
Events for folders outside the configured monitored set are ignored.

Events are not used to determine Receive Only divergence. They are retained
for remote-deletion audit information that `/rest/db/status` does not
provide.

## Backup Health and Configuration Verification

Each periodic verification cycle checks the protected Syncthing folders
without modifying their configuration.

For configuration integrity, each protected folder is expected to have:

- folder type `receiveonly`;
- `paused=false`;
- `fsWatcherEnabled=true`;
- staggered file versioning;
- versioning `maxAge="31536000"` (365 days);
- the derived authoritative device present in the folder's device list.

The authoritative device is derived from Syncthing's configured topology.
The local device ID is obtained from `/rest/system/status`, and the monitor
requires exactly one other device in `/rest/config/devices`. A missing local
device, no remote device, or multiple remote devices makes authoritative-source
verification fail.

A protected folder that omits the otherwise valid authoritative device is
reported as a configuration-integrity violation.

Folder health is checked using `/rest/db/status` and `/rest/folder/errors`.
The monitor treats a folder as unhealthy when Syncthing reports an error
state, a non-empty filesystem-watcher error, or folder errors.

Configuration and folder-health violations are notification-only conditions.
The monitor does not repair configuration or protected data automatically.

### Source connectivity

The monitor observes whether the authoritative source is connected and persists
connectivity transitions. Diagnostic connection timestamps and durations are
not persisted merely because they change.

Source-connectivity alerting is intentionally disabled during the observation
period. The alert threshold remains unresolved and is not hardcoded.

### API health

API health is evaluated once per complete backup-verification cycle. A cycle
includes Receive Only, configuration-integrity, folder-health, and
source-connectivity checks.

If any required observation fails, the consecutive-failure streak increments
once for that cycle, regardless of how many individual API operations failed.
A completely successful verification cycle resets the streak to zero.

The API-health alert threshold remains unresolved and is not hardcoded.
The consecutive-failure streak is persisted for observation, but API-health
notifications are not currently generated.

## Receive Only Detection

Receive Only monitoring is count-based. For each monitored folder, the
monitor persists:

```text
receiveOnlyChangedFiles
receiveOnlyChangedDirectories
receiveOnlyChangedSymlinks
receiveOnlyChangedDeletes
receiveOnlyChangedBytes
receiveOnlyTotalItems
```

`receiveOnlyTotalItems` is authoritative for divergence:

```text
receiveOnlyTotalItems == 0  -> clean
receiveOnlyTotalItems > 0   -> divergent
```

The component counters are retained for diagnostic context but do not
independently define whether the folder is divergent.

The monitor also tracks the last observed divergence count and the count and
time associated with the last alert. This distinguishes several transitions:

```text
0 -> >0       initial divergence; alert
>0 -> larger  divergence worsened
>0 -> same    unchanged; silent
>0 -> smaller partial recovery; silent
>0 -> 0       full recovery; silent and rearm
```

If the monitor has no prior Receive Only observation and the first successful
status check is already divergent, it alerts rather than silently adopting
the pre-existing divergence.

Every observed count change is persisted. Worsening divergence is currently
recorded as a worsening candidate, but a separate worsening notification is
not yet emitted because the minimum notification-coalescing interval remains
unresolved.

A failed status request is treated as an unknown observation, not as a clean
folder. The monitor retains the last known Receive Only state and waits for a
successful status check before evaluating another transition.

## Why Receive Only Detection Does Not Use LocalChangeDetected

Testing with Syncthing 2.0.10 showed that `LocalChangeDetected` is not a
reliable trigger for Receive Only divergence.

Observed behavior included:

| Operation | Receive Only state | `LocalChangeDetected` observed |
| --- | --- | --- |
| NAS-local file addition | Dirty | No prompt event observed |
| NAS-local modification of synchronized file | Dirty | No prompt event observed |
| Revert Local Changes deleting NAS-local divergence | Clean | Yes |

For both the local-addition and local-modification tests, Syncthing recognized
the divergence promptly in its Receive Only database state even though no
corresponding `LocalChangeDetected` event was observed.

The monitor therefore does not subscribe to `LocalChangeDetected`.
`/rest/db/status` polling is the sole Receive Only detection mechanism.

## Remote Mass-Deletion Detection

The monitor also watches `RemoteChangeDetected`.

Only events matching both of these conditions count toward the remote-delete detector:

```text
action = deleted
type   = file
```

Directory deletion events are not counted as deleted files.

Production defaults are:

```text
REMOTE_DELETE_THRESHOLD=50
REMOTE_DELETE_WINDOW=300
```

This represents **50 remote file deletions within a rolling 300-second window**.

When the threshold is crossed, the monitor sends one notification for that
deletion incident. The email subject identifies the event as `HIGH`, but the
notifier does not set email priority headers.

It continues counting subsequent qualifying deletions and can provide the complete incident count when the deletion burst becomes quiet.

Event timestamps, rather than notification-processing time, are used for the rolling deletion window.

## Important Limitation: Remote Modifications

The monitor does **not** currently attempt to identify ransomware or destructive overwrites based on `RemoteChangeDetected action=modified`.

Testing demonstrated that a newly created file arriving normally from an authoritative Syncthing peer may appear as:

```text
RemoteChangeDetected
action=modified
```

Therefore, treating every burst of remote `modified` events as encryption or overwrite activity would create false positives.

The current remote detector intentionally focuses on **mass file deletion**.

## Important Limitation: Transient Local Divergence

Receive Only polling detects divergence that Syncthing has discovered and
that is still present when the monitor polls `/rest/db/status`.

With the Syncthing filesystem watcher operating normally, the monitor checks
each protected folder every 60 seconds. A local divergence discovered by
Syncthing should therefore normally become visible to the monitor within
roughly one polling interval.

A transient edge case still exists when all of the following occur:

1. A folder becomes locally divergent.
2. Syncthing discovers the divergence.
3. The divergence is completely resolved before the monitor's next
   `/rest/db/status` poll.

In that case, the monitor may never observe the divergent state.

There is a separate discovery limitation. If Syncthing's filesystem watcher
fails or otherwise does not discover a local change promptly, the Receive
Only counters may remain unchanged until Syncthing discovers the change by
another mechanism, potentially including a later full rescan. The production
folders currently use a 3600-second rescan interval.

The 60-second monitor interval therefore bounds how often the monitor
observes Syncthing's database state; it does not guarantee that Syncthing
itself discovers every filesystem change within 60 seconds.

The monitor should be considered an integrity-warning mechanism, not a
complete filesystem audit system.

## Important Limitation: Monitor Availability

The monitor can record verification failures while it is running, but it
cannot detect or report its own failure. If the monitor process or container
stops, its internal API-health tracking also stops.

Complete monitor-health coverage therefore requires an external heartbeat or
dead-man mechanism. That external monitoring is not currently implemented by
this project.

## Recovery

Recovery is deliberately manual.

When a Receive Only alert occurs:

1. Inspect the affected Syncthing folder.
2. Determine what changed locally on the NAS.
3. Review `.stversions` when appropriate.
4. Confirm that the authoritative source is correct.
5. Use Syncthing's **Revert Local Changes** only after reviewing the incident.

The monitor will never automatically invoke Revert Local Changes.

It also does not automatically stop Syncthing or modify protected data.

After the Receive Only divergence is resolved, the monitor detects the transition back to clean state and rearms the folder for a future incident.

## State

Runtime state is stored by default at:

```text
/state/monitor-state.json
```

The state includes information such as:

- Syncthing process start time
- Last processed combined event ID
- Receive Only observations and alert state
- Active remote-delete incidents
- Configuration-integrity observations
- Folder-health observations
- Authoritative-source connectivity state
- Consecutive backup-verification API failures
- Pending notifications

State is written atomically.

The `state/` directory is runtime data and should not be committed to Git.

For a new deployment, the monitor creates fresh state automatically.

## Restart Behavior

If the monitor container restarts while the Syncthing process remains unchanged, it resumes the combined event stream from its persisted event cursor and preserves any active remote-deletion incident.

If Syncthing's reported process start time changes, the monitor treats that as a Syncthing restart. Any active remote-deletion incident is first closed using its persisted pre-restart count and queued for notification. The monitor then resets the event-stream baseline for the new Syncthing process lifetime.

## Notification Reliability

Failed notifications can remain in persisted monitor state and be retried according to `NOTIFY_RETRY_INTERVAL`.

This prevents a transient SMTP failure from silently discarding an alert.

## Logging

The production container is configured with:

```yaml
environment:
  TZ: Pacific/Honolulu
```

Human-readable operational logs therefore use Hawaii Standard Time.

Machine-oriented timestamps that are explicitly stored as UTC remain UTC.

## Deployment

The intended source-controlled project lives on the authoritative source workstation under:

```text
~/GitHub/syncthing-monitor
```

The production deployment is copied to the Synology NAS under:

```text
/volume1/docker/syncthing-monitor
```

The Git repository is the source of truth for application code and configuration templates.

Runtime secrets and monitor state are not committed.

### Files

Typical repository contents:

```text
syncthing-monitor/
├── compose.yml
├── .env.example
├── .gitignore
├── README.md
├── docs/
│   ├── operations_guide.md
│   └── testing.md
├── tests/
│   ├── fixtures/
│   │   ├── db-status-clean-syncthing-2.0.10.json
│   │   ├── device-stats-connected-syncthing-2.0.10.json
│   │   ├── folder-errors-clean-syncthing-2.0.10.json
│   │   └── system-connections-connected-syncthing-2.0.10.json
│   ├── test_config.py
│   ├── test_connectivity.py
│   ├── test_events.py
│   ├── test_health.py
│   ├── test_notifications.py
│   ├── test_receive_only.py
│   └── test_state.py
└── syncthing-monitor.py
```

Historical development snapshots may be retained locally if desired but are not required for deployment.

## Configuration

Create `.env` from the supplied template:

```bash
cp .env.example .env
```

The monitor validates required configuration before entering the monitoring loop.
Startup fails if any of the following are missing or empty:

- `ST_API_KEY`
- `FOLDERS`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `MAIL_FROM`
- `MAIL_TO`

`NOTIFY_METHOD` must currently be `email`.

`FOLDERS` must contain comma-separated `id:label` entries. Each entry must
include both a non-empty Syncthing folder ID and a non-empty label. For example:

```text
FOLDERS=folder-id-1:documents,folder-id-2:github
```

Other settings shown in `.env.example` have application defaults unless
otherwise noted.

Never commit `.env`.

Production remote-deletion settings are:

```text
REMOTE_DELETE_THRESHOLD=50
REMOTE_DELETE_WINDOW=300
```

The production Receive Only status interval is:

```text
STATUS_INTERVAL=60
```

## Security

The Syncthing API key and SMTP credentials are secrets.

They belong only in `.env` or another appropriate secret-management mechanism
and must not be committed to Git.

The Syncthing API key is a privileged credential, not a read-only monitoring
token. Syncthing's REST API can perform administrative actions, so the key must
be protected even though this monitor uses it only for observation.

The monitor does not require filesystem write access to the protected Syncthing
data directories. Running the container with filesystem least privilege reduces
its direct access to backup data, but does not reduce the privileges carried by
the Syncthing API credential.

The supplied Compose configuration runs the container as UID/GID `1027:100`,
which corresponds to the dedicated `svc-docker:users` account on the system
used for this deployment. Adjust `user:` to an appropriate unprivileged
UID/GID on other hosts.
