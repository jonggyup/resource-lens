# M5 PR14 memory observation and MemoryHigh validation

PR14 implementation is complete. The live release gate remains open: all policy
writes in this milestone's validation used fixtures/in-memory backends, and no host
memory policy was changed. MemoryMax and swap controls remain deferred.

## Automated verification

`python3 -m unittest discover -s tests -v`: **398 passing tests**.

- Backend fixtures exercise actual temporary cgroup-like files and a mocked manager
  command runner: byte units/page sizes, no-write preview, manager/native agreement,
  identity/invocation checks, ancestor/hard/startup/swap configuration changes,
  missing controller/property/read permissions, fresh below-usage crossings, denial,
  timeout after a write, false success, manager-only partial recovery, external change
  refusal, LIFO/fresh restore binding and prior page-rounded restoration.
- UI checks exercise process-to-service scope handoff, edit/preview/apply separation,
  full small-terminal review, mouse Apply, cancel/enable controls, readable blocked/
  unconfirmed results, health links, shared Changes, fresh restoration, recorded
  receipts, replay denial and exit acknowledgement. Helper policy still rejects
  the memory domain even for an allowlisted system unit.
- Observation fixtures distinguish zero/missing/malformed scalar readings; current/
  swap/peak from RSS/PSS; overlapping categories; high/max/swap ancestor recomputation;
  local/subtree events, resets, source age and legacy recorded scalars.

## Terminal evidence

Run `python3 scripts/verify_memory_ui.py`. It opens the actual curses UI with
synthetic telemetry and a MemoryHighSession over an in-memory backend. Host manager
commands, hardware access and live captures are forbidden. The walkthrough:

1. Inspects a process and reads exact group memory with m.
2. Reviews service scope, edits MemoryHigh and reads a fresh below-usage warning.
3. Confirms that Enter cannot apply before the complete preview is reviewed.
4. Applies/verifies, prepares a fresh restoration preview and restores/verifies.
5. Opens Changes, resizes from 110x40 to 60x14 and exits cleanly.

[Stored terminal evidence](m5-memoryhigh-ui-evidence.json) records two exact fixture
writes (128 MiB, then unlimited), unchanged hard max, retained 256 MiB observed usage,
matching-output timings and exit status. Keeping usage above the verified boundary
deliberately avoids presenting read-back as enforcement. Full-frame redraw is test
instrumentation; timings are local checks, not benchmarks. This walkthrough also
runs in the terminal suite; M2/M3/M4 walkthroughs remain regression checks.

## Remaining release gates

Disposable owned-service tests still need to establish actual reclaim/pressure,
workload behavior, timeout/recovery and restoration across supported kernel/systemd
combinations. Independent-user usability and a broader hardware/distribution matrix
remain outstanding. Restoring configuration cannot restore lost work or cache.
No MemoryMax control should proceed through its release gate without its separate
risk and recovery cases. [Usage and semantics](MEMORYHIGH.md) link the authoritative
systemd/kernel references used for this implementation.
