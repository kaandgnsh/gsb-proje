"""Dış dünya da izole: yalnız laboratuvar lo adresleri, geçici mount/net/PID ns.

Bu dosya unshare -Urnmfp --mount-proc altında çalıştırılır. Sistem ayarı değişmez.
"""

import argparse
import json
import socket
import socketserver
import subprocess
import sys
import threading
from pathlib import Path

from probe import attempts

from lai_runtime.sandbox import launch


def run(*args):
    subprocess.run(args, check=True, capture_output=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("work", type=Path)
    a = p.parse_args()
    work = a.work
    run("mount", "--make-rprivate", "/")
    run("mount", "-t", "tmpfs", "tmpfs", "/run")
    nss = work / "nsswitch.conf"
    nss.write_text("hosts: resolve [!UNAVAIL=return] dns\n")
    run("mount", "--bind", str(nss), "/etc/nsswitch.conf")
    run("ip", "link", "set", "lo", "up")
    run("ip", "addr", "add", "198.18.0.1/32", "dev", "lo")
    run("ip", "-6", "addr", "add", "2001:db8::1/128", "dev", "lo", "nodad")
    hits = []
    servers = []
    targets = {}

    class TCP(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(4096)
            hits.append(self.server.label)
            self.request.sendall(b"ACK")

    class UDP(socketserver.BaseRequestHandler):
        def handle(self):
            _data, sock = self.request
            hits.append(self.server.label)
            sock.sendto(b"ACK", self.client_address)

    def server(label, family, kind, address, handler=None):
        base = (
            socketserver.TCPServer
            if kind == socket.SOCK_STREAM
            else socketserver.UDPServer
        )
        cls = type(
            "Server",
            (socketserver.ThreadingMixIn, base),
            {
                "address_family": family,
                "daemon_threads": True,
                "allow_reuse_address": True,
            },
        )
        s = cls(address, handler or (TCP if kind == socket.SOCK_STREAM else UDP))
        s.label = label
        servers.append(s)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        targets[label] = {
            "family": {
                socket.AF_INET: "AF_INET",
                socket.AF_INET6: "AF_INET6",
                socket.AF_UNIX: "AF_UNIX",
            }[family],
            "kind": "SOCK_STREAM" if kind == socket.SOCK_STREAM else "SOCK_DGRAM",
            "address": list(s.server_address[:2])
            if family != socket.AF_UNIX
            else address,
        }
        return s

    for family, address, suffix in [
        (socket.AF_INET, "198.18.0.1", "4"),
        (socket.AF_INET6, "2001:db8::1", "6"),
    ]:
        server("tcp" + suffix, family, socket.SOCK_STREAM, (address, 0))
        server("udp" + suffix, family, socket.SOCK_DGRAM, (address, 0))
        server("dns_tcp" + suffix, family, socket.SOCK_STREAM, (address, 53))
        server("dns_udp" + suffix, family, socket.SOCK_DGRAM, (address, 53))
    collector = tuple(targets["udp4"]["address"])

    class Relay(socketserver.BaseRequestHandler):
        def handle(self):
            data = self.request.recv(4096)
            hits.append(self.server.label)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as forward:
                forward.settimeout(1)
                forward.sendto(data, collector)
                forward.recv(16)
            if self.server.label == "resolved_varlink":
                reply = {
                    "parameters": {
                        "addresses": [
                            {"ifindex": 1, "family": 2, "address": [198, 18, 0, 1]}
                        ],
                        "name": "sentetik-m11.invalid",
                        "flags": 0,
                    }
                }
                self.request.sendall(json.dumps(reply).encode() + b"\0")
            else:
                self.request.sendall(b"ACK")

    server(
        "host_loopback_proxy",
        socket.AF_INET,
        socket.SOCK_STREAM,
        ("127.0.0.1", 0),
        Relay,
    )
    server(
        "unix_proxy",
        socket.AF_UNIX,
        socket.SOCK_STREAM,
        str(work / "proxy.sock"),
        Relay,
    )
    server(
        "abstract_unix_proxy",
        socket.AF_UNIX,
        socket.SOCK_STREAM,
        "\0lai-m11-lab",
        Relay,
    )
    resolved = Path("/run/systemd/resolve")
    resolved.mkdir(parents=True)
    server(
        "resolved_varlink",
        socket.AF_UNIX,
        socket.SOCK_STREAM,
        str(resolved / "io.systemd.Resolve"),
        Relay,
    )
    # Özel protokol soketi doğrudan probe'a ACK vermez; glibc ile ayrıca doğrulanır.
    targets.pop("resolved_varlink")
    cfg = work / "targets.json"
    cfg.write_text(json.dumps({"targets": targets}))
    baseline = attempts({"targets": targets})
    assert all(baseline.values()), baseline
    count = len(hits)
    command = [sys.executable, str(Path(__file__).with_name("probe.py")), str(cfg)]
    result = launch(
        command, writable=[work], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr
    protected = json.loads(result.stdout)
    assert not any(protected.values()), protected
    assert len(hits) == count, (hits, count)
    # Aynı koruma exec/fork alt sürecinde kalır.
    nested = launch(
        [
            sys.executable,
            "-c",
            "import subprocess,sys; raise SystemExit(subprocess.call(sys.argv[1:]))",
            *command,
        ],
        writable=[work],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert nested.returncode == 0, nested.stderr
    child = json.loads(nested.stdout)
    assert not any(child.values()) and len(hits) == count
    runtime = launch(
        [
            sys.executable,
            str(Path(__file__).with_name("runtime_check.py")),
            str(work),
            str(cfg),
        ],
        writable=[work],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert runtime.returncode == 0, runtime.stderr + runtime.stdout
    runtime = json.loads(runtime.stdout)
    assert len(hits) == count
    # M2 açığına pozitif kontrol: yalnız netns Unix relay'i kapatmaz.
    partial = subprocess.run(
        ["unshare", "-Urn", *command],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    partial = json.loads(partial.stdout)
    assert partial["unix_proxy"] and partial["glibc_nss_resolve"], partial
    output = {
        "synthetic_only": True,
        "public_network_used": False,
        "baseline": baseline,
        "protected": protected,
        "protected_child": child,
        "protected_ollama": runtime,
        "namespace_only": partial,
        "baseline_hits": count,
        "protected_hits": 0,
        "outer_interfaces": Path("/proc/net/dev").read_text(),
    }
    (work / "network-proof.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2)
    )
    print(json.dumps(output, ensure_ascii=False))
    for s in servers:
        s.shutdown()
        s.server_close()


if __name__ == "__main__":
    main()
