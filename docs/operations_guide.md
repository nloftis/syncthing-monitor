# Operations Guide

## Normal Operation

The Syncthing Integrity Monitor runs continuously alongside Syncthing.

Normal operation consists of:

- consuming Syncthing's combined event stream;
- periodically checking Receive Only folder state;
- maintaining persistent event and incident state;
- detecting bursts of remote file deletions;
- sending notifications when configured conditions are met.

No operator action is required while all monitored folders remain clean and no suspicious remote deletion burst occurs.

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
generate a new initial alert. With the production 60-second polling interval,
this normally occurs within roughly one polling interval after Syncthing
discovers the recovery.

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

The state file contains the last processed event ID, Syncthing process start time, Receive Only state, remote-delete incident state, and pending notifications.

Do not routinely delete this file. Deleting it causes the monitor to establish a new baseline on startup rather than continuing from its previous event cursor.

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

If Syncthing itself has restarted, the monitor resets its event-stream baseline for the new Syncthing process lifetime.

This is different from merely restarting the monitor container.

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

Notification retry behavior is controlled by `NOTIFY_RETRY_INTERVAL`.

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

The monitor polls every monitored folder every 60 seconds.

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

The 60-second polling interval controls how frequently the monitor observes
Syncthing's database state; it does not guarantee that Syncthing discovers
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
