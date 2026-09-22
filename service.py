#!/usr/bin/env python3
"""Report SystemD unit status and uptime as JSON, for the Telegraf exec plugin.

Originally by Alexey Nizhegolenko (2018). Rewritten in 2026 to drop all external
dependencies and to fix several defects (see CHANGELOG.md).

Instead of parsing the human-readable output of `systemctl status`, this version
queries `systemctl show`, which returns machine-readable key=value fields, and
derives uptime from the monotonic clock. No locale handling, no regular
expressions, no pip install.
"""

import json
import os
import subprocess
import sys

try:
    import configparser
except ImportError:  # pragma: no cover - Python 2 is not supported
    sys.exit("Python 3 is required")

FIELDS = ("LoadState", "ActiveState", "SubState", "ActiveEnterTimestampMonotonic")

# Two status encodings exist in the wild.
#
#   "legacy"  is what this project has always emitted, and what the bundled
#             dashboard "SystemD Services Status" expects: 4 is the healthy
#             value and colours are graded downwards.
#
#   "modern"  puts the healthy value at 1 and reserves the higher numbers for
#             problems, which reads better in alert rules ("alert when > 1").
#             The dashboards under dashboards/ use this encoding.
#
# Pick one with `convention` in the [OUTPUT] section of settings.ini. The
# default stays "legacy" so existing installations keep working untouched.
ENCODINGS = {
    "legacy": {"active": 4, "exited": 3, "inactive": 2, "failed": 1, "unknown": 0},
    "modern": {"active": 1, "exited": 1, "inactive": 3, "failed": 4, "unknown": 0},
}


def uptime_seconds():
    """Seconds since boot, used to date a unit's entry into its current state."""
    try:
        with open("/proc/uptime", encoding="ascii") as handle:
            return float(handle.read().split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0


def unit_status(unit, encoding, now, user=False):
    """Return one status record for a unit, never an empty dict.

    A unit that does not exist still yields a record with the "unknown" value.
    Returning {} instead — as earlier versions did — produces a JSON object
    Telegraf cannot tag, and the whole batch is silently dropped.
    """
    record = {"service": unit, "status": encoding["unknown"], "status_time": 0}

    command = ["systemctl"] + (["--user"] if user else []) + \
              ["show", unit, "-p", ",".join(FIELDS)]
    try:
        output = subprocess.run(command, capture_output=True, text=True,
                                timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return record

    values = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value

    if values.get("LoadState") != "loaded":
        return record

    active = values.get("ActiveState", "")
    sub = values.get("SubState", "")
    if active == "active":
        # "exited" covers oneshot units that completed successfully. Earlier
        # versions tested for "active (existed)" — a typo — so these units were
        # never recognised and always reported as "no match".
        record["status"] = encoding["exited"] if sub == "exited" else encoding["active"]
    elif active == "inactive":
        record["status"] = encoding["inactive"]
    elif active == "failed":
        record["status"] = encoding["failed"]

    try:
        entered = int(values.get("ActiveEnterTimestampMonotonic", "0")) / 1_000_000.0
        if entered > 0:
            record["status_time"] = max(0, int(now - entered))
    except ValueError:
        pass

    return record


def read_settings():
    here = os.path.abspath(os.path.dirname(os.path.realpath(__file__)))
    config = configparser.ConfigParser()
    config.read(os.path.join(here, "settings.ini"))

    if not config.has_section("SERVICES"):
        sys.exit("settings.ini is missing or has no [SERVICES] section")

    name = config.get("OUTPUT", "convention", fallback="legacy").strip().lower()
    if name not in ENCODINGS:
        sys.exit(f"unknown convention '{name}', expected one of {sorted(ENCODINGS)}")

    system = config.get("SERVICES", "name", fallback="").split()
    user = config.get("USER_SERVICES", "name", fallback="").split() \
        if config.has_section("USER_SERVICES") else []
    return ENCODINGS[name], system, user


def main():
    os.environ["LC_ALL"] = "C"
    encoding, system_units, user_units = read_settings()
    now = uptime_seconds()

    records = [unit_status(u, encoding, now) for u in system_units]
    records += [unit_status(u, encoding, now, user=True) for u in user_units]
    print(json.dumps(records))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
