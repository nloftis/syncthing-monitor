# Syncthing Integrity Monitor

A lightweight notification monitor for a Syncthing-based backup workflow using **Receive Only folders** on a Synology NAS.

The monitor watches for two classes of events:

1. **Unexpected local changes on the NAS** that cause a Receive Only folder to diverge from its authoritative source.
2. **Bursts of remote file deletions** that may indicate an accidental or destructive deletion event on an authoritative source.

The monitor is intentionally **notification-only**. It does not revert files, stop Syncthing, delete files, or perform automated recovery.

## Design Principle

The monitor intentionally favors a simple and defensible model:

> Detect suspicious state, preserve evidence, notify the operator, and leave recovery decisions to a human.

This complements Receive Only replication and file versioning without turning an integrity monitor into an automated recovery system.

## Protection Model

In this environment, source systems such as the `corsair` workstation are authoritative.

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
Authoritative systems
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

The monitor uses two complementary mechanisms.

### Receive Only state polling

The monitor periodically queries:

```text
/rest/db/status?folder=<folder-id>
```

The Receive Only counters returned by this endpoint are treated as the authoritative persistent indication that the NAS copy has diverged locally.

The default production polling interval is:

```text
STATUS_INTERVAL=900 (or 15 minutes.)
```

This relatively conservative interval is intentional because Syncthing documents database status queries as potentially expensive.

### Syncthing event stream

The monitor also consumes the combined Syncthing event stream for:

```text
LocalChangeDetected
RemoteChangeDetected
```

The event cursor is persisted across monitor restarts.

Events are used as an **accelerator**, not as the authoritative representation of Receive Only state.

When a `LocalChangeDetected` event occurs for a monitored folder, the monitor immediately checks that folder's Receive Only state. The event itself does not independently generate a local-change notification.

This design prevents the event path and scheduled database-status path from independently sending duplicate alerts for the same Receive Only incident.

## Receive Only Detection

Receive Only monitoring is state-based rather than change-count based.

The state transition model is:

```text
clean
  |
  | Receive Only divergence detected
  v
dirty  ----> send one notification
  |
  | additional local changes
  v
dirty  ----> no repeated state notification
  |
  | manual recovery / Revert Local Changes
  v
clean  ----> detector rearmed
```

Multiple local changes while a folder remains dirty are treated as **one integrity incident**.

The monitor does not attempt to provide a per-file audit trail of every local change.

## LocalChangeDetected Behavior

Testing with Syncthing 2.0.10 demonstrated that `LocalChangeDetected` should not be relied upon as the sole detector of Receive Only divergence.

Observed behavior included:

| Operation | Receive Only state | `LocalChangeDetected` observed |
| --- | --- | --- |
| NAS-local file addition | Dirty | No prompt event observed |
| NAS-local modification of synchronized file | Dirty | No prompt event observed |
| Revert Local Changes deleting NAS-local divergence | Clean | Yes |

For both the local-addition and local-modification tests, Syncthing recognized the divergence promptly in its Receive Only database state even though no corresponding `LocalChangeDetected` event was observed.

For this reason:

> `/rest/db/status` is the authoritative persistent detector. `LocalChangeDetected` is an opportunistic accelerator.

A useful event can cause detection or rearming sooner than the next scheduled status check, but correctness does not depend on every local change producing an event.

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

When the threshold is crossed, the monitor sends one high-priority notification for that deletion incident.

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

Receive Only polling detects persistent divergence.

An edge case exists when all of the following occur:

1. A folder becomes locally dirty.
2. No useful `LocalChangeDetected` event is emitted.
3. The divergence is completely resolved before the next scheduled `/rest/db/status` check.

In that case, the monitor may never observe the dirty state.

The monitor should therefore be considered an integrity-warning mechanism, not a complete filesystem audit system.

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
- Receive Only dirty/clean state
- Active remote-delete incidents
- Pending notifications

State is written atomically.

The `state/` directory is runtime data and should not be committed to Git.

For a new deployment, the monitor creates fresh state automatically.

## Restart Behavior

If the monitor container restarts while the Syncthing process remains unchanged, it resumes the combined event stream from its persisted event cursor.

If Syncthing's reported process start time changes, the monitor treats that as a Syncthing restart and resets its event-stream baseline appropriately.

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

The intended source-controlled project lives on the `corsair` workstation under:

```text
~/GitHub/syncthing-monitor
```

The production deployment is copied to the Synology NAS under:

```text
/volume1/docker/synology_monitor
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
└── synology-monitor.py
```

Historical development snapshots may be retained locally if desired but are not required for deployment.

## Configuration

Create `.env` from the supplied template:

```bash
cp .env.example .env
```

Populate the required Syncthing API and notification credentials.

Never commit `.env`.

Production remote-deletion settings are:

```text
REMOTE_DELETE_THRESHOLD=50
REMOTE_DELETE_WINDOW=300
```

The production Receive Only status interval is:

```text
STATUS_INTERVAL=900
```

## Security

The Syncthing API key and SMTP credentials are secrets.

They belong only in `.env` or another appropriate secret-management mechanism and must not be committed to Git.

The monitor requires access to the Syncthing REST API but does not require write access to the protected Syncthing data directories.

The supplied Compose configuration runs the container as UID/GID `1027:100`,
which corresponds to the dedicated `svc-docker:users` account on the system
used for this deployment. Adjust `user:` to an appropriate unprivileged
UID/GID on other hosts.
