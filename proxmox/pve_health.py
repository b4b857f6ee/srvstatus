#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Proxmox functional health: quorum, replication, storages, HA, ZFS pools.

This complements service status monitoring. A running systemd unit does not
tell you that a storage answers, or that a replication job actually succeeds --
pvestatd can be perfectly healthy while replication has failed for weeks.

Emits InfluxDB line protocol (several measurements), consumed by Telegraf via
[[inputs.exec]] with data_format = "influx".

Read-only: authenticates with a PVEAuditor API token, which cannot write.
"""
import json
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request

BASE = "https://localhost:8006/api2/json"
FICHIER_JETON = "/etc/srvstatus/pve-token"
NOEUD = socket.gethostname()

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE   # certificat interne Proxmox, hote = localhost


def jeton():
    with open(FICHIER_JETON, encoding="utf-8") as f:
        return f.read().strip()


def api(chemin, tk):
    req = urllib.request.Request(BASE + chemin,
                                 headers={"Authorization": f"PVEAPIToken={tk}"})
    try:
        with urllib.request.urlopen(req, timeout=15, context=CTX) as r:
            return json.load(r).get("data")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def ech(v):
    """Echappement d'une valeur de tag au protocole de ligne."""
    return (str(v).replace("\\", "\\\\").replace(" ", "\\ ")
            .replace(",", "\\,").replace("=", "\\="))


def ligne(mesure, tags, champs):
    t = ",".join(f"{k}={ech(v)}" for k, v in tags.items() if v not in (None, ""))
    c = ",".join(f"{k}={v}" for k, v in champs.items())
    return f"{mesure},{t} {c}" if t else f"{mesure} {c}"


def main():
    tk = jeton()
    maintenant = int(time.time())
    out = []

    # --- quorum et noeuds ---------------------------------------------------
    d = api("/cluster/status", tk)
    if d:
        grappe = next((x for x in d if x.get("type") == "cluster"), {})
        noeuds = [x for x in d if x.get("type") == "node"]
        out.append(ligne("pve_cluster", {"cluster": grappe.get("name", "pve")}, {
            "quorate": f"{int(grappe.get('quorate', 0))}i",
            "noeuds": f"{len(noeuds)}i",
            "en_ligne": f"{sum(1 for x in noeuds if x.get('online') == 1)}i",
        }))

    # --- replication (jobs dont ce noeud est la source) ---------------------
    d = api(f"/nodes/{NOEUD}/replication", tk)
    for j in d or []:
        dernier = j.get("last_sync") or 0
        prochain = j.get("next_sync") or 0
        out.append(ligne("pve_replication", {
            "job": j.get("id"), "guest": j.get("guest"),
            "source": NOEUD, "target": j.get("target"),
        }, {
            "age_s": f"{max(0, maintenant - int(dernier)) if dernier else -1}i",
            "duree_s": float(j.get("duration") or 0),
            "echecs": f"{int(j.get('fail_count') or 0)}i",
            "en_erreur": f"{1 if j.get('error') else 0}i",
            "prochain_dans_s": f"{max(0, int(prochain) - maintenant) if prochain else -1}i",
        }))

    # --- stockages de ce noeud ----------------------------------------------
    d = api(f"/nodes/{NOEUD}/storage", tk)
    for s in d or []:
        total = int(s.get("total") or 0)
        utilise = int(s.get("used") or 0)
        out.append(ligne("pve_storage", {
            "storage": s.get("storage"), "node": NOEUD, "type": s.get("type"),
        }, {
            "actif": f"{int(s.get('active') or 0)}i",
            "active": f"{int(s.get('enabled') or 0)}i",
            "total_o": f"{total}i",
            "utilise_o": f"{utilise}i",
            "libre_o": f"{int(s.get('avail') or 0)}i",
            "pct_utilise": round(utilise / total * 100, 2) if total else 0.0,
        }))

    # --- haute disponibilite ------------------------------------------------
    d = api("/cluster/ha/status/current", tk)
    for h in d or []:
        etat = str(h.get("state") or h.get("status") or "")
        # Liste NOIRE et non liste blanche : Proxmox produit des libelles ouverts
        # ("master ... (active)", "lrm ... (idle)"), qu'une liste blanche
        # classait a tort en defaut. On ne signale que l'anormal explicite.
        bas = etat.lower()
        sain = 0 if any(m in bas for m in
                        ("error", "fence", "freeze", "unknown", "disabled")) else 1
        out.append(ligne("pve_ha", {
            "sid": h.get("sid") or h.get("id") or h.get("type"),
            "node": h.get("node") or NOEUD, "etat": etat or "inconnu",
        }, {"sain": f"{sain}i"}))

    # --- pools ZFS de ce noeud ----------------------------------------------
    d = api(f"/nodes/{NOEUD}/disks/zfs", tk)
    for p in d or []:
        sante = str(p.get("health") or "")
        out.append(ligne("pve_zfs", {
            "pool": p.get("name"), "node": NOEUD, "sante": sante or "inconnue",
        }, {
            "sain": f"{1 if sante == 'ONLINE' else 0}i",
            "taille_o": f"{int(p.get('size') or 0)}i",
            "libre_o": f"{int(p.get('free') or 0)}i",
            "fragmentation": float(p.get("frag") or 0),
            "erreurs": f"{int(p.get('errors') or 0) if str(p.get('errors','')).isdigit() else 0}i",
        }))

    if not out:
        print("aucune donnee recuperee depuis l'API Proxmox", file=sys.stderr)
        sys.exit(1)
    print("\n".join(out))


if __name__ == "__main__":
    main()
