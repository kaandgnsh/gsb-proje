"""Salt okunur ortam tespiti. Hiçbir sistem durumunu değiştirmez."""
from __future__ import annotations

import json
import os
import platform as _platform
import re
import shutil
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .paths import AppPaths
from .runner import CommandRunner

SYSTEM_OLLAMA_PORT = 11434
APP_OLLAMA_PORT = 11435
INTERESTING_CPU_FLAGS = ("avx", "avx2", "avx512f", "f16c", "fma", "sse4_2", "neon", "asimd")
PCI_VENDORS = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel"}

HttpGet = Callable[[str, float], dict]


@dataclass
class GpuInfo:
    vendor: str
    name: str = ""
    accel: str = ""  # cuda | rocm | vulkan | "" (bilinmiyor)


@dataclass
class OllamaInfo:
    binary: str | None = None
    version: str | None = None
    system_service: bool = False
    listening_11434: bool = False
    listening_11435: bool = False
    wildcard_11435: bool = False
    models_11434: list[str] = field(default_factory=list)


@dataclass
class SystemInfo:
    os: str
    distro_id: str = ""
    distro_like: list[str] = field(default_factory=list)
    distro_version: str = ""
    arch: str = ""
    mem_total_mb: int = 0
    disk_free_mb: int = 0
    cpu_model: str = ""
    cpu_cores: int = 0
    cpu_flags: list[str] = field(default_factory=list)
    gpus: list[GpuInfo] = field(default_factory=list)
    python_version: str = ""
    ollama: OllamaInfo = field(default_factory=OllamaInfo)
    existing_install: bool = False

    @classmethod
    def empty(cls, **kw) -> SystemInfo:
        return cls(**kw)

    def to_dict(self) -> dict:
        return asdict(self)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _parse_os_release(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _cpu(fs_root: Path) -> tuple[str, int, list[str]]:
    text = _read(fs_root / "proc/cpuinfo")
    model, cores, flags = "", 0, set()
    for line in text.splitlines():
        key, _, val = line.partition(":")
        key = key.strip()
        if key == "processor":
            cores += 1
        elif key in ("model name", "Model", "Hardware") and not model:
            model = val.strip()
        elif key in ("flags", "Features"):
            flags.update(val.split())
    if cores == 0:
        cores = os.cpu_count() or 0
    return model, cores, sorted(f for f in INTERESTING_CPU_FLAGS if f in flags)


def _mem_mb(fs_root: Path) -> int:
    m = re.search(r"^MemTotal:\s+(\d+)\s*kB", _read(fs_root / "proc/meminfo"), re.M)
    return int(m.group(1)) // 1024 if m else 0


def _gpus(fs_root: Path, runner: CommandRunner) -> list[GpuInfo]:
    found: dict[str, GpuInfo] = {}
    drm = fs_root / "sys/class/drm"
    if drm.is_dir():
        for card in sorted(drm.glob("card*")):
            vendor = PCI_VENDORS.get(_read(card / "device/vendor").strip().lower())
            if vendor and vendor not in found:
                found[vendor] = GpuInfo(vendor=vendor)
    if "nvidia" in found and runner.which("nvidia-smi"):
        found["nvidia"].accel = "cuda"
    if "amd" in found and (fs_root / "dev/kfd").exists():
        found["amd"].accel = "rocm"
    return [found[k] for k in sorted(found)]


def _listeners(fs_root: Path) -> dict[int, set[str]]:
    """port -> dinleyen yerel adresler (/proc/net/tcp{,6}, durum 0A)."""
    out: dict[int, set[str]] = {}
    for name in ("tcp", "tcp6"):
        for line in _read(fs_root / "proc/net" / name).splitlines()[1:]:
            parts = line.split()
            if len(parts) < 4 or parts[3] != "0A":
                continue
            addr, _, port = parts[1].rpartition(":")
            out.setdefault(int(port, 16), set()).add(addr)
    return out


def _is_wildcard(addr: str) -> bool:
    return set(addr) == {"0"}


def _disk_free_mb(path: Path) -> int:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).free // (1024 * 1024)
    except OSError:
        return 0


def _default_http_get(url: str, timeout: float) -> dict:
    # Yalnız loopback; proxy ayarları devre dışı.
    if not url.startswith(("http://127.0.0.1:", "http://[::1]:")):
        raise ValueError("yalnız loopback adreslerine izin verilir")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _ollama(runner: CommandRunner, fs_root: Path, platform: str,
            http_get: HttpGet) -> OllamaInfo:
    info = OllamaInfo()
    info.binary = runner.which("ollama")
    if info.binary:
        r = runner.run([info.binary, "--version"], timeout=10)
        m = re.search(r"(\d+\.\d+\.\d+[\w.\-]*)", (r.stdout or "") + (r.stderr or ""))
        info.version = m.group(1) if r.returncode == 0 and m else None
    if platform == "linux" and runner.which("systemctl"):
        r = runner.run(["systemctl", "is-enabled", "ollama.service"], timeout=5)
        info.system_service = r.returncode == 0 or (r.stdout or "").strip() in (
            "enabled", "disabled", "static", "indirect")
    listeners = _listeners(fs_root) if platform == "linux" else {}
    info.listening_11434 = SYSTEM_OLLAMA_PORT in listeners
    info.listening_11435 = APP_OLLAMA_PORT in listeners
    info.wildcard_11435 = any(_is_wildcard(a) for a in listeners.get(APP_OLLAMA_PORT, ()))
    try:
        tags = http_get(f"http://127.0.0.1:{SYSTEM_OLLAMA_PORT}/api/tags", 2.0)
        info.models_11434 = sorted(m["name"] for m in tags.get("models", []) if "name" in m)
    except Exception:  # erişilemiyorsa yalnız boş liste: tespit asla başarısız olmaz
        info.models_11434 = []
    return info


def detect_system(runner: CommandRunner, *, paths: AppPaths, fs_root: Path = Path("/"),
                  platform: str | None = None, http_get: HttpGet | None = None) -> SystemInfo:
    platform = platform or ("windows" if sys.platform.startswith("win") else "linux")
    http_get = http_get or _default_http_get
    info = SystemInfo(os=platform)
    info.arch = _platform.machine().lower()
    info.python_version = _platform.python_version()
    if platform == "linux":
        rel = _parse_os_release(_read(fs_root / "etc/os-release"))
        info.distro_id = rel.get("ID", "")
        info.distro_like = rel.get("ID_LIKE", "").split()
        info.distro_version = rel.get("VERSION_ID", rel.get("BUILD_ID", ""))
        info.mem_total_mb = _mem_mb(fs_root)
        info.cpu_model, info.cpu_cores, info.cpu_flags = _cpu(fs_root)
        info.gpus = _gpus(fs_root, runner)
    else:
        info.distro_id = "windows"
        info.distro_version = _platform.version()
        info.cpu_model = _platform.processor()
        info.cpu_cores = os.cpu_count() or 0
        info.mem_total_mb = _windows_mem_mb()
        info.gpus = _windows_gpus(runner)
    info.disk_free_mb = _disk_free_mb(paths.root)
    info.ollama = _ollama(runner, fs_root, platform, http_get)
    info.existing_install = paths.state_file.exists()
    return info


def _windows_mem_mb() -> int:
    try:
        import ctypes

        class MEMSTAT(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        s = MEMSTAT()
        s.dwLength = ctypes.sizeof(MEMSTAT)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))  # type: ignore[attr-defined]
        return s.ullTotalPhys // (1024 * 1024)
    except Exception:
        return 0


def _windows_gpus(runner: CommandRunner) -> list[GpuInfo]:
    r = runner.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }"], timeout=15)
    out = []
    for name in (r.stdout or "").splitlines():
        n = name.strip()
        low = n.lower()
        vendor = ("nvidia" if "nvidia" in low else "amd" if ("amd" in low or "radeon" in low)
                  else "intel" if "intel" in low else "")
        if n and vendor:
            out.append(GpuInfo(vendor=vendor, name=n,
                               accel="cuda" if vendor == "nvidia" and runner.which("nvidia-smi") else ""))
    return out
