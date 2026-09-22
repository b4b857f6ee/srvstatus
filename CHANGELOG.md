# Changelog

## 2.0 — 2026-09-22

### Added
- `proxmox/pve_health.py`: functional health of a Proxmox VE cluster — quorum,
  replication jobs, storage availability, ZFS pools and HA resources — through a
  read-only `PVEAuditor` API token.
- `dashboards/srvstatus-systemd-services.json` and
  `dashboards/proxmox-functional-health.json`, exported for sharing.
- `telegraf/` with ready-to-drop configuration files, documenting the
  `namepass`/`namedrop` pair needed when Telegraf already writes elsewhere.
- Selectable status encoding via `[OUTPUT] convention` in `settings.ini`.

### Fixed
- `active (existed)` was a typo for `active (exited)`: oneshot units never
  matched and were reported as "no match" indefinitely.
- Units that do not exist returned an empty `{}`, which Telegraf cannot tag —
  the whole batch was dropped without an error. They now return the `unknown`
  value.
- A second regex match was dereferenced without being checked, raising
  `AttributeError` on `Active:` lines it did not match.
- `os.system('clear')` ran on `KeyboardInterrupt`, wiping the terminal.

### Changed
- `service.py` no longer parses `systemctl status` text. It reads
  `systemctl show` and computes uptime from the monotonic clock.
- **No third-party dependencies.** `parsedatetime` is gone, and so is the pinned
  `configparser==3.5.0`, a Python 2 backport that shadows the standard library
  under Python 3.
- The original dashboard moved to
  `dashboards/legacy-systemd-services-status.json`.

### Compatibility
The default encoding is unchanged (`legacy`), so 1.x installations keep working
after upgrading `service.py`. Switch to `modern` only together with the new
dashboards.
