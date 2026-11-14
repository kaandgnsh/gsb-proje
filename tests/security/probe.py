"""Kontrollü hedeflere veri + ACK denemesi; gerçek internet adresi yok."""

import json
import socket
import sys


def attempts(config):
    results = {}
    for name, spec in config["targets"].items():
        try:
            family = getattr(socket, spec["family"])
            kind = getattr(socket, spec["kind"])
            with socket.socket(family, kind) as s:
                s.settimeout(0.35)
                address = spec["address"]
                if isinstance(address, list):
                    address = tuple(address)
                s.connect(address)
                s.sendall(b"SENTETIK-M11")
                results[name] = s.recv(16).startswith(b"ACK")
        except OSError:
            results[name] = False
    try:
        results["glibc_nss_resolve"] = (
            socket.getaddrinfo("sentetik-m11.invalid", None, socket.AF_INET)[0][4][0]
            == "198.18.0.1"
        )
    except OSError:
        results["glibc_nss_resolve"] = False
    return results


if __name__ == "__main__":
    print(
        json.dumps(
            attempts(json.loads(__import__("pathlib").Path(sys.argv[1]).read_text()))
        )
    )
