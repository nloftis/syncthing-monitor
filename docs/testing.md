# Acceptance Testing

This document records the principal behaviors verified during development of the Syncthing Integrity Monitor.

The tests were performed against Syncthing 2.0.10 with Receive Only folders on a Synology NAS.

## Receive Only Local Addition

A disposable file was created directly in a NAS-side Receive Only folder.

Observed:

- Syncthing recognized the folder as locally divergent.
- `/rest/db/status` reported a non-zero Receive Only change count.
- No prompt `LocalChangeDetected` event was observed during repeated direct event-stream checks.
- The scheduled database-status fallback detected the persistent dirty state.
- One notification was sent.

**Result: PASS — persistent Receive Only divergence is detected independently of `LocalChangeDetected`.**

## Receive Only Recovery After Local Addition

The local addition was recovered using Syncthing's **Revert Local Changes** operation.

Observed:

- the NAS-local file was removed;
- Syncthing emitted `LocalChangeDetected` for the deletion;
- the monitor consumed the event;
- v4 immediately checked Receive Only state;
- Receive Only state transitioned from dirty to clean;
- the monitor rearmed without waiting for the next 15-minute status check;
- no unnecessary notification was generated for the clean transition.

**Result: PASS — event-assisted immediate rearming works.**

## Existing File Local Modification

A synchronized test file was first created on the authoritative source and allowed to synchronize normally to the NAS. The NAS baseline was verified clean.

The synchronized file was then modified directly on the NAS.

Observed:

- Syncthing promptly showed Receive Only divergence;
- `/rest/db/status` reported one changed file;
- no prompt `LocalChangeDetected` event was observed;
- the scheduled database-status check detected the dirty state;
- exactly one local-change notification was sent.

**Result: PASS — local modification is detected through the authoritative database-status fallback.**

## Receive Only Recovery After Local Modification

The modified file was recovered using Syncthing's **Revert Local Changes**.

Observed:

- Syncthing emitted `LocalChangeDetected`;
- the monitor immediately performed a targeted Receive Only check;
- state transitioned from dirty to clean;
- the folder was rearmed immediately.

This reproduced the event-assisted recovery behavior previously observed after the local-addition test.

**Result: PASS — v4 event-assisted rearming reproduced.**

## Duplicate Notification Prevention

Earlier monitor behavior allowed the event path and scheduled Receive Only check to independently notify about the same local-change incident.

v4 changed the model so that `LocalChangeDetected` does not directly send an alert. Instead:

```text
LocalChangeDetected
        |
        v
targeted Receive Only status check
        |
        v
existing clean/dirty transition logic
        |
        v
notification only on clean -> dirty
```

The scheduled status check uses the same state transition logic.

**Result: PASS — Receive Only notification ownership is centralized in state transitions rather than duplicated between detection mechanisms.**

A clean-to-dirty event-triggered alert was not directly observed during these tests because the tested local addition and local modification did not produce prompt `LocalChangeDetected` events.

The event-triggered code path itself was directly exercised during dirty-to-clean recovery.

## Multiple Local Changes During One Dirty Period

The monitor's Receive Only detector is intentionally state-based.

Once a folder transitions `clean -> dirty`, one incident notification is generated. Additional local changes while the folder remains dirty do not create additional state-transition notifications. After `dirty -> clean`, the detector rearms.

**Result: Behavior confirmed by design and state-transition testing.**

This is not intended to provide a per-file local-change audit trail.

## Transient Divergence Limitation

A known architectural blind spot remains:

```text
clean
  -> local divergence
  -> no useful LocalChangeDetected event
  -> divergence completely resolved
  -> next scheduled DB poll
```

If the entire dirty period occurs between database-status polls, the monitor may never observe it.

**Result: Known limitation — accepted.**

## Remote File Deletion Detection

Remote mass-deletion testing verified that the detector:

- consumes `RemoteChangeDetected`;
- counts only `action=deleted`;
- counts only `type=file`;
- excludes directory deletion events;
- uses event timestamps for the rolling window;
- sends one high-priority notification when the threshold is crossed;
- continues tracking the full incident after threshold crossing;
- produces the complete incident count when the burst closes;
- persists notification retry state;
- survives monitor-container recreation;
- handles Syncthing process restart separately from monitor restart.

Testing used a deliberately low threshold to make controlled tests practical.

The production threshold was subsequently restored to:

```text
REMOTE_DELETE_THRESHOLD=50
REMOTE_DELETE_WINDOW=300
```

**Result: PASS — remote mass-file-deletion detection and incident tracking verified.**

## Remote Deletion Recovery Evidence

A controlled five-file remote deletion test was performed.

Syncthing versioning retained recoverable file data in `.stversions`.

**Result: PASS — versioning provided recovery evidence for the tested deletion scenario.**

The monitor itself does not perform recovery.

## Remote Modified Event Semantics

During creation of the synchronized modification-test file, Syncthing emitted:

```text
RemoteChangeDetected
action=modified
```

even though the file was newly arriving from the authoritative source.

**Result: Important finding — `action=modified` cannot safely be treated as evidence of destructive overwrite or ransomware activity by itself.**

The current monitor therefore detects remote mass deletions but does not attempt to classify remote modification bursts as encryption.

## Event Cursor Persistence

The monitor container was recreated while the Syncthing process remained unchanged.

Observed startup behavior:

```text
resuming combined event stream after id 6
```

The monitor preserved its state file and combined event cursor, did not restart at event ID zero, and did not replay an old notification.

**Result: PASS — monitor-container recreation preserves event-stream continuity.**

## State Normalization

State schema version 4 normalization was initially applied in memory but was not immediately persisted.

The production code was updated so that existing state is normalized and atomically saved during startup:

```python
s = normalize_state(s)
atomic_save(s)
```

The modified code passed Python compilation and normal container startup.

**Result: PASS — normalized state is now persisted immediately.**

## Logging Timezone

The Python operational logger uses the container's local timezone.

The container was configured with:

```text
TZ=Pacific/Honolulu
```

The Alpine image already contained the required `Pacific/Honolulu` zoneinfo data.

After recreation, the monitor logged in Hawaii Standard Time while explicitly UTC machine timestamps remained UTC.

**Result: PASS — human-facing logs now match the NAS/operator timezone.**

## Notification Retry

Notification failure/retry behavior was tested during development.

Pending notifications persisted in monitor state and survived container recreation.

**Result: PASS — transient notification failure does not discard queued notification state.**

## Production Acceptance State

At completion of functional testing:

```text
STATUS_INTERVAL=900
RESTART_CHECK_INTERVAL=60
REMOTE_DELETE_THRESHOLD=50
REMOTE_DELETE_WINDOW=300
TZ=Pacific/Honolulu
```

The production monitor:

- starts successfully;
- resumes persisted event state;
- detects persistent Receive Only divergence;
- avoids repeated alerts while a folder remains dirty;
- rearms after recovery;
- uses events opportunistically for faster checks;
- detects suspicious remote mass-file-deletion bursts;
- persists notification retry state;
- performs no automatic recovery or destructive action.

## Acceptance Conclusion

The monitor meets its intended scope:

> Provide an additional notification layer around a Receive Only + versioned Syncthing backup target without automating recovery.

It should not be treated as:

- a complete filesystem audit trail;
- ransomware detection based on file modifications;
- a replacement for independent backups;
- an automated recovery system.
