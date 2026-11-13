"""Port denetimi: dinleyen bir süreç varsa ona asla dokunulmaz."""
from __future__ import annotations

import socket
import sys


class PortConflictError(RuntimeError):
    def __init__(self, host: str, port: int, detail: str = ""):
        self.host, self.port = host, port
        super().__init__(
            f"{host}:{port} başka bir süreç tarafından kullanılıyor{': ' + detail if detail else ''}. "
            "Mevcut sürece dokunulmadı. config.toml içindeki [ollama] port değerini değiştirin.")


def port_in_use(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    # 1) bağlanılabiliyorsa biri dinliyor
    with socket.socket(family, socket.SOCK_STREAM) as c:
        c.settimeout(0.5)
        if c.connect_ex((host, port)) == 0:
            return True
    # 2) bağlanılamasa da adres (ör. 0.0.0.0 dinleyicisi) bağlanamaz durumda olabilir
    with socket.socket(family, socket.SOCK_STREAM) as b:
        if sys.platform.startswith("win"):
            b.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            b.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            b.bind((host, port))
        except OSError:
            return True
    return False
