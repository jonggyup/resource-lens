# 0.4.0

- Responsive socket/NUMA/core topology tiles with SMT siblings, utilization,
  governor, clocks and offline state; preserve right-click hardware menus.
- Workloads: `p` opens visual service CPU placement; click threads, core headers
  or socket headers, preview, apply and verify systemd/cgroup settings.
- Parent-aware selection, explicit versus inherited sets, session placement Undo
  (`U`), and conflict/restart protection.
- Live disposable-service tests verified placement and both forms of restoration.

# 0.3.0

- CPU topology with present/offline CPUs, sockets and physical core IDs.
- Right-click CPU menus and mouse/keyboard bulk governor and hotplug controls.
- Shared-policy previews, verified writes, partial-failure recovery and session Undo.
- Hardware support and permissions are detected from sysfs; monitoring stays read-only by default.

# Changelog

## 0.2.0

- Add Workloads page (`8`) with cgroup/systemd grouping, accounting, inherited
  limits, member processes, I/O and throttling/pressure details.
- Add optional `--control` with temporary CPU weight/quota editing (`w`/`l`),
  exact-target preview, confirmation, background requests and systemd/kernel
  verification.
- Add conflict-aware session Undo (`u`), timestamped control events and explicit
  exit handling when changes remain active.
- Record actual enforcement, terminal workflow, permission denial and cleanup
  verification using exclusively disposable systemd units.
- Include workload data in JSON snapshots. Monitoring is still read-only by
  default; user-manager control support requires additional live host testing.

## 0.1.0

- Initial live CPU/GPU/memory/disk dashboard, process view and JSON recording.
- Add per-interface network rates, histories, counters and link information.
- Replace unknown history question marks with unavailable-data messages.
- Add resource cards, readable charts, compact layout, ASCII fallback and tabs.
