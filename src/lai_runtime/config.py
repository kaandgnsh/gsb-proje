"""Çalışma ortamı ayarları (config.toml içindeki [ollama] bölümü)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from typing import Literal

LOOPBACK_HOSTS = ("127.0.0.1", "::1")
SYSTEM_OLLAMA_PORT = 11434


@dataclass(frozen=True)
class RuntimeConfig:
    host: str = "127.0.0.1"
    port: int = 11435
    model: str = "qwen3.5:4b"
    accel: Literal["auto", "cpu"] = "auto"
    keep_alive: str = "5m"
    binary: str | None = None

    def __post_init__(self):
        if self.host not in LOOPBACK_HOSTS:
            raise ValueError(f"ollama.host yalnız loopback olabilir (127.0.0.1 / ::1), verilen: {self.host}")
        if self.port == SYSTEM_OLLAMA_PORT:
            raise ValueError("11434 sistem Ollama servisinin portudur; uygulama onu kullanamaz")
        if not 1024 <= int(self.port) <= 65535:
            raise ValueError(f"geçersiz port: {self.port}")
        if self.accel not in ("auto", "cpu"):
            raise ValueError(f"ollama.accel 'auto' ya da 'cpu' olmalı, verilen: {self.accel}")

    @property
    def address(self) -> str:
        return f"[{self.host}]:{self.port}" if ":" in self.host else f"{self.host}:{self.port}"

    @property
    def base_url(self) -> str:
        return f"http://{self.address}"


def load_runtime_config(paths) -> RuntimeConfig:
    """`paths.config_file` okunur; dosya yoksa varsayılanlar."""
    cfg_file = paths.config_file
    if not cfg_file.exists():
        return RuntimeConfig()
    data = tomllib.loads(cfg_file.read_text(encoding="utf-8")).get("ollama", {})
    if data.get("host") == "localhost":
        data["host"] = "127.0.0.1"
    known = {f.name for f in fields(RuntimeConfig)}
    return RuntimeConfig(**{k: v for k, v in data.items() if k in known})
