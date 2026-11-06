# Saved sessions

**L** opens Sessions from the dashboard. `resource-lens --sessions [PATH]` starts
fully offline and never constructs a live collector. With no path it discovers
`profiles`, `recordings` and `incidents` below the normal resource-lens state
directory (`XDG_STATE_HOME/resource-lens`, or `~/.local/state/resource-lens`).

Discovery visits only these directories, their immediate profile children and
explicitly imported paths. It does not search arbitrary home directories. Recording
files with other suffixes can be imported explicitly. Imports leave originals in
place; the private `session-paths.json` index can be rebuilt. No evidence is deleted
automatically when a budget is reached.

## Browse and reopen

| Key | Action |
| --- | --- |
| Up/Down, Page Up/Down | Select entries or scroll results |
| Enter / click | Open one profile or recording |
| i | Import a recording, session.json, profile directory or repeat directory |
| r | Refresh the bounded list |
| b | Select a profile/repeat set as baseline |
| c | Compare the selected candidate with the baseline |
| o | Add a measured outcome to an opened complete profile |
| x | Export the opened profile/comparison as a redacted summary |
| Esc | Return or close Sessions |

A recording opens in its own replay view. Inspector/search, evidence, Changes and
Timeline use only recorded snapshots. **L** returns to Sessions; replay navigation
retains the selection and inspector. Opening Sessions from a live dashboard preserves
the dashboard selection and continues ordinary host monitoring behind it; recorded
targets never become live collector/detail-worker requests.

Captured findings in **J Changes** retain their original rule IDs/versions. The
reopened inspector explicitly re-analyzes the observations with current rules;
these are separate interpretations. Profiles also show captured context findings
when a capture-context sidecar exists. Missing older context stays unavailable.
CPU stacks/perf files are not executed or decoded automatically.

## Compare compatible runs

Choose a profile or explicit repeat directory with **b**, then another with **c**.
The comparison reuses the CLI's means, sample spread, min/max, repeat counts and
absolute/relative changes. A single run has unknown spread. Process and cgroup
scopes cannot be mixed. Environment differences and workload-label mismatches are
visible caveats; separate process lifetimes remain separate runs.

Only common recorded metrics are compared. Energy domains remain separate; missing
or different hardware host identities, changed domain identities/sources and
incompatible completed-work units suppress affected energy metrics. Partial or
cancelled captures remain readable and are rejected as complete comparison runs.
Counter coverage is shown separately from capture status; unknown or under-80%
coverage remains incomplete even if the capture's status says complete.

Recordings use their own event windows: **[ / ]** chooses 5/15/30/60 seconds in
Timeline or Changes. Baseline/transition/post-verification rules remain in force.
Per-metric covered seconds, min/max and contributing intervals are visible. Recording
versus recording run-set comparison is not offered; select a profile/repeat set for
that workflow.

## Measured application outcomes

In a complete saved profile, press **o** and enter:

```text
p99 | ms | 12.5 | load generator log
```

The fields are name, unit, finite value and measurement source. Only enter measurements
from the displayed capture window. These are user-supplied measurements, not
latency inferred from resource counters. A private `outcomes.json` sidecar records
the value/unit/provenance/window and original profile identity/content digest.
Changing the original profile invalidates the sidecar rather than silently applying
an outcome to another capture. Original `session.json` files remain unchanged.

Matched outcomes require matching explicit units, or legacy numeric outcomes with
the same unit-bearing name on both sides. Mixed explicit/unknown units are omitted.
Keep workload, build, warmup and load comparable; differences alone are not causal
evidence or confidence estimates.

## Exports, limits and recovery

**x** creates a new 0600 summary with consistent per-export identifier aliases,
coverage, comparison results, configuration and measured outcomes. Measurement units
and numeric values remain available. Outcome names/provenance and identifiers are
aliased. **R** inside replay exports retained recording comparison/findings/control
outcomes using the same redaction rules. Raw recordings, perf files, profile originals
and outcome sidecars are separate, unredacted artifacts.

Supplementary metadata uses private temporary files and atomic replacement. Exports
are installed only at new paths. Disk-full/failed writes leave previous metadata and
original captures intact; the UI reports the error. Raw evidence is never removed.

Current bounds:

- 300 list entries, 2,000 scanned directory entries, 100 remembered explicit paths;
  no arbitrary recursive scan.
- 32 MiB per profile JSON; 1,000 repeats and 64 MiB of profile JSON per comparison
  side. Supplementary metadata is limited to 64 KiB; captured context to 2 MiB.
- 256 MiB per recording, 16 MiB per line, 8 MiB of event metadata. A replay window
  retains at most 120 snapshots / 16 MiB of raw sample bytes; the original remains
  on disk. Parser nesting/item/string budgets reject oversized structures.
- Imports require regular files, finite numbers, known schemas and valid consumed
  field types. Symlinks/devices and changed selected evidence are rejected. Loaded
  profiles and replayed rows use content digests in addition to filesystem identity.

At a discovery/path budget, import a narrower location or deliberately manage the
rebuildable path index. File-retention cleanup remains explicit user work.
