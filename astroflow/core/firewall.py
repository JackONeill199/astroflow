"""Heimnetz-Freigabe in der Linux-Firewall (ufw bzw. firewalld) – Gegenstück zur Windows-Regel „AstroFlow LAN“.

Nur auf ausdrücklichen Klick und nur am Rechner selbst (Endpunkt `/setup/firewall` mit `security.require_local`). Die Befehle sind
fest: einzige Werte darin sind der Port (int, 1–65535) und das lokale Subnetz, das AstroFlow selbst aus `ip -o -4 addr` liest und
per `ipaddress` als privates IPv4-Netz (RFC 1918) prüft – nichts davon kommt aus der Anfrage. Ausgeführt wird per `pkexec` (Polkit fragt nach
dem Passwort). Ohne pkexec, ohne grafische Sitzung oder bei unbekannter Firewall wird der Befehl nur angezeigt.
Regel wie unter Windows: TCP, nur das lokale Subnetz (`LocalSubnet`), Entfernen ebenso.
"""
from __future__ import annotations

import ipaddress
import os
import re
import shlex
import shutil
import socket
import subprocess
from pathlib import Path

SBIN = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"   # ufw/firewall-cmd liegen oft nicht im PATH des Nutzers
UFW_CONF = Path("/etc/ufw/ufw.conf")
COMMENT = "AstroFlow LAN"
HOME_NETS = tuple(ipaddress.IPv4Network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))   # RFC 1918


def _which(name: str) -> str | None:
    return shutil.which(name, path=os.pathsep.join(filter(None, [os.environ.get("PATH"), SBIN])))


def _run(cmd: list[str], timeout: float = 10) -> subprocess.CompletedProcess | None:
    """Kleiner Haken (Tests ersetzen ihn, nichts wird echt ausgeführt)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None


def detect() -> str | None:
    """Aktive Firewall: 'firewalld', 'ufw' oder None. Beides ohne root lesbar (firewall-cmd --state, /etc/ufw/ufw.conf)."""
    fc = _which("firewall-cmd")
    if fc:
        r = _run([fc, "--state"])
        if r is not None and r.returncode == 0 and "running" in (r.stdout or ""):
            return "firewalld"
    if _which("ufw"):
        try:
            if re.search(r"^\s*ENABLED\s*=\s*yes\s*$", UFW_CONF.read_text(encoding="utf-8", errors="replace"), re.M | re.I):
                return "ufw"
        except OSError:
            pass
    return None


def _primary_ip() -> str | None:
    """Adresse, über die der Rechner ins Netz geht (UDP-connect sendet nichts)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))                 # TEST-NET-1, wird nie erreicht
            return s.getsockname()[0]
    except OSError:
        return None


def local_subnet(addr_output: str | None = None, primary: str | None = None) -> ipaddress.IPv4Network | None:
    """Privates IPv4-Subnetz der Netzwerkschnittstelle (z. B. 192.168.178.0/24) oder None."""
    if addr_output is None:
        ip = _which("ip")
        r = _run([ip, "-o", "-4", "addr", "show", "scope", "global"]) if ip else None
        addr_output = r.stdout if r is not None and r.returncode == 0 else ""
    primary = primary if primary is not None else _primary_ip()
    nets = []
    for m in re.finditer(r"\binet\s+(\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})\b", addr_output or ""):
        try:
            iface = ipaddress.IPv4Interface(m.group(1))
        except ValueError:
            continue
        net = iface.network
        if any(iface.ip in r for r in HOME_NETS) and 8 <= net.prefixlen <= 30:
            nets.append((str(iface.ip) == primary, net))
    nets.sort(key=lambda t: not t[0])                  # Schnittstelle mit der Hauptadresse zuerst
    return nets[0][1] if nets else None


def commands(kind: str, net: ipaddress.IPv4Network, port: int, remove: bool = False) -> list[list[str]]:
    """Feste Befehle je Firewall (ohne pkexec). Port und Netz sind geprüfte Werte."""
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError("Ungültiger Port.")
    net = ipaddress.IPv4Network(net)
    if kind == "ufw":
        ufw = _which("ufw") or "/usr/sbin/ufw"
        rule = ["from", str(net), "to", "any", "port", str(port), "proto", "tcp"]
        return [[ufw, "delete", "allow", *rule]] if remove else [[ufw, "allow", *rule, "comment", COMMENT]]
    if kind == "firewalld":
        fc = _which("firewall-cmd") or "/usr/bin/firewall-cmd"
        rich = f'rule family="ipv4" source address="{net}" port port="{port}" protocol="tcp" accept'
        return [[fc, "--permanent", "--remove-rich-rule" if remove else "--add-rich-rule", rich], [fc, "--reload"]]
    raise ValueError("Unbekannte Firewall.")


def shell_line(cmds: list[list[str]]) -> str:
    return " && ".join(shlex.join(c) for c in cmds)


def pkexec_argv(cmds: list[list[str]]) -> list[str] | None:
    """pkexec-Aufruf (ein Passwortdialog auch bei zwei Befehlen) oder None, wenn pkexec bzw. eine grafische Sitzung fehlt."""
    pk = _which("pkexec")
    if not pk or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return None
    if len(cmds) == 1:
        return [pk, *cmds[0]]
    return [pk, "/bin/sh", "-c", shell_line(cmds)]


def plan(port: int, remove: bool = False) -> dict:
    """Was zu tun ist: {kind, net, cmds, argv (pkexec oder None), manual (Befehl zum Anzeigen)} bzw. {kind: None}."""
    kind = detect()
    if not kind:
        return {"kind": None}
    net = local_subnet()
    if not net:
        return {"kind": kind, "net": None}
    cmds = commands(kind, net, port, remove)
    return {"kind": kind, "net": str(net), "cmds": cmds, "argv": pkexec_argv(cmds), "manual": "sudo sh -c " + shlex.quote(shell_line(cmds))
            if len(cmds) > 1 else "sudo " + shlex.join(cmds[0])}


def execute(argv: list[str], timeout: float = 300) -> tuple[bool, str]:
    """pkexec ausführen (wartet auf die Passworteingabe). Rückgabe (ok, feste deutsche Meldung)."""
    r = _run(argv, timeout=timeout)
    if r is None:
        return False, "Die Firewall-Freigabe ließ sich nicht starten."
    if r.returncode == 0:
        return True, ""
    if r.returncode in (126, 127):                     # pkexec: Dialog abgebrochen bzw. nicht berechtigt
        return False, "Abgebrochen – keine Berechtigung erteilt."
    return False, "Die Firewall hat den Befehl abgelehnt (Protokoll des Systems prüfen)."
