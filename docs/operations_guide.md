# Operations Guide

## Normal Operation

The Syncthing Integrity Monitor runs continuously alongside Syncthing.

Normal operation consists of:

- consuming Syncthing's combined event stream;
- periodically running the backup-verification cycle;
- checking Receive Only state, configuration integrity, and folder health;
- observing authoritative-source connectivity and API health;
- maintaining persistent event, incident, and verification state;
- detecting bursts of remote file deletions;
- sending notifications when configured conditions are met.

No operator action is required while the monitored backup state remains healthy
and no alert condition requires investigation.

## Check Container Status

On the Synology NAS:

```bash
sudo docker ps --filter name=syncthing-monitor
```

View recent monitor logs with:

```bash
sudo docker logs --since 30m syncthing-monitor
```

Production log timestamps use Hawaii Standard Time through `TZ=Pacific/Honolulu`.

## Receive Only Alert

A Receive Only notification means that Syncthing reports persistent local divergence in one of the monitored Receive Only folders. It does **not** necessarily mean data has been lost.

Possible causes include accidental modification, creation, or deletion directly on the NAS, or other administrative/application activity affecting protected data.

### Investigation

Open the affected folder in the Syncthing UI and inspect its Receive Only state. Determine which files are locally changed, whether the authoritative source still contains the expected data, whether versioned copies exist in `.stversions`, and whether the NAS-side change was intentional.

Do not immediately revert merely because an alert occurred.

### Recovery

If the authoritative source is confirmed correct, use:

**Syncthing → affected folder → Revert Local Changes**

This is a manual recovery action. The integrity monitor never performs it automatically.

Once Syncthing returns the folder to clean Receive Only state, the next
successful `/rest/db/status` poll observes `receiveOnlyTotalItems == 0`.
The monitor then silently rearms the folder so that a future divergence can
generate a new initial alert. With `STATUS_INTERVAL=60`, rearming occurs on the
next verification cycle after Syncthing discovers the recovery; main-loop work
can delay that cycle beyond 60 seconds.

## Configuration Integrity Alert

A `[Syncthing] configuration integrity violation` notification means that one
or more protected folders no longer matches the backup configuration expected
by the monitor.

The monitor requires each protected folder to be Receive Only, unpaused, using
filesystem watching, and configured with Staggered File Versioning with
`maxAge=31536000`. Each protected folder must also include the authoritative
device derived from Syncthing's configured topology.

### Investigation

Review the violations listed in the notification and compare the affected
folder's Syncthing configuration with the required protection settings.

Do not automatically change the configuration merely to clear the alert.
Determine why the setting changed and whether the change was intentional
before restoring the expected configuration.

The monitor reports configuration violations but never repairs them
automatically.

## Folder Health Alert

A `[Syncthing] folder health violation` notification means that Syncthing
reports an operational problem with one or more protected folders.

The monitor treats a folder as unhealthy when `/rest/db/status` reports
`state=error`, when `watchError` is non-empty, or when `/rest/folder/errors`
reports one or more errors.

### Investigation

Review the violations listed in the notification and inspect the affected
folder in Syncthing. Determine whether Syncthing reports a folder error,
filesystem-watcher problem, or specific file-level errors before making
changes.

The monitor reports folder-health violations but never repairs them
automatically.

## Source Connectivity Monitoring

The monitor observes whether the authoritative source device is connected and
persists connectivity transitions. The authoritative device is derived from
Syncthing's configured topology rather than from a configured device name.

Source-connectivity alerting is intentionally disabled during the current
observation period. A source disconnect or reconnect is therefore recorded in
monitor state and logs but does not generate a notification.

The alert threshold remains unresolved and is not hardcoded.

## API Health Monitoring

API health is evaluated once per complete backup-verification cycle. A cycle
includes Receive Only, configuration-integrity, folder-health, and
source-connectivity checks.

If any required observation fails, the consecutive-failure streak increments
once for that cycle, regardless of how many individual API operations failed.
A completely successful verification cycle resets the streak to zero.

The API-health alert threshold remains unresolved and is not hardcoded. The
failure streak is persisted for observation, but API-health notifications are
not currently generated.

## Remote Mass-Deletion Alert

The production detector considers a remote deletion incident significant when at least **50 files** are deleted within **300 seconds**.

Only remote events representing deleted files count toward the threshold. Directory deletion events do not count as deleted files.

### Investigation

When a remote deletion alert occurs:

1. Identify the authoritative source associated with the changes.
2. Determine whether the deletion was intentional.
3. Avoid making additional destructive changes while investigating.
4. Inspect Syncthing file versioning on the NAS.
5. Verify the scope of affected data before beginning recovery.

The monitor is designed to notify rather than automatically intervene.

## Version Recovery

Protected NAS folders use Syncthing Staggered File Versioning.

When a synchronized file is deleted or replaced remotely, previous data may be available beneath the folder's `.stversions` directory according to the configured retention policy.

Availability of a historical version should be verified before relying on it for recovery.

## Monitor State

Persistent state is stored at:

```text
state/monitor-state.json
```

The state file contains the last processed event ID, Syncthing process start
time, Receive Only state, remote-delete incident state, configuration-integrity
state, folder-health state, source-connectivity state, the API-health
consecutive-failure streak, and pending notifications.

Do not routinely delete this file. Deleting it causes the monitor to establish a new baseline on startup rather than continuing from its previous event cursor.

### Corrupt or Invalid State

If the state file cannot be loaded or validated, the monitor fails closed rather
than treating the missing state as a clean backup condition. The container log
reports a fatal error beginning with:

~~~text
fatal: Cannot load state file:
~~~

With the configured restart policy, the container may repeatedly restart and
encounter the same error. No email notification is generated because the
monitor cannot enter its normal monitoring loop.

Preserve the invalid state file before taking recovery action so that it remains
available for inspection. On the production Synology deployment, stop the
restart loop and move the invalid file aside:

~~~bash
cd /volume1/docker/syncthing-monitor && \
sudo docker compose stop syncthing-monitor && \
mv state/monitor-state.json "state/monitor-state.json.corrupt-$(date +%Y%m%d-%H%M%S)"
~~~

Then restart the monitor:

~~~bash
sudo docker compose up -d syncthing-monitor
~~~

The monitor will create a fresh state file and establish a new event and
verification baseline. Preserve the renamed corrupt file until the failure has
been investigated.

Resetting state has operational consequences:

- a protected folder that is already Receive Only divergent at the first
  successful observation is treated as an initial divergence and alerts again;
- any active remote-deletion incident stored only in the discarded state is
  lost and cannot be continued;
- the previous combined-event cursor and other persisted monitoring history are
  discarded.

State reset should therefore be a deliberate recovery action, not a routine
response to monitor startup failure.

## Fresh Deployment

A new deployment does not require an existing monitor state file.

Before starting the container, create the `state/` directory and ensure it is
writable by the UID/GID configured by `user:` in `compose.yml`.

On first startup, the monitor creates a fresh state file using the current
Syncthing event baseline.

Do not copy state from a previous deployment unless intentionally preserving
that monitor's event and incident state.

Runtime state should not be stored in Git.

## Restarting the Monitor

A monitor-container restart is expected to preserve state.

After recreation:

```bash
sudo docker logs --since 2m syncthing-monitor
```

A normal restart while Syncthing itself has not restarted should report behavior similar to:

```text
resuming combined event stream after id <event-id>
```

The event ID should come from persisted state rather than restarting from zero.

## Syncthing Restart

The monitor periodically compares Syncthing's current process start time with the value stored in monitor state.

If Syncthing itself has restarted, the monitor first closes any active remote-deletion incident using the persisted pre-restart count and queues the closing notification. It then resets its event-stream baseline for the new Syncthing process lifetime.

This is different from merely restarting the monitor container, which preserves the active incident and resumes from the persisted event cursor.

## Configuration Changes

Changes to `.env` do not alter the environment of an already-running container.

After changing production environment settings, recreate the monitor container:

```bash
sudo docker compose up -d --force-recreate syncthing-monitor
```

Then inspect its startup log.

Avoid printing the fully expanded Compose configuration when `.env` contains secrets because expanded output can expose credentials.

## Production Settings

The tested production values are:

```text
STATUS_INTERVAL=60
RESTART_CHECK_INTERVAL=60
REMOTE_DELETE_THRESHOLD=50
REMOTE_DELETE_WINDOW=300
```

Notification delivery uses a persistent FIFO queue. At most one queued
notification is attempted per `NOTIFY_RETRY_INTERVAL`. If delivery fails, that
notification remains at the head of the queue for a later retry, so subsequent
notifications are not attempted until it is successfully delivered.

## Secrets

`.env` contains secrets such as the Syncthing API key and SMTP credentials.

Do not commit `.env`, paste its contents into issue reports, keep unnecessary credential-bearing backup copies, or publish expanded `docker compose config` output.

Use `.env.example` as the repository-safe configuration reference.

## Troubleshooting

### Receive Only divergence detection

Receive Only detection does not depend on `LocalChangeDetected`.

Testing with Syncthing 2.0.10 showed that Syncthing could recognize local NAS
additions and modifications in Receive Only database state without emitting a
prompt `LocalChangeDetected` event. The monitor therefore uses
`/rest/db/status` as the sole Receive Only detection mechanism.

The monitor uses `STATUS_INTERVAL=60` as the configured interval for checking
monitored folders. Because the main loop also performs a long-polling event
request, verification cycles are not guaranteed to begin exactly every 60
seconds.

### Revert clears the folder

When Syncthing reports `receiveOnlyTotalItems == 0`, the monitor silently
rearms the folder on the next successful status poll. No recovery notification
is sent.

### No alert for a very brief local change

A local divergence that appears and completely disappears between two
`/rest/db/status` polls can be missed.

There is also a separate Syncthing discovery limitation. If the filesystem
watcher does not discover a local change promptly, the Receive Only counters
may not reflect that change until Syncthing discovers it by another mechanism,
potentially including a later full rescan. The production folders currently
use a 3600-second rescan interval.

The configured 60-second polling interval controls the minimum elapsed time
between verification cycles; actual observations may occur later because of
other main-loop work. It also does not guarantee that Syncthing discovers
every filesystem change within 60 seconds.

These are known limitations of the current design.

### Large number of RemoteChangeDetected modified events

Do not interpret these alone as ransomware evidence.

Testing demonstrated that even a newly synchronized file can be represented as `RemoteChangeDetected action=modified`.

The current monitor therefore does not classify remote modification bursts as ransomware.

## Operational Rule

When an alert occurs:

> Investigate first, preserve recovery options, and make recovery changes manually.

The monitor intentionally does not automate destructive or restorative actions.
