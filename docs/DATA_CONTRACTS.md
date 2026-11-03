# Identity and measurement contracts

## M5 PR14 memory observation and control

Selected-target observations add parsed nonnegative `memory.current`,
`memory.swap.current` and `memory.peak` counters. Raw source values/read outcomes
remain in each node's files. `memory.low`/`memory.min` join the limit inventory.
Optional absence/malformed data remains unknown, never an invented zero or max.
Ancestor summaries add `memory_high` and `swap_max` with source/state/completeness;
navigation recomputes them after removing child nodes from an ancestor view.
Existing sample/recording schema versions remain 1.

Inspector group memory distinguishes group-and-descendant usage from RSS/PSS,
labels peak as cgroup-reported rather than an interval maximum, preserves overlapping
memory.stat categories and local/subtree event scope, and reports observation age.
It reads recorded data only; V retains the separate live process memory workflow.

The `memory` control domain currently permits only `memory_high`. New requests use
positive whole bytes or binary K/M/G/T suffixes, rounded up to the host page size;
max/infinity explicitly writes runtime `MemoryHigh=infinity`. Percentages and
default/reset are not accepted. MemoryMax/swap settings remain observed guards,
not mutation properties. The optional privileged helper still permits CPU/disk only.

MemoryHigh receipts preserve unit/manager, cgroup device/inode, invocation, manager
properties, native high/max read-back, swap boundary and bounded ancestor identities/
settings. Current usage and other changing gauges are observations, excluded from
configuration equality; a new below-usage crossing invalidates the reviewed preview.
Prior page-rounded settings can be restored by checking their captured native value.

Apply creates an unconfirmed receipt before the manager call. Verification requires
both the requested manager value and native `memory.high`, with stable lifetime and
other settings/ancestors. Timeout/partial results remain unconfirmed. Restoration
uses only the latest active domain receipt, a fresh UI preview and fresh checks;
external changes refuse restoration. A denied write already at prior values can
be cleared without another write. Manager-only partial state is recoverable when
native high is still exactly the captured prior value. Restoring values leaves a
runtime override and cannot restore reclaimed cache or lost work.

Control events add the `memory` domain to existing Changes/recordings/replay/export
contracts. Replay cannot create live receipts or access a memory backend. M5 PR14
implements this workflow; live enforcement is a separate validation gate.

## M4 saved evidence, findings and exports

`evidence.py` validates bounded JSON trees and consumed profile fields before saved
evidence is rendered. `recording.py` validates known nested telemetry/context/event
types and finite numbers while keeping schema/envelope version 1. Imports require
regular files. Loaded profile contents have SHA-256 digests; replay sample rows have
content digests in addition to filesystem identity. A same-size/same-timestamp
rewrite therefore cannot silently replace selected loaded evidence.

Sessions scans at most 2,000 directory entries and lists 300 artifacts; remembered
explicit paths are capped at 100 and 64 KiB of index metadata. It scans default state
directories and explicit paths, without arbitrary recursion. Profiles are capped at
32 MiB; repeat sets at 1,000 profiles / 64 MiB per side. Recording limits are 256 MiB,
16 MiB per line and 8 MiB of events; replay windows retain at most 120 snapshots /
16 MiB of encoded sample bytes. Parser depth/item/string budgets also apply.

Original artifacts remain unchanged. `outcomes.json` is a 0600 supplementary sidecar:
schema 1, original `profile_identity` and `profile_digest`, and an `outcomes` map.
Each measured outcome contains `value`, `unit`, `provenance` and `[start,end]` capture
window. The UI requires a complete capture and explicit user-entered measurement
for that displayed window. Units must match for structured outcomes; mixed explicit/
unknown units are omitted. Legacy numeric outcomes keep units embedded in names.
Changed originals invalidate sidecars. Captured finding IDs/rule versions remain
separate from a reopened inspector's current-rule re-analysis.

`ResourceHistory` retains another bounded 120-frame / 2 MiB projection alongside
the original CPU projection, with explicit source identities, times, read outcomes,
pressure and memory counter evidence. PSI rules require three connected source
intervals (each <=2.5s, at least 3s total) with `some.avg10 >=20%`. Local findings are
observed; ancestor findings are possible/shared. Memory counter rules require valid
positive deltas and known local/subtree accounting; their actual interval is shown.
They do not assert ongoing pressure. Missing/stale/moved/replaced evidence is not
reused to establish persistence. Resource finding IDs also bind the source cgroup
lifetime. Each new rule family starts at rule version 1.

Incident events add `threshold`, `unit`, `observed_value`, `persistence_seconds`,
`deduplication_key` and captured `findings`. PSI percent, quota throttle ms/s and
memory event counts have separate thresholds. Cached counter observations cannot
emit the same memory incident repeatedly; file/cooldown budgets remain bounded.
Replay retains original captured rules and can reproduce findings from raw evidence.

Reports retain compatibility fields and add current findings, configuration,
captured findings/control outcomes, comparison coverage and min/max/interval counts.
Per-export aliases replace identifiers, outcome names and provenance consistently;
measurement units and numeric values remain intact. Supplementary metadata uses
private temporary files and atomic replacement. New exports are installed without
overwriting existing paths. Raw originals/sidecars/perf files are unredacted artifacts.

## M3 control events and comparison

The JSONL envelope remains version 1. New control events add `id`, `domain`
(`cpu`, `disk`, `placement`, `hardware`), `action` (`review`, `apply`, `restore`),
`setting`, `started_at`, `completed_at`, `verified_at`, `scope_id`, `before`,
`previous_value`, `requested`, `after`, `device`, `error` and `verification`.
Times are wall-clock seconds. `timestamp` remains the operation start for existing
marker consumers. Successful reviews create no change entry; failed reviews do.

`blocked` means no local mutation receipt was created. `unconfirmed` means a write
may have occurred (including failed restoration); it does not establish either
success or no change. `verified` describes backend configuration read-back. It
does not establish workload enforcement. A verified restore links its original
apply event with `restores_id`; historical apply status is retained.
`before` retains the original apply-preview snapshot, including on restoration
events. `previous_value` on a restore is the original requested apply value;
`requested` is the restoration destination. Backend fresh checks remain authoritative
when actual settings have changed externally.

Live restoration links are held separately from serialized evidence and are bounded
by retained history. Only the latest active receipt in a backend domain is eligible.
The backend still checks lifetime, external changes, domain conflicts and read-back.
A recording never supplies a trusted restoration receipt.

`comparison.py` calculates two requested 30-second windows. Baseline ends at start;
post-change starts at verified completion plus optional recorded `settling_s`
(default zero). Unverified outcomes and reversed completion clocks have no post
window. Other control attempts interrupt observation. Scope changes and recorded
cgroup inode/device/generation mismatches break intervals.
Backwards observation timestamps suppress comparison coverage instead of sorting
measurements into a fabricated continuous interval.

Rates are weighted by overlap with their preceding `elapsed_s` interval, never
extending before the previous observation; a newly observed device/domain has no
compatible interval start. Boundary splits assume a constant rate within an
interval. RAM/PSI gauges use left hold, capped at 2.5 seconds. Null/nonfinite
values and invalid rate durations add no rate coverage. Each metric includes its
mean, contributing interval count, covered seconds, coverage fraction and
`complete` (at least 80% of requested seconds). A zero baseline has no relative
change. These are descriptive resource measurements, not application outcomes.

Systemd InvocationID is checked during the control operation but is not sampled
throughout these observation windows. A later unit restart retaining a cgroup is
an explicit confounder. Retention remains 600 UI-observed snapshots. Legacy
recordings without elapsed intervals can still contribute bounded gauge holds;
old verified events without a completion timestamp cannot establish a post window.

Reports add `comparison` with this method, boundaries and coverage. The original
`sampled_means`/`available_samples` fields remain as explicitly labelled legacy,
unweighted compatibility fields. Interactive rendering uses the new comparison.

## M0–M2 foundations

Introduced by blueprint milestone M0. Implemented types live in
[`resourcelens/model.py`](../resourcelens/model.py). They are pure data contracts:
constructing or decoding one never reads `/proc`, calls systemd, or authorizes a
change. M1 adds the live resolver in `targets.py`, bounded read outcomes in
`sources.py`, and source jobs/caches in `sampling.py`.

## Compatibility and rollout

The existing sample schema and JSONL recording envelope remain version 1. Numeric
telemetry fields retain their existing meanings. Live samples now add:

- `collection_scope`: the collector's session identity and available boot/namespace
  context, captured once when it is constructed.
- `collector.stages`: the current sample's per-stage wall and collector-thread CPU
  costs, in milliseconds.
- `collector.counts`: visible logical CPU, process, workload, cgroup-node, disk,
  network-interface, GPU and energy-domain counts, plus skipped processes.

These additions survive recording/replay. Old samples without them remain valid.
The existing `collector.duration_ms`, `cpu_ms` and `rss_bytes` fields remain;
`rss_bytes` is process peak RSS, not current memory consumption.

M1 adds `source_quality`, `source_activity`, `source_cadence_s`, and optional
`target_context` to live samples. Quality is derived from read/parse outcomes and
source observations, independently of stage timings. A fast successful function
return does not establish whether a rate has a valid previous sample.

Current producers cover host CPU busy, available RAM, PSI `some.avg10`, process
enumeration/membership coverage, optional frequency/GPU utilization, inventory,
hierarchy collection, and selected-target details. They do not claim all optional
fields within a source are available. Resource topology reports partial/unknown
completeness while retaining a concrete observation timestamp. Disk/network and
energy continue to expose their existing per-metric availability/status fields.

When metadata is absent, `source_quality(sample, source_id)` returns status
`unknown`, reason `not_recorded`, and no invented source timestamp or coverage.
Existing measured zero values stay zero. Unknown quality does not turn a numeric
legacy reading into an unavailable reading.

## Collection scope

`CollectionScope` contains:

| Field | Meaning |
| --- | --- |
| `id` | Random ID for one collector instance; stable across its samples |
| `boot_id` | Observed boot ID, or null when unreadable/unexposed |
| `pid_namespace`, `cgroup_namespace` | Observed `/proc/self/ns` device/inode strings, or null |
| `cgroup_root_device`, `cgroup_root_inode` | Identity of the visible configured cgroup root, or both null |

A visible cgroup root does not establish access to the system hierarchy root.
Namespace/mount resolution is deliberately not inferred from these fields. The
collector instance ID is the conservative identity boundary even when two sessions
report the same boot. Future cross-session comparisons need an explicit scope
compatibility check. Fixture proc/sys roots can leave the context unknown.

Scope context is part of raw recordings, which already contain identifiers; it
must not be copied into a supposedly redacted report without considering it. The
benchmark exports cost/count summaries and excludes collection scope identifiers.

## Target identity

The initial `TargetRef` supports `process` and `cgroup`. Device/thread/service
references can be added when their consumers arrive; current control sessions keep
their existing manager/unit/invocation checks.

- `ProcessIdentity(scope_id, pid, start_ticks)` distinguishes PID reuse and sessions.
  A renamed process keeps its identity. Exact cgroup membership is a separate
  relationship: moving groups must invalidate group evidence without inventing
  a new process identity.
- `CgroupIdentity(scope_id, path, device, inode, generation=0)` distinguishes
  filesystems, replacements and observed lifetimes. The M1 resolver increments
  generation when it observes disappearance/recreation, including inode reuse.
  The data type does not detect lifecycle events by itself.
- `TargetRef.label` is excluded from equality/hash. It is presentation text and
  never sufficient for matching a live target or authorizing a mutation.
- Cgroup paths must be absolute/canonical. PIDs are positive integers; start ticks
  and generations are nonnegative integers. Boolean values are not numeric IDs.

`TargetRef.to_dict()` / `from_dict()` round-trip through JSON without losing identity.
No method interprets a recorded target as a live target.

## Source quality

`SourceQuality` separates observation quality from derived freshness:

| Field | Contract |
| --- | --- |
| `source_id` | Stable source name; must agree with its key in the sample map |
| `status` | `ok`, `partial`, `unavailable`, `error`, or `unknown` |
| `reason` | Null for `ok`; otherwise an explicit reason listed below |
| `scope_id`, `sequence` | Optional collection scope and nonnegative source sequence |
| `observed_at_wall` | Source observation wall time, not an inferred snapshot timestamp |
| `observed_at_monotonic` | Observation time on the recorded scope's monotonic clock |
| `interval_start`, `interval_end` | Both absent, or a positive interval on that clock |
| `duration_ms` | Optional finite, nonnegative source work duration |
| `coverage` | Examined/omitted counts and truncation reason; unknown counts remain null |

Reasons: `permission_denied`, `not_exposed`, `disappeared`, `parse_error`, `warmup`,
`counter_reset`, `timed_out`, `budget_exceeded`, `not_recorded`, `identity_changed`,
`membership_changed`, `unknown`.

`freshness(now_monotonic, max_age, scope_id)` returns `fresh`, `stale`, or `unknown`.
It does not overwrite `partial`/`unavailable` or reinterpret errors as success.
Mismatched scopes, absent observation time, or a backwards clock yield unknown.
Never pass a replayed scope ID with the current machine's clock to fabricate live
freshness; replay uses recorded context and its own presentation.

All numeric metadata must be finite; negative durations, reversed/incomplete
intervals and invalid statuses are rejected. JSON decoding is explicit and does
not mutate the original sample. Unknown future contract fields currently need an
intentional reader update; the existing outer sample validator still tolerates
optional metadata it does not use.

Per-metric exceptions are deferred until the first producer needs them. A source
record must not claim all metrics are available merely because one metric is valid.

## Constraints

`Constraint` carries resource/property, origin `TargetRef`, unit, value, state,
source quality, optional observed owner, and ancestry visibility.

| State | Value | Meaning |
| --- | --- | --- |
| `known` | Finite number or nonempty text | An observed setting; zero is valid |
| `unlimited` | null | Explicit unlimited setting was observed |
| `unavailable` | null | The requested setting could not be obtained |
| `unknown` | null | Evidence cannot establish the constraint |

Ancestry visibility is `to_system_root`, `to_visible_root`, `partial`, or `unknown`.
Known quality scope must match the constraint origin's scope. Owner text describes
an observed relationship, not authorization. The contract does not compute an
effective quota, reserved capacity, or performance prediction. Existing scalar
limit fields retain their existing meanings. The M1 resolver supplies a separate
exact-target context; it does not relabel a service group's counters as the
selected process's counters. Readable constraints at a visible mount root are
also included in the legacy service limit scan when exposed.

## Resolver acceptance fixtures

[`target-context-scenarios.json`](../tests/fixtures/target-context-scenarios.json)
records the expected M1 behavior for:

- A tighter child constraint below a service.
- A limiting visible root with hidden ancestry.
- An unreadable parent versus explicit unlimited settings and a real zero limit.
- Membership movement during collection, PID reuse, cgroup replacement, and
  observed recreation with a reused inode.

M0 tests enforce identity contracts and JSON/legacy-recording compatibility.
M1 executes the resolver-specific expectations against filesystem fixtures, with
additional races, resets, mount mappings and worker tests. Diagnoses follow in M2.

## M1 collection and selected-target context

CLI monitoring and benchmarks construct `Collector(background=True)`. Frequency
and GPU reads use separate lazy workers. Selected-target detail uses a third worker
on demand. Each worker has at most one running job and one replaceable latest job.
Programmatic `Collector()` defaults to synchronous optional reads for predictable
fixture/library use. Explicitly close either form when finished.

`source_cadence_s` records cache refresh minimums, not a promise of actual cadence.
Requests occur through sampling or UI selection polling. Each completed source
retains its own observation time. `source_activity` separately describes the latest
attempt, cost, worker-thread CPU, queue delay, and pending state. Main collector
stage timings measure polling/cache cost for asynchronous sources; summing them
does not include worker CPU. Process-wide benchmark CPU includes those threads.

Permission/timeout/parse failures can retain an optional source's previous value
with its original timestamp and a degraded status. Disappeared/not-exposed sources
do not retain obsolete devices. Target-detail errors discard ancestry entirely.
Neither refreshing a snapshot nor attempting a failed read restamps an old value.

CPU hardware/resource topology cache for ten seconds. Online/present CPU changes
invalidate CPU inventory; observed disk/interface/GPU identity changes invalidate
resource relationships. All attempted UI control/restore outcomes invalidate relevant
caches, including uncertain failures. Backends still prepare/verify from fresh
reads independently of display caches. The hierarchy has a one-second minimum
refresh in background mode and computes rates over its actual observation interval.

The UI requests details only for the selected Applications/browser target, except
while frozen or replaying. Process requests include PID, start ticks and snapshot
membership. Browser group paths carry an explicit visible-mount coordinate tag;
process membership/service paths use their observed namespace coordinates.
`/proc/self/mountinfo` supplies cgroup2 mount mapping. If mapping is unavailable or
membership lies outside the visible subtree, the resolver reports that limitation.
A mount root of `/` alone never proves initial-namespace ancestry. Live resolution
conservatively reports visible-root/partial coverage; fixtures can explicitly model
a known system root.

`target_context` contains the request identity, exact group, separate service
association, per-ancestor identities, raw file outcomes, parsed limits/counters,
counter deltas, intervals, memory-event scope, visibility and collection limits.
Local memory events remain separate from subtree events and the mount's
`memory_localevents` option. Leader membership does not establish every thread's
placement; threaded hierarchies have an explicit limitation.

The resolver checks process identity/membership before and after reads, then
rechecks every observed group and the mount identity before publishing baselines.
It bounds ancestry at 32 levels, successfully/over-read file bytes at 1 MiB plus
one sentinel byte, individual files at 32 KiB plus a sentinel, and checked work
time at 150 ms. Root/identity/proc/mount metadata and final rechecks are outside
the ancestry byte budget and remain individually bounded. A single kernel read
can outlast the checked work budget; the input thread and shutdown do not join it.
Identity and counter caches retain at most 256 entries each.

Process enumeration checks a 10,000-entry/200 ms work budget. Omitted counts stay
unknown on truncation. Unreadable/exited/malformed processes remain distinct from
an empty complete scan. These bounds are cooperative between reads, not guaranteed
kernel-read deadlines.

## M2 findings, actions and replay

`diagnostics.py` is a pure interpretation layer. `DiagnosticHistory` projects
distinct source observations into bounded CPU frames (120 frames / 2 MiB encoded),
including missing-context boundaries. It excludes full process inventories and
memory maps. Cached repeats do not add coverage. Rule version 1 emits `Finding`
records with ID, rule/version, target, title, explanation, relevance, evidence,
window, limitations and next-action IDs. IDs depend on the rule and identity,
not a process's display name.

Counter evidence records file/path, counter name, before/after/delta, interval
bounds, source scope and observation wall time. M2 adds per-node observation times
to target contexts; earlier M1 contexts can use their overall source observation
time. Raw quota text must match the validated parsed ceiling before rules use it.
Clock/scope changes, reset/invalid intervals and changed configuration interrupt
persistence. See [M2_VALIDATION.md](M2_VALIDATION.md) for exact rule settings and limits.

Related-group inspection uses captured ancestor identity and recomputes the
remaining constraint chain; a child ceiling cannot be carried into its parent's
view. Replay rebuilds diagnostic history from its recorded window and compares
recorded time, never the replaying host's process or sensor state.

`actions.py` defines named read-only actions shared by inspector buttons/help and
scoped search. Bound search actions retain request and collection-scope identity
from search-open time and recheck before dispatch. Every capture action is disabled
in replay. Existing resource-control backends and their explicit previews remain
separate until M3.

Capturing from an inspector writes optional private `capture-context.json` beside
the profile. Its schema-1 object contains selection-time request/scope, observation
time, findings and an interpretation label. It does not change the sample or JSONL
envelope version and is not a claim of continuously analyzed capture results.
