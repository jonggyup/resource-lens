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

Keep this release read-only. Resource-changing features need a separate
reviewed design: authorization, scope, effective-value verification, conflict
handling and rollback. Tests must not change the host's resource policies.
