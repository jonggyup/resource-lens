# Optional narrow privileged helper

The UI normally uses the current user's systemd permissions. The helper is an
opt-in alternative for exact system service/scope names selected by an administrator.
It accepts only CPU weight/quota and block-device read/write bandwidth actions.
It has no shell execution, arbitrary file-write, hardware or user-manager API.

Build in the project directory:

```bash
python3 scripts/build_helper.py resource-lens-helper.pyz
```

An administrator reviews the code and installs the isolated archive:

```bash
sudo install -d -o root -g root -m 0755 /usr/local/libexec /etc/resource-lens
sudo install -o root -g root -m 0755 resource-lens-helper.pyz /usr/local/libexec/resource-lens-helper
sudoedit /etc/resource-lens/helper.json
```

The archive uses `/usr/bin/python3 -I` to ignore caller Python paths and startup
customization. The executable, policy and their parent directories must be
root-owned, non-symlink paths and not writable by group/other users. The installer
above does not create a sudo grant or policy file automatically.

Example policy (replace `1000` with the permitted user's numeric UID, and use
actual existing unit names):

```json
{"1000": ["my-build.service", "my-backup.service"]}
```

Make the policy root-owned and readable only as intended (0644 is sufficient;
0600 is also supported). Unit names are exact matches, not shell patterns.
Permission to a unit permits changing its CPU quota/weight and disk bandwidth,
including removing limits. Choose units that the user is authorized to tune.

Configure a command-specific sudo rule with `visudo`, for example:

```sudoers
alice ALL=(root) /usr/local/libexec/resource-lens-helper ""
```

The empty argument string permits invocation without command-line arguments.
The UI uses `sudo -n` and never prompts inside curses. With the authenticated rule
above, establish the normal sudo credential cache outside the UI (`sudo -v`). An
administrator may choose NOPASSWD for this exact helper command under local policy.
Do not authorize a user-writable Python source file or a general Python interpreter.

```bash
python3 -m resourcelens --control --helper /usr/local/libexec/resource-lens-helper
```

CPU weight/quota and disk operations then use the helper. CPU placement, governors
and hotplug do not use it; they still require their normal permissions.

## Request boundary and recovery

The helper reads one bounded JSON request from stdin. A prepare request validates
the exact allowed unit, cgroup identity, manager, live settings and bounded value.
It stores the plan in `/run/resource-lens-helper` (0700) and returns an opaque
receipt. Apply/Undo accept only a receipt, never a caller-supplied replacement
plan. Receipts are owned by the issuing UID, recheck current policy, and serialize
apply/Undo using a file lock. The helper reconstructs a clean environment and
uses only its own systemctl argument lists. No daemon or listening socket exists.

Server receipts retain attempted/confirmed outcomes for recovery and are bounded
to 10,000 entries; they disappear at reboot. A client timeout is unconfirmed,
not success: inspect the service before retrying. UI Undo uses the receipt even
when an apply response was lost. Undo remains subject to identity/conflict checks.
The one-shot JSON protocol also accepts `{"action":"undo","token":"..."}`;
only the receipt's owner and a still-authorizing policy can use it. Administrators
can inspect the private receipts and unit settings if a UI session was lost.

Do not remove receipts while clients are using them. No persistent tuning profile
is installed. Updating code requires rebuilding/reinstalling the trusted archive.

## Verification limits

Real CPU/disk preview, apply, foreign-receipt rejection and Undo were exercised
through the root dispatcher against a disposable service. The built archive was
also run as an unprivileged user and refused to execute privileged operations.
No persistent sudo policy was installed in the development environment; the full
administrator-specific sudo authentication workflow remains deployment validation.
See [live evidence](helper-live-evidence.json).
