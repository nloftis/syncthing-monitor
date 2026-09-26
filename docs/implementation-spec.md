# Syncthing Integrity Monitor --- Implementation Specification

## 1. Purpose and status

`syncthing-monitor` is a notification-only monitor for a Syncthing-based
NAS backup. It detects unexpected Receive Only divergence, audits remote
deletion bursts, verifies backup health and configuration, and records
operational verification state. It does **not** repair backup data or
change Syncthing configuration.

Syncthing **Receive Only** behavior and **Staggered File Versioning**
provide the actual data-protection and recovery mechanisms. The monitor
observes, records, and alerts.

This document describes the **current implemented design** unless a
section is explicitly marked unresolved, experimental, or future work.
The implementation, automated tests, and production acceptance record
are authoritative when older planning assumptions conflict with current
behavior.

The central design rule is:

> **Use polling for state; use events only where events provide
> information polling cannot.**

The monitor has four independent concerns:

1.  **Receive Only divergence** --- periodic `/rest/db/status`
    observation.
2.  **Remote deletion audit** --- `RemoteChangeDetected` events for path
    and timing information.
3.  **Backup health/configuration** --- periodic REST API verification.
4.  **Operational health** --- durable notification delivery and
    complete-cycle API-health tracking.

State for these concerns remains independent. Failure or recovery in one
category must not incorrectly clear, re-arm, or redefine another.

------------------------------------------------------------------------

## 2. Non-goals

The monitor does not:

-   automatically run **Revert Local Changes**;
-   restore files or versions;
-   change folder types;
-   pause or unpause folders;
-   change versioning configuration;
-   add or remove devices;
-   treat an API failure or malformed API response as evidence that a
    folder is clean;
-   attempt to replace Syncthing's own protection mechanisms;
-   currently alert on source-device connectivity loss;
-   currently alert on the API-health failure streak;
-   currently provide an external dead-man/heartbeat mechanism.

------------------------------------------------------------------------

## 3. Architecture

``` text
                         syncthing-monitor
                                |
               +----------------+----------------+
               |                                 |
        periodic verification              event consumer
               |                                 |
     +---------+----------+                      |
     |         |          |                      |
 Receive   health /    source               RemoteChangeDetected
 Only      config      connectivity                |
     |         |          |                  deletion audit
     +---------+----------+
               |
        complete-cycle API health
               |
          persistent state
               |
       persistent FIFO queue
               |
             notifier
```

Polling answers:

> Is the backup currently in the state expected by policy?

Events answer:

> Which eligible remote deletion happened, and when?

`STATUS_INTERVAL=60` is the configured minimum elapsed interval between
backup verification cycles. The main loop also performs long-polling
event requests, so verification cycles are not guaranteed to begin
exactly every 60 seconds.

------------------------------------------------------------------------

## 4. Receive Only divergence

### 4.1 Detection mechanism

For every protected folder, the monitor queries `/rest/db/status`.

`LocalChangeDetected` is not part of the production event subscription.
`FolderSummary` is not used as the production divergence detector.

The primary divergence condition is:

``` text
receiveOnlyTotalItems == 0  -> clean
receiveOnlyTotalItems > 0   -> divergent
```

### 4.2 Required observation

A successful Receive Only observation requires all six counters:

``` text
receiveOnlyChangedFiles
receiveOnlyChangedDirectories
receiveOnlyChangedSymlinks
receiveOnlyChangedDeletes
receiveOnlyChangedBytes
receiveOnlyTotalItems
```

All six values must be present and integer-convertible.

A missing, null, or otherwise malformed required counter makes the
observation fail. It must not be defaulted to zero. The previous
persisted Receive Only state is retained, no false recovery is inferred,
and no notification is generated from the invalid observation.

This enforces the invariant:

``` text
unknown observation != clean observation
```

### 4.3 Persisted Receive Only state

The monitor persists the six required counters for each protected
folder, together with alert-tracking state used by the Receive Only
evaluator.

`receiveOnlyTotalItems` is the count used to determine whether
divergence appeared, worsened, partially recovered, or fully recovered.

### 4.4 Transition behavior

  -----------------------------------------------------------------------
  Transition                          Behavior
  ----------------------------------- -----------------------------------
  No prior state → `0`                Silent; establish clean baseline

  No prior state → `>0`               Alert; do not silently adopt
                                      pre-existing divergence

  `0 → >0`                            Alert immediately

  `>0 → larger`                       Persist worsening; notification
                                      subject to coalescing policy

  `>0 → same`                         Silent

  `>0 → smaller but >0`               Silent; persist partial recovery

  `>0 → 0`                            Silent recovery; re-arm future
                                      initial divergence alert

  Unknown observation                 Preserve previous state; do not
                                      infer recovery
  -----------------------------------------------------------------------

Component counters may change while `receiveOnlyTotalItems` remains
unchanged. That does not constitute worsening for notification purposes.

### 4.5 Worsening-alert coalescing

Every successfully observed count change is persisted. The minimum
interval for repeat worsening notifications remains **unresolved** and
must not be invented without an explicit policy decision.

### 4.6 Detection limitation

A divergence that appears and disappears entirely between successful
observations can escape detection.

Prompt detection also depends on Syncthing discovering the filesystem
change. The protected production folders use filesystem watching and a
3600-second full rescan interval. A failed watcher can therefore
materially delay discovery even while the monitor itself continues
polling successfully.

------------------------------------------------------------------------

## 5. Status polling cost and evidence

On the production DS224+ running Syncthing 2.0.10, idle
`/rest/db/status` measurements were approximately:

``` text
tdtbs-2don4  0.001336 s
rpaem-rd2ho  0.001281 s
asohy-5azks  0.001186 s
rrygc-s9sw5  0.001168 s
```

A one-minute stress test issued approximately 240 status requests. The
current verification path performs two `/rest/db/status` observations
per protected folder --- Receive Only and folder health --- so four
folders produce approximately eight configured status calls per minute
before timing delays. The stress test was therefore approximately **30
times the configured status-call rate**.

DSM Resource Monitor showed no concerning sustained load during the idle
test.

Status-call cost during a substantial active synchronization remains
**unresolved**.

------------------------------------------------------------------------

## 6. Global Syncthing counters --- experimental evidence

`/rest/db/status` exposes global counters including:

``` text
globalFiles
globalDeleted
globalTotalItems
```

These counters are **not part of the current persisted monitor state**
and are not used by the current alert logic.

A controlled Documents test produced:

``` text
                      initial   +3 files   delete 3   recreate 3
globalFiles              8146       8149       8146        8149
globalDeleted             172        172        175         172
globalTotalItems          8667       8670       8670        8670
```

This establishes that `globalDeleted` behaves as a current-state
deleted-entry count rather than a cumulative deletion-operation counter.

Therefore the monitor must not assume:

``` text
delta(globalDeleted) == number of remote deletion operations
```

Deletes and recreations can cancel between observations. Tombstone/index
exchange behavior remains unresolved. `globalDeleted` may be useful for
future coarse-state analysis, but it is not a proven event-completeness
signal.

------------------------------------------------------------------------

## 7. Remote deletion audit

### 7.1 Event subscription

The production event subscription uses:

``` text
RemoteChangeDetected
```

An event is eligible for the deletion incident detector when:

``` text
event type = RemoteChangeDetected
action     = deleted
item type  = file
folder     = protected folder
```

Remote additions and modifications do not generate deletion-incident
alerts. Directory deletions do not count toward the current threshold.

### 7.2 Incident threshold

The production rule is:

``` text
rolling window: 300 seconds
threshold:      50 eligible file deletions
```

When the 50th eligible deletion falls within the rolling 300-second
window, the monitor queues the initial incident alert immediately.

Once an incident is active, additional eligible deletions increase the
incident count without generating another threshold alert for the same
incident.

### 7.3 Incident close rule

An active incident closes when wall-clock time satisfies:

``` text
now - last_eligible_deletion_time >= 300 seconds
```

The close check runs independently of new event arrival. The closing
notification reports the final incident count, including eligible
deletions received after the initial threshold crossing.

### 7.4 Restart behavior

A normal monitor-container restart preserves active incident state.

When Syncthing itself restarts and its event namespace must be reset,
the monitor closes any active incident using its persisted pre-restart
count before resetting event-baseline/restart state.

### 7.5 Event-batch processing

For each returned event batch, the monitor:

1.  processes the batch in memory;
2.  ignores events from unprotected folders;
3.  validates relevant event data;
4.  logs and skips invalid `RemoteChangeDetected` data encountered
    during event processing;
5.  processes eligible deletion events;
6.  advances the event cursor only from a valid event ID;
7.  persists event/incident state once per processed batch when state
    changed.

Malformed event envelopes are not broadly guaranteed to be harmless:
cursor advancement still requires a valid event ID.

### 7.6 Event limitations

The following remain unresolved:

-   event buffer size and practical lag behavior;
-   event-ID contiguity assumptions under the filtered subscription;
-   tombstone/index-exchange behavior relative to observed events.

Path-level deletion auditing therefore remains dependent on Syncthing's
event subsystem.

------------------------------------------------------------------------

## 8. Authoritative source identity

The monitor does **not** identify the authoritative source by display
name.

For each source-connectivity/configuration verification:

1.  query `/rest/system/status` and require local `myID`;
2.  query `/rest/config/devices`;
3.  require the local `myID` to be present in configured devices;
4.  exclude the local device;
5.  require exactly one remaining configured device;
6.  treat that remaining device ID as authoritative.

Verification fails if:

-   `myID` is absent;
-   the local device is missing from configured topology;
-   no remote device remains;
-   more than one remote device remains.

Each protected folder is then independently checked for membership of
the derived authoritative device.

A protected folder omitting the otherwise-valid authoritative device is
a **configuration-integrity violation**, not an API-observation failure.

------------------------------------------------------------------------

## 9. Backup health and configuration

### 9.1 Source connectivity

The monitor observes the authoritative source through:

``` text
/rest/stats/device
/rest/system/connections
```

The persisted connectivity state contains only:

``` text
deviceId
connected
```

Diagnostic values such as `lastSeen`, connection duration, connection
start time, and observation time may be logged, but changes to those
diagnostic values alone do not mutate persisted connectivity state.

An observation failure preserves the previous connectivity state.

Source-connectivity transitions are currently persisted and logged but
do **not** generate notifications. The alert threshold remains
unresolved.

The semantics of `lastSeen` during a long-lived connection also remain
unresolved.

### 9.2 Folder health

For every protected folder, health observation uses `/rest/db/status`
and `/rest/folder/errors`.

A folder is unhealthy when any of the following is true:

``` text
state == error
watchError is non-empty
/rest/folder/errors reports one or more errors
```

Folder health is independent of Receive Only divergence.

Implemented notification behavior includes:

-   initial healthy observation: silent;
-   initial unhealthy observation: alert;
-   healthy → unhealthy: alert;
-   unchanged unhealthy observation: silent.

The notification policy for one unhealthy condition changing into a
different unhealthy condition, and for unhealthy → healthy recovery,
remains unresolved.

### 9.3 Configuration integrity

Each protected folder must satisfy:

``` text
type == receiveonly
paused == false
fsWatcherEnabled == true
versioning.type == staggered
versioning.params.maxAge == 31536000
derived authoritative device ID is present
```

A successfully observed violation is a configuration-integrity
condition, not an API failure.

Implemented notification behavior includes an alert for an initial or
newly observed configuration violation and silence for an unchanged
violation.

The notification policy for violation A → violation B while still
invalid, and for violation → clean recovery, remains unresolved.

The monitor reports violations but never repairs configuration
automatically.

------------------------------------------------------------------------

## 10. Complete-cycle API health

API health is evaluated once per complete backup-verification cycle.

A verification cycle consists of:

``` text
Receive Only verification
configuration-integrity verification
folder-health verification
source-connectivity verification
```

If every required component observation succeeds, the
consecutive-failure streak resets to zero.

If one or more required observations fail, the streak increments **once
for the complete cycle**, regardless of how many underlying REST
operations failed.

A successfully observed unhealthy folder, configuration violation, or
source disconnect is still a successful observation and therefore does
not itself constitute an API-health failure.

The API-health failure streak is persisted for observation.

API-health notifications are **not currently generated**. The alert
threshold N remains unresolved, as do the exact desired API-health
logging semantics.

API failure means:

> The monitor is alive but cannot completely verify Syncthing backup
> state.

It does not mean the backup is clean.

------------------------------------------------------------------------

## 11. Monitor failure and dead-man monitoring

Internal API-health tracking cannot detect the monitor process itself
dying. A dead process cannot send its own failure notification.

Complete monitor-health coverage therefore requires an **external
heartbeat or dead-man mechanism**. This remains separately scoped future
work.

------------------------------------------------------------------------

## 12. Notification delivery

Condition detection and notification delivery are separate.

``` text
observation
    |
state transition
    |
notification queued
    |
persistent FIFO queue
    |
delivery attempt
    +-- success -> remove first queued notification and persist
    +-- failure -> retain first notification for retry
```

At most one queued notification is attempted per
`NOTIFY_RETRY_INTERVAL`. A failed notification remains at the head of
the FIFO queue, so later notifications wait behind it.

Temporary SMTP failure must not silently discard an alert.

The current implementation does not include a dedicated test-alert
mechanism. Such a mechanism may be added as future operational assurance
work.

------------------------------------------------------------------------

## 13. Persistent state

### 13.1 State categories

The current state includes:

-   Syncthing process start time and combined-event cursor;
-   Receive Only observations and alert state;
-   remote-deletion incident state;
-   configuration-integrity state;
-   folder-health state;
-   source-connectivity state;
-   API-health consecutive-failure streak;
-   pending notifications.

These categories remain logically independent.

### 13.2 Atomic writes

State writes are atomic: the complete new state is written to a
temporary file and then atomically replaces the live state file.

A partially written state file must never replace the last valid state.

### 13.3 Corrupt or invalid state

A corrupt, unparseable, or invalid persisted state file is **not**
interpreted as a clean first start.

Startup fails with an error beginning:

``` text
fatal: Cannot load state file:
```

With the production restart policy, the container can repeatedly restart
into the same failure. Normal email delivery is unavailable because the
monitor does not enter its monitoring loop.

Operational recovery is deliberate:

1.  stop the monitor restart loop;
2.  preserve the invalid state file for inspection;
3.  move it out of the live state path;
4.  restart the monitor;
5.  allow the monitor to create a fresh baseline.

Resetting state has consequences:

-   an already-divergent protected folder is treated as initial
    divergence and alerts again on successful observation;
-   an active remote-deletion incident represented only in the discarded
    state is lost;
-   the previous event cursor and persisted monitoring history are
    discarded.

State reset is therefore a recovery action, not routine maintenance.

------------------------------------------------------------------------

## 14. Startup configuration validation

Configuration is validated before the monitoring loop begins.

Required non-empty settings include:

``` text
ST_API_KEY
FOLDERS
SMTP_USER
SMTP_PASSWORD
MAIL_TO
```

`NOTIFY_METHOD` must currently be `email`.

`MAIL_FROM` defaults to `SMTP_USER` when it is unset. If `MAIL_FROM` is
explicitly set to an empty value, startup validation fails.

`FOLDERS` uses:

``` text
FOLDERS=id:label,...
```

Each entry must contain a non-empty folder ID and label. Malformed
entries are rejected rather than starting with ambiguous monitoring
scope.

------------------------------------------------------------------------

## 15. Security

The monitor runs as an unprivileged account and does not require write
access to protected backup data.

However:

> **The Syncthing API key is privileged; it is not a read-only
> credential.**

Anyone who obtains the key may be able to perform administrative
Syncthing REST operations beyond the monitor's intended reads.

Filesystem least privilege reduces the container's direct filesystem
blast radius but does not make the API credential harmless.

Secrets must not be committed to the repository.

------------------------------------------------------------------------

## 16. Test strategy and acceptance baseline

Core evaluation logic is structured for deterministic testing without
requiring production backup mutations.

Recorded/sanitized Syncthing 2.0.10 fixtures are used where appropriate.

Automated coverage includes:

-   Receive Only clean/dirty/worsening/recovery transitions;
-   startup dirty behavior;
-   required-counter fail-closed validation;
-   remote deletion threshold, close, restart, filtering, and batch
    persistence;
-   configuration invariants and topology-derived authoritative
    identity;
-   folder-health transitions;
-   source-connectivity persistence without alerting;
-   complete-cycle API-health behavior;
-   atomic persistence and corrupt-state rejection;
-   durable notification queue/retry behavior.

After Receive Only API-response hardening, the complete regression suite
contains **110 passing tests**.

Production acceptance has also verified, within a safe-production
boundary:

-   healthy Receive Only production baseline;
-   a real configuration-integrity violation and notification by
    temporarily disabling filesystem watching;
-   healthy folder-health observation without manufacturing a
    destructive fault;
-   authoritative-source disconnect/reconnect observation and
    persistence without connectivity notifications;
-   successful verification cycles retaining/resetting API health
    correctly.

`docs/testing.md` is the detailed acceptance and historical test record.

------------------------------------------------------------------------

## 17. Experimental findings

### 17.1 NAS-local changes

Controlled testing established that NAS-local additions, deletions, and
modifications can produce Receive Only divergence state. Syncthing
filesystem watching can expose that state promptly.

A NAS-local deletion of a synchronized disposable file produced:

``` text
receiveOnlyChangedDeletes = 1
receiveOnlyTotalItems      = 1
```

No corresponding prompt `LocalChangeDetected` event was relied upon.

**Revert Local Changes** restored the tested local divergence and
returned the Receive Only state to clean. These findings support polling
`/rest/db/status` as the authoritative divergence mechanism.

### 17.2 Filesystem watcher health

Live Syncthing 2.0.10 checks confirmed that `/rest/db/status` exposes
`watchError` and that the protected production folders use filesystem
watching.

The verified Documents configuration included:

``` text
fsWatcherEnabled = true
fsWatcherDelayS  = 10
rescanIntervalS  = 3600
```

This is why `watchError` and `fsWatcherEnabled` are monitored
explicitly.

### 17.3 Remote deletion / `globalDeleted`

Deleting three synchronized files from the authoritative source
produced:

``` text
globalFiles:      8149 -> 8146
globalDeleted:     172 -> 175
globalTotalItems: 8670 -> 8670
```

Recreating the same paths produced:

``` text
globalFiles:      8146 -> 8149
globalDeleted:     175 -> 172
globalTotalItems: 8670 -> 8670
```

This confirms that `globalDeleted` reflects current deleted state rather
than a cumulative deletion-operation count.

------------------------------------------------------------------------

## 18. Implemented stages

### Stage 1 --- Infrastructure refactor

Completed:

-   event batch processing;
-   notification queue/retry behavior;
-   state persistence;
-   atomic state writes;
-   corrupt-state handling;
-   startup configuration parsing/validation;
-   regression tests for surviving infrastructure.

### Stage 2 --- Receive Only polling

Completed:

-   `/rest/db/status`-based divergence polling;
-   removal of `LocalChangeDetected` from production subscription;
-   no `FolderSummary` production dependency;
-   count-based divergence state;
-   startup-dirty and worsening behavior;
-   partial/full recovery handling;
-   fail-closed required-counter validation.

### Stage 3 --- Remote deletion hardening

Completed:

-   `RemoteChangeDetected`-only subscription;
-   50-file / 300-second incident rule;
-   initial and closing notifications;
-   final closing count;
-   wall-clock quiet-period close;
-   incident persistence across monitor restart;
-   pre-Syncthing-restart incident close;
-   in-memory batch processing;
-   once-per-batch event-state persistence;
-   filtering of unprotected folders;
-   validation/logging of invalid relevant event data.

### Stage 4 --- Backup verification

Completed:

-   topology-derived authoritative source identity;
-   source-connectivity observation and persistence;
-   `/rest/system/connections` and `/rest/stats/device` diagnostics;
-   folder-health verification;
-   configuration-integrity verification;
-   filesystem-watcher verification;
-   authoritative-device membership verification;
-   complete-cycle API-health streak tracking;
-   production validation of configuration and connectivity behavior.

Source-connectivity and API-health threshold notifications remain
intentionally disabled because their policy thresholds have not been
selected.

### Future operational assurance

Not currently implemented:

-   source-connectivity threshold and notifications;
-   API-health threshold and notifications;
-   external dead-man/heartbeat monitoring;
-   optional explicit test-alert mechanism.

------------------------------------------------------------------------

## 19. Explicit limitations

### Transient local divergence

A local divergence that appears and disappears entirely between
successful observations may not be observed.

### Filesystem discovery

Successful polling does not prove that Syncthing has already discovered
every filesystem change. Watcher failure can delay discovery until
another mechanism, including the configured full rescan.

### Remote modification

Remote-deletion incidents are monitored; remote file modifications do
not have a corresponding incident-alert rule.

### Event subsystem

Path-level remote-deletion auditing depends on Syncthing event delivery,
buffering, cursor behavior, and restart behavior.

### Monitor death

The monitor cannot detect its own death. External monitoring is required
for that failure mode.

------------------------------------------------------------------------

## 20. Unresolved items

The following remain explicitly unresolved and must not be silently
converted into implementation policy:

-   status-call cost during a substantial active synchronization;
-   whether/how `lastSeen` advances during a long-lived connection;
-   source-connectivity alert threshold N;
-   API-health alert threshold N;
-   exact API-health logging semantics;
-   Receive Only worsening-alert coalescing interval;
-   configuration violation A → B repeat-alert policy;
-   configuration violation → clean recovery-notification policy;
-   folder-health unhealthy A → B repeat-alert policy;
-   folder-health unhealthy → clean recovery-notification policy;
-   event buffer size and practical lag behavior;
-   event-ID contiguity assumptions;
-   tombstone/index-exchange behavior relative to `globalDeleted`;
-   strength and future operational meaning, if any, of `globalDeleted`.

------------------------------------------------------------------------

## 21. Current design decisions

The following are settled unless new evidence requires reconsideration:

-   Receive Only divergence is determined from periodic
    `/rest/db/status` observations.
-   `STATUS_INTERVAL=60` is a configured minimum interval, not a
    guarantee of exact 60-second cycle starts.
-   `LocalChangeDetected` is not used for production divergence
    detection.
-   `FolderSummary` is not used for production divergence detection.
-   All six required Receive Only counters must be present and valid.
-   API unknown or malformed Receive Only status is never interpreted as
    clean.
-   `receiveOnlyTotalItems` defines divergence and worsening.
-   Partial recovery is silent.
-   Full recovery silently re-arms future initial divergence alerting.
-   Startup with an already-divergent folder alerts.
-   Remote deletion auditing uses `RemoteChangeDetected`.
-   The deletion threshold is 50 eligible file deletions in 300 seconds.
-   Incidents close after a wall-clock 300-second quiet period.
-   Closing notifications report the final incident count.
-   Active incidents survive monitor-container restart.
-   Active incidents are closed with their persisted pre-restart count
    before event-state reset after Syncthing restart.
-   Event batches are processed in memory and event/incident state is
    persisted once per changed batch.
-   Events from unprotected folders are ignored.
-   Authoritative source identity is derived from Syncthing topology,
    never from a display name.
-   The supported topology requires the local device plus exactly one
    configured remote authoritative device.
-   Every protected folder must include the derived authoritative
    device.
-   Protected-folder policy is Receive Only, unpaused, filesystem
    watching enabled, Staggered File Versioning, and
    `maxAge="31536000"`.
-   Folder-health failure is independent of Receive Only divergence.
-   Source-connectivity transitions are persisted/logged but currently
    do not notify.
-   API health is evaluated once per complete verification cycle.
-   One failed cycle increments the API-health streak once regardless of
    the number of failed component observations.
-   A fully successful cycle resets the API-health streak to zero.
-   API-health notifications are currently disabled.
-   Notification delivery uses a persistent FIFO retry queue.
-   Corrupt persisted state fails closed and requires deliberate
    operator recovery; it is never silently treated as a clean baseline.
-   `globalDeleted` is experimental/coarse evidence only and is not
    current persisted monitor state.
-   The Syncthing API key is privileged.
-   The monitor never automatically repairs protected backup data or
    configuration.
