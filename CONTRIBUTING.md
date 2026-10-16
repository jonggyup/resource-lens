# Contributing

Run from source using Python 3.10+ on Linux. There are no runtime dependencies.
Run `python3 -m unittest discover -s tests -v` before proposing a change.

Keep Linux telemetry separate from presentation. Add fixture coverage for
units, missing fields, counter resets and process identity when changing a
collector. Use `null`/N/A for unavailable values. Never treat a parser fixture
as proof that a driver works on actual hardware.

GPU reports should include the driver/kernel version, relevant readable sysfs
fields or sanitized `nvidia-smi` output, and which metrics are unavailable.
Do not post hostnames, full process lists or secrets without reviewing them.

Monitoring stays read-only by default; CPU edits require `--control`, a target
preview and confirmation. Maintain unit/cgroup identity checks, authorization,
kernel read-back and conflict-aware Undo for any new control. Never add silent
sudo/pkexec, broad revert operations, or direct writes into systemd-owned cgroups.

Automated tests must use fixtures/mock backends and must not change host
resource policies. The explicitly invoked integration script is separate;
it may manage only the unique units/slice it creates and must clean them up
even after failure. Record real enforcement observations rather than claiming
success solely from a configuration value.
