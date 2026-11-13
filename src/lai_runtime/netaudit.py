"""Ağ yalıtımı denetimi (Linux): bir süreç ağacının soketlerini /proc üzerinden inceler.

`OLLAMA_NO_CLOUD=1` tek başına kanıt sayılmaz; bu modül süreçlerin gerçekte hangi
adreslerle konuştuğunu gözlemler. İhlal: loopback dışı uzak adres, loopback dışı
arayüzde dinleme, DNS (53) girişimi.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import threading
from dataclasses import dataclass
from pathlib import Path

PROC = Path("/proc")
_TCP_STATES = {"01": "ESTABLISHED", "02": "SYN_SENT", "03": "SYN_RECV", "04": "FIN_WAIT1",
               "05": "FIN_WAIT2", "06": "TIME_WAIT", "07": "CLOSE", "08": "CLOSE_WAIT",
               "09": "LAST_ACK", "0A": "LISTEN", "0B": "CLOSING"}


@dataclass(frozen=True)
class Conn:
    proto: str
    local: tuple[str, int]
    remote: tuple[str, int]
    state: str
    pid: int

    def __str__(self):
        return f"{self.proto} {self.local[0]}:{self.local[1]} -> {self.remote[0]}:{self.remote[1]} " \
               f"{self.state} (pid {self.pid})"


def _ppid(pid_dir: Path) -> int | None:
    try:
        return int(pid_dir.joinpath("stat").read_text().rsplit(")", 1)[1].split()[1])
    except (OSError, IndexError, ValueError):
        return None


def process_tree(root: int, proc: Path = PROC) -> list[int]:
    children: dict[int, list[int]] = {}
    for d in proc.iterdir():
        if d.name.isdigit():
            pp = _ppid(d)
            if pp is not None:
                children.setdefault(pp, []).append(int(d.name))
    out, stack = [], [root]
    while stack:
        p = stack.pop()
        if (proc / str(p)).exists():
            out.append(p)
        stack.extend(children.get(p, []))
    return out


def _decode(hexaddr: str) -> tuple[str, int]:
    addr, port = hexaddr.split(":")
    raw = bytes.fromhex(addr)
    if len(raw) == 4:
        ip = socket.inet_ntop(socket.AF_INET, raw[::-1])
    else:  # /proc: 4 adet 32 bitlik sözcük, her biri küçük-sonlu
        ip = socket.inet_ntop(socket.AF_INET6, b"".join(raw[i:i + 4][::-1] for i in range(0, 16, 4)))
    return ip, int(port, 16)


def _socket_inodes(pid_dir: Path) -> set[str]:
    out = set()
    try:
        for fd in (pid_dir / "fd").iterdir():
            try:
                t = os.readlink(fd)
            except OSError:
                continue
            if t.startswith("socket:["):
                out.add(t[8:-1])
    except OSError:
        pass
    return out


def sockets_of(pids: list[int], proc: Path = PROC) -> list[Conn]:
    conns: list[Conn] = []
    for pid in pids:
        d = proc / str(pid)
        inodes = _socket_inodes(d)
        if not inodes:
            continue
        for proto in ("tcp", "tcp6", "udp", "udp6"):
            try:
                lines = (d / "net" / proto).read_text().splitlines()[1:]
            except OSError:
                continue
            for line in lines:
                f = line.split()
                if len(f) < 10 or f[9] not in inodes:
                    continue
                state = _TCP_STATES.get(f[3], f[3]) if proto.startswith("tcp") else "UDP"
                conns.append(Conn(proto, _decode(f[1]), _decode(f[2]), state, pid))
    return conns


def _is_loop(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return a.is_loopback


def _is_unspec(ip: str) -> bool:
    return ipaddress.ip_address(ip).is_unspecified


def violations(conns: list[Conn]) -> list[str]:
    out = []
    for c in conns:
        rip, rport = c.remote
        if c.state == "LISTEN" or (c.proto.startswith("udp") and _is_unspec(rip)):
            if not _is_loop(c.local[0]):
                out.append(f"loopback dışı arayüzde dinleme: {c}")
            continue
        if _is_unspec(rip) and rport == 0:
            continue
        if not _is_loop(rip):
            kind = "DNS sorgusu girişimi" if rport == 53 else "dış bağlantı"
            out.append(f"{kind}: {c}")
    return sorted(set(out))


class NetworkMonitor:
    """Bağlam yöneticisi: süreç ağacını arka planda örnekler, görülen tüm soketleri toplar."""

    def __init__(self, pid: int, interval: float = 0.05, proc: Path = PROC):
        self.pid, self.interval, self.proc = pid, interval, proc
        self.seen: set[Conn] = set()
        self.samples = 0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            self.sample()
            self._stop.wait(self.interval)

    def sample(self) -> None:
        self.seen.update(sockets_of(process_tree(self.pid, self.proc), self.proc))
        self.samples += 1

    def violations(self) -> list[str]:
        return violations(list(self.seen))

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join()
        self.sample()
