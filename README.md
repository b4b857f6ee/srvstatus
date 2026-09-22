# SRVSTATUS

## version 2.0

## Monitoring SystemD services — and Proxmox health — with Telegraf + InfluxDB + Grafana

![The srvstatus dashboard](https://github.com/b4b857f6ee/srvstatus/blob/master/docs/dashboard-srvstatus.png?raw=true)

*Five hosts, 102 units. Host names are redacted in this screenshot.*

<details>
<summary>Screenshots of the 1.x dashboard</summary>

![1.x dashboard](https://github.com/b4b857f6ee/srvstatus/blob/master/services_grafana.png?raw=true)
![1.x dashboard](https://github.com/b4b857f6ee/srvstatus/blob/master/services_grafana1.png?raw=true)

</details>

Fork of <https://grafana.com/grafana/dashboards/12552> and
<https://github.com/mkopnsrc/srvstatus>.

`service.py` checks a list of SystemD units and reports their status and uptime
as JSON, for the Telegraf `exec` input. Version 2.0 adds `proxmox/pve_health.py`,
which reports the *functional* health of a Proxmox VE cluster.

---

## What changed in 2.0

**No dependencies.** `service.py` used to parse the human-readable output of
`systemctl status` with regular expressions, and needed `parsedatetime` to turn
the `since` date back into a duration. It now queries `systemctl show`, which
returns machine-readable `key=value` fields, and derives uptime from
`ActiveEnterTimestampMonotonic` compared against `/proc/uptime`. No locale
handling, no regular expressions, no virtualenv, nothing to `pip install`.

This matters beyond elegance: `requirements.txt` pinned `configparser==3.5.0`,
a Python 2 backport that **shadows the standard library** when installed under
Python 3. And on hosts where you would rather not add packages at all —
hypervisors, appliances — the old version simply could not be deployed.

**Oneshot units are recognised.** The code tested for `active (existed)`, a typo
for `active (exited)`. Units that complete and exit — a great many system units
do — never matched and were reported as "no match" forever.

**Missing units produce a record.** A unit that does not exist used to yield an
empty `{}`. Telegraf cannot apply `tag_keys` to an empty object, so the whole
batch was silently dropped. Unknown units now report the `unknown` value.

**No more crash on unusual `Active:` lines.** The old code dereferenced a second
regex match without checking it, raising `AttributeError` when only the first
one matched.

**`os.system('clear')` on interrupt is gone.** Wiping the terminal from a
Telegraf plugin was never the intent.

---

## Status encodings

Two encodings exist in the wild, and mixing them silently shows healthy services
as failed. Choose one in `settings.ini`:

| | active | exited | inactive | failed | unknown |
|---|---|---|---|---|---|
| `legacy` *(default)* | 4 | 3 | 2 | 1 | 0 |
| `modern` | 1 | 1 | 3 | 4 | 0 |

`legacy` is what this project has always emitted and what
`dashboards/legacy-systemd-services-status.json` expects — existing
installations keep working with no change.

`modern` puts the healthy value at 1 and reserves higher numbers for problems,
which reads far better in alert rules (*alert when > 1*). The two dashboards
under `dashboards/` use it.

```ini
[OUTPUT]
convention = modern
```

---

## Installation

```sh
cd /opt && git clone https://github.com/b4b857f6ee/srvstatus.git
cd /opt/srvstatus && chmod +x service.py
cp settings.ini.back settings.ini
```

List the units you care about:

```ini
[SERVICES]
name = docker.service nginx.service

# optional, for `systemctl --user` units
[USER_SERVICES]
name = syncthing.service
```

Then copy `telegraf/srvstatus.conf` into `/etc/telegraf/telegraf.d/`.

### If Telegraf already writes to another bucket

This is the one step people lose time on. If the host's existing Telegraf output
writes to a different bucket than the one your dashboard reads — a Proxmox node
writing to `proxmox` while the dashboard reads `linux`, for instance — you need
**two** adjustments:

1. a second output in `telegraf.d/srvstatus.conf`, restricted to this
   measurement with `namepass = ["services_stats"]`;
2. `namedrop = ["services_stats"]` on the pre-existing output in
   `telegraf.conf`.

Without the first, the data never reaches the dashboard's bucket. Without the
second, it is written twice. Neither failure produces an error message.

---

## Proxmox VE

`proxmox/pve_health.py` covers what unit status cannot: `pvestatd` can run
perfectly while a storage is unreachable or a replication job has been failing
for weeks.

| Measurement | Fields | Scope |
|---|---|---|
| `pve_cluster` | quorum, total and online nodes | cluster |
| `pve_replication` | age since last sync, duration, failures, error flag, next run | source node |
| `pve_storage` | active, enabled, total/used/free, percent | node |
| `pve_ha` | state per resource | cluster |
| `pve_zfs` | pool health, size, fragmentation, errors | node |

### Read-only API token

The script talks to `https://localhost:8006`. Create a token that can read and
nothing else, rather than granting Telegraf a path to root through sudoers:

```sh
pveum user add srvstatus@pve --comment "srvstatus monitoring, read-only"
pveum acl modify / --users srvstatus@pve --roles PVEAuditor
pveum user token add srvstatus@pve collector --privsep 0
```

Store `USER@REALM!TOKENID=SECRET` in `/etc/srvstatus/pve-token`, readable by
Telegraf and nobody else:

```sh
install -d -m 750 -o root -g telegraf /etc/srvstatus
# write the token file, then:
chown root:telegraf /etc/srvstatus/pve-token && chmod 640 /etc/srvstatus/pve-token
```

Verify it really is read-only before trusting it — a `POST` must be refused:

```sh
curl -sk -o /dev/null -w '%{http_code}\n' -X POST \
  -H "Authorization: PVEAPIToken=$(cat /etc/srvstatus/pve-token)" \
  https://localhost:8006/api2/json/nodes/$(hostname)/status -d command=reboot
# expected: 403
```

Then deploy `proxmox/pve_health.py` next to `service.py` and copy
`telegraf/pve_health.conf` into `/etc/telegraf/telegraf.d/`.

Cluster-wide measurements are emitted by every node. Deduplicate in your
queries by grouping on the entity and taking the last value — that way the data
survives one node going down.

---

## Dashboards

| File | Encoding | Contents |
|---|---|---|
| `dashboards/srvstatus-systemd-services.json` | `modern` | unit status across hosts — *this is the dashboard in the screenshot above* |
| `dashboards/proxmox-functional-health.json` | — | quorum, replication, storages, ZFS, HA |
| `dashboards/legacy-systemd-services-status.json` | `legacy` | the original 1.x dashboard |

Both new files are exported for sharing: importing them asks which InfluxDB
data source to use.

### Two Flux traps worth knowing

**`pivot` refuses a mixed-type column.** If some fields are integers and others
floats, you get `schema collision detected: column "_value" is both of type
float and int`. The wording suggests a grouping-key problem; it is not. Cast
first:

```flux
|> map(fn: (r) => ({ r with _value: float(v: r._value) }))
```

**Stat panels reduce on the wrong column.** Leftover columns such as `host`
make Grafana display a hostname where you expect a number. End the query with
`|> keep(columns: ["_value"])`.

---

## License

MIT — **Free Software, Hell Yeah!**
