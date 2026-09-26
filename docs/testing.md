# Acceptance Testing

This document records the principal behaviors verified during development of the Syncthing Integrity Monitor.

The tests were performed against Syncthing 2.0.10 with Receive Only folders on a Synology NAS.

---

## Automated Regression Tests

The repository includes dependency-free Python `unittest` coverage for
infrastructure retained during the staged monitor redesign.

Run the suite from the repository root:

```bash
python3 -m unittest discover -s tests
```

Stage 1 coverage includes:

- startup configuration parsing and validation;
- state loading and persistence;
- atomic state-file replacement and failure handling;
- corrupt or invalid state rejection;
- notification queue ordering and retry behavior;
- event-batch ordering, cursor advancement, persistence, and failure handling.

Stage 2 adds automated coverage for the count-based Receive Only evaluator and
its integration with status polling.

Stage 2 Receive Only coverage includes:

- clean startup baseline;
- startup with pre-existing divergence;
- clean-to-divergent transition;
- unchanged divergence;
- worsening divergence;
- component-counter changes with an unchanged total count;
- partial recovery;
- full recovery and rearming;
- comparison with the last known state after an unknown API observation;
- preservation of the last known state when `/rest/db/status` fails;
- migration of legacy Boolean Receive Only state;
- preservation of structured Stage 2 Receive Only state.

Stage 3 deliberately replaces the Stage 1 per-event persistence behavior.
Event batches are now processed in memory and persisted once after the batch.

Stage 3 automated coverage verifies:

- ordered event-batch processing and once-per-batch persistence;
- malformed event payloads are skipped without blocking later valid events;
- only monitored-folder `RemoteChangeDetected` file deletions are eligible;
- remote additions, modifications, and directory deletions are ignored;
- the rolling deletion window excludes events older than the configured window;
- threshold crossing sends one initial incident notification;
- post-threshold deletions contribute to the final incident count;
- wall-clock quiet-period checks close active incidents;
- normal monitor restart preserves persisted active-incident state;
- Syncthing restart closes an active incident with its persisted pre-restart
  count before resetting the event baseline.

The tests use a sanitized `/rest/db/status` fixture captured from the actual
Syncthing 2.0.10 deployment for the clean response shape. Divergent
observations used by the evaluator tests are derived synthetically from that
fixture rather than represented as live-captured production responses.

Stage 2 removes `LocalChangeDetected` from the monitor's Receive Only
detection path. The historical acceptance tests below are retained because
they document the Syncthing 2.0.10 behavior that motivated that design
change; descriptions of v4 event-assisted behavior should not be read as
descriptions of the current Stage 2 implementation.

---

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

The Stage 2 Receive Only detector polls `/rest/db/status` every 60 seconds.

A known architectural blind spot remains:

```text
clean
  -> Syncthing discovers local divergence
  -> divergence completely resolves
  -> next scheduled DB poll
```

If the entire divergent period occurs between database-status polls, the
monitor may never observe it.

There is a separate Syncthing discovery limitation. The monitor can only
observe Receive Only state that Syncthing has already discovered. With the
filesystem watcher operating normally, a discovered divergence should become
visible to the monitor within roughly one 60-second polling interval.

If the filesystem watcher fails or otherwise does not discover a local change
promptly, the Receive Only counters may not reflect the change until Syncthing
discovers it by another mechanism, potentially including a later full rescan.
The production folders currently use a 3600-second rescan interval.

**Result: Known limitation — accepted. The 60-second interval bounds monitor
observation frequency, not Syncthing filesystem-discovery latency.**

## Remote File Deletion Detection

Remote mass-deletion testing verified that the detector:

- consumes `RemoteChangeDetected`;
- counts only `action=deleted`;
- counts only `type=file`;
- excludes directory deletion events;
- uses event timestamps for the rolling window;
- sends one notification when the threshold is crossed (the subject contains `HIGH`; no email priority headers are set);
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

## Stage 2 Production Acceptance

Stage 2 was deployed to the existing Synology monitor container without
modifying production backup data.

Before deployment, all four monitored Receive Only folders reported a clean
state through `/rest/db/status`:

```text
receiveOnlyChangedFiles=0
receiveOnlyChangedDirectories=0
receiveOnlyChangedSymlinks=0
receiveOnlyChangedDeletes=0
receiveOnlyChangedBytes=0
receiveOnlyTotalItems=0
```

The persisted pre-Stage-2 state contained legacy Boolean `false` values for
all four Receive Only folders.

After container recreation with the Stage 2 implementation:

- the monitor resumed the existing combined event stream after event ID 350;
- each legacy Boolean Receive Only value was replaced with structured
  count-based state from a successful `/rest/db/status` observation;
- all four folders persisted `lastObservedCount=0`;
- all six Receive Only counters were persisted for each folder;
- `lastAlertedCount` and `lastAlertTime` remained unset for the clean baseline;
- no spurious Receive Only notification was generated during migration;
- the running container reported `STATUS_INTERVAL=60`;
- the container remained healthy across multiple polling intervals with no
  status-check failures or unexpected monitor log messages.

No production Receive Only divergence was manufactured for this acceptance
test. Dirty-state transition behavior is covered by the automated evaluator
and integration tests rather than by modifying production backup data.

**Result: PASS — Stage 2 clean-state migration and production polling
configuration verified without modifying protected backup data.**

## Stage 4 Backup Verification

Stage 4 extended the monitor beyond Receive Only divergence and remote-deletion
audit by adding periodic verification of configuration integrity, folder health,
authoritative-source connectivity, and complete-cycle API health.

The authoritative source is derived from Syncthing topology rather than from a
display name. The monitor obtains the local device ID from
`/rest/system/status`, excludes that ID from `/rest/config/devices`, and requires
exactly one remaining configured device. Missing local-device membership, no
remote device, or multiple remote devices causes authoritative-source
verification to fail. Each protected folder is then checked independently for
membership of the derived authoritative device.

Stage 4 automated regression coverage verifies:

- authoritative-device derivation from Syncthing topology;
- rejection of missing, zero-remote, and multiple-remote device topologies;
- protected-folder configuration invariants: `receiveonly`, `paused=false`,
  `fsWatcherEnabled=true`, staggered versioning, `maxAge="31536000"`, and
  authoritative-device membership;
- configuration violations are distinguished from observation/API failures;
- folder-health observation through `/rest/db/status` and
  `/rest/folder/errors`;
- initial clean folder health is silent, initial unhealthy state alerts,
  clean-to-unhealthy transition alerts, and unchanged unhealthy state is silent;
- source-connectivity observation and persistence without connectivity
  notifications during the current observation period;
- changing diagnostic connection timestamps or durations alone does not mutate
  persisted connectivity state;
- observation failure preserves the last known connectivity state;
- one API-health failure-streak increment per failed complete verification
  cycle, regardless of how many component observations fail;
- a completely successful verification cycle resets the API-health failure
  streak to zero;
- state normalization adds the Stage 4 configuration, folder-health,
  source-connectivity, and API-health state categories.

At completion of Stage 4 and its corrective topology change, the complete
regression suite contained 108 passing tests.

### Receive Only API-response hardening

Post-Stage-4 review identified a fail-open condition in Receive Only status
parsing. A successful `/rest/db/status` response that omitted a required
Receive Only counter could previously default that counter to zero. In
particular, a missing `receiveOnlyTotalItems` value could incorrectly turn a
previously dirty observation into a clean one.

The parser now requires all six Receive Only counters to be present and
integer-convertible. Missing or malformed required values cause the folder
status check to fail rather than being interpreted as zero. The previous
Receive Only state is preserved so an unknown observation cannot clear a
known dirty state.

Regression coverage verifies that:

- omission of any of the six required Receive Only counters fails closed;
- a present but null required counter fails closed;
- the previously persisted Receive Only state remains unchanged;
- no state save or notification is produced from the invalid observation.

After this hardening, the complete regression suite contains 110 passing
tests.

### Stage 4 Production Validation

Stage 4 was validated against the running Synology deployment using controlled
changes that did not modify protected backup data.

Configuration-integrity validation temporarily disabled **Watch for Changes**
for the `documents` folder. Syncthing then reported
`fsWatcherEnabled=false`; the monitor persisted the configuration violation and
a notification email was delivered. After Watch for Changes was restored, the
monitor observed `fsWatcherEnabled=true` and cleared the violation.

**Result: PASS — a real protected-folder configuration violation was detected,
persisted, and notified without automatic repair.**

Folder-health validation used the live `documents` folder without manufacturing
a destructive fault. `/rest/db/status` reported an idle state with empty
`error` and `watchError` values and zero errors, while `/rest/folder/errors`
reported no folder errors.

**Result: PASS within the safe-production validation boundary — the live healthy
folder was observed correctly. Deliberately creating a production folder fault
was not required.**

Source-connectivity validation paused the authoritative source device. Syncthing
reported it disconnected and paused; the monitor persisted
`connected=false` and logged the transition. After the source was resumed, the
monitor persisted `connected=true` and logged recovery. No connectivity
notification was expected because source-connectivity alerting remains
intentionally disabled during the observation period.

**Result: PASS — authoritative-source connectivity transitions were observed and
persisted without enabling an unresolved alert policy.**

During valid configuration and connectivity transitions,
`apiHealth.consecutiveFailures` remained zero. This confirms that successfully
observed configuration violations or connectivity-state changes are not treated
as API observation failures.

**Result: PASS — successful complete verification cycles preserve/reset the API
failure streak as designed.**

The following Stage 4 policy questions remain intentionally unresolved and are
not established by this acceptance record:

- API-health alert threshold;
- exact API-health logging semantics;
- source-connectivity alert threshold;
- whether or how `lastSeen` should advance during a long-lived connection;
- configuration-violation change and recovery notification policy;
- folder-health change and recovery notification policy.

The monitor also cannot detect its own failure. Complete monitor-health coverage
requires an external heartbeat or dead-man mechanism, which is outside the
current implementation.

## Pre-Stage-2 Production Acceptance State

The following records the production configuration and behavior verified before
the Stage 2 Receive Only redesign. It is retained as historical acceptance
evidence and does not describe the current Stage 2 implementation.

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
