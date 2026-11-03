# Cgroup memory and runtime MemoryHigh

In a process or cgroup inspector, **m** opens group memory usage. It includes
`memory.current`, swap current, cgroup peak, high/max/swap boundaries, protection
settings, selected memory.stat categories, pressure and event counters. Group
accounting includes descendants. It differs from process RSS/PSS; **V** retains the
process sharing inspector. Categories can overlap and should not be added as a total.
Missing fields, first intervals, resets, source age and local/subtree scope remain
explicit. Recorded memory inspection performs no live reads.

MemoryHigh is a reclaim/throttling boundary: usage can remain above it. MemoryMax
is a separate hard boundary with OOM consequences. See the
[kernel memory-controller reference](https://docs.kernel.org/admin-guide/cgroup-v2.html#memory-interface-files)
and [systemd resource-control reference](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html).

## Review and apply

1. From an inspector, **r** reviews the associated service and the scope expansion
   to all its members/descendants. Enable controls with **C** if needed.
2. Press **h MemoryHigh**. Existing service-group mode also accepts **h** for the
   selected service. No policy changes while editing or preparing the preview.
3. Enter positive whole bytes, `K/M/G/T` or `KiB/MiB/GiB/TiB` (base 1024), or `max`.
   Percentages and default/reset are unsupported. Values round up to whole pages;
   the preview shows input bytes, page size and the actual requested value.
4. Review the exact unit/manager/cgroup, fresh current usage, current manager/kernel
   high boundary, hard max, swap and ancestor restrictions. Below-usage requests
   warn about reclaim and allocation stalls. A newly crossed boundary requires a
   fresh preview before applying.
5. Scroll to the end to enable Enter/Apply. Escape cancels the editor/preview.
   Once a write starts, the busy view awaits verification; Escape does not claim
   that the write has been rolled back. Monitoring continues in the background.
6. Read the result in **J Changes**. Verified means manager and native configuration
   were read back; resource outcomes still need observation. Timeout/partial results
   stay unconfirmed. **H** opens measurement health.

The backend sends one runtime `MemoryHigh` property through the selected manager.
It never writes directly into a systemd-owned cgroup or modifies MemoryMax/swap
settings. Existing permissions apply without automatic elevation. The configured
CPU/disk helper does not authorize memory operations.

`max` explicitly writes `MemoryHigh=infinity`. Ancestor and hard limits still apply.
It does not delete the runtime override or infer a persistent default. Runtime
settings remain after dashboard exit, until changed or removed with the unit/reboot.

## Restore and verify

In **J Changes**, select the apply entry and press **u**. Only the latest active
MemoryHigh receipt in this UI session is eligible. The UI reads a fresh restoration
preview, including current usage and any reclaim warning, and requires full review
before confirmation. The backend checks unit invocation, cgroup identity, current
high values, other memory settings and ancestor configuration again before writing.
Another actor's changes, unit restart/replacement or a stale restoration preview
refuse restoration. A known denied request already at prior values clears its
receipt without another write.

Restore returns the captured numeric manager value and native read-back, retaining
a runtime override. It cannot restore reclaimed cache, request latency or lost work.
Disabling controls or exiting does not silently restore settings. Recordings preserve
outcomes, but they are not trusted live restoration receipts.

## Supported path and validation limits

This first workflow requires cgroup v2 memory support, readable local memory.high/
memory.max/current, an active matching service/scope and an accessible owning
systemd manager exposing MemoryHigh/MemoryMax. Unsupported/denied combinations
report a reason. Older optional peak/swap readings can stay unavailable.

Ancestor inspection is bounded to 32 ancestors / a cooperative 150 ms scan budget
and stops at the configured cgroup root. Readable bounds are shared with descendants;
hidden host ancestry remains unknown. Changes since preview invalidate it rather
than silently targeting a new service or replacing external settings.

Automated policy writes use fixtures/in-memory backends. The terminal walkthrough
is synthetic; no host memory policy was changed. See [PR14 validation](M5_MEMORYHIGH_VALIDATION.md).
Live owned-service reclaim/enforcement/restoration and a broader kernel/systemd
matrix remain release checks. MemoryMax and swap controls remain separate work.
