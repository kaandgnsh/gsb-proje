"""Uygulamaya ait izole Ollama sürecinin yaşam döngüsü.

Kurallar:
- Başlatmadan hemen önce port yeniden denetlenir; dolu ve sahibi biz değilsek PortConflictError.
- Yalnız kendi başlattığımız süreç durdurulur: pid + başlangıç zamanı + sahiplik kimliği.
- Sistemdeki Ollama servisine (11434) hiçbir koşulda sinyal gönderilmez.
- Model süreci yalnız doğrulanmış ağ yalıtımında başlar (`guard`). `download_only=True`
  yalnız kurulumun açık izinli indirmesi içindir: ayrı kayıt dosyası kullanır ve
  istemcisi yalnız `/api/pull` gönderebilir.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .client import OllamaClient, OllamaError
from .config import RuntimeConfig
from .env import build_env
from .guard import require_model_isolation
from .ports import PortConflictError, port_in_use


class RuntimeStartError(RuntimeError):
    pass


@dataclass
class RuntimeStatus:
    running: bool
    pid: int | None = None
    port: int | None = None
    owned: bool = False
    version: str | None = None


def select_ops(platform: str):
    if platform == "windows":
        from ._windows import WindowsOps
        return WindowsOps()
    from ._posix import PosixOps
    return PosixOps()


class RuntimeManager:
    def __init__(self, cfg: RuntimeConfig, paths, *, platform: str | None = None,
                 binary: str | None = None, base_env: dict | None = None,
                 download_only: bool = False):
        self.cfg, self.paths = cfg, paths
        self.download_only = download_only
        self.platform = platform or ("windows" if sys.platform.startswith("win") else "linux")
        self.ops = select_ops(self.platform)
        self._binary = binary or cfg.binary
        self.base_env = dict(os.environ if base_env is None else base_env)
        name = "ollama-download" if download_only else "ollama"
        self.record_file: Path = paths.runtime / f"{name}-process.json"
        self.log_file: Path = paths.runtime / f"{name}.log"
        self._proc = None

    @property
    def base_url(self) -> str:
        return self.cfg.base_url

    def client(self, timeout: float = 600) -> OllamaClient:
        return OllamaClient(self.base_url, timeout=timeout, download_only=self.download_only)

    def resolve_binary(self) -> str | None:
        exe = "ollama.exe" if self.platform == "windows" else "ollama"
        for cand in (self._binary, self.paths.runtime / "bin" / exe):
            if cand and Path(cand).is_file():
                return str(cand)
        return shutil.which("ollama", path=self.base_env.get("PATH"))

    # -- sahiplik kaydı -----------------------------------------------------------------------
    def _load(self) -> dict | None:
        try:
            return json.loads(self.record_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _owned(self, rec: dict | None) -> bool:
        if not rec or not isinstance(rec.get("pid"), int):
            return False
        pid = rec["pid"]
        return (self.ops.alive(pid) and self.ops.start_id(pid) == rec.get("start_id")
                and self.ops.owner_matches(pid, rec.get("owner", ""), rec.get("binary", "")))

    def _clear(self) -> None:
        try:
            self.record_file.unlink()
        except FileNotFoundError:
            pass

    # -- yaşam döngüsü ------------------------------------------------------------------------
    def status(self) -> RuntimeStatus:
        rec = self._load()
        if not self._owned(rec):
            return RuntimeStatus(False, port=self.cfg.port)
        try:
            ver = self.client(timeout=3).version()
        except OllamaError:
            ver = None
        return RuntimeStatus(True, rec["pid"], rec["port"], True, ver)

    def start(self, timeout: float = 60) -> RuntimeStatus:
        if not self.download_only:
            require_model_isolation()
        rec = self._load()
        if self._owned(rec):
            if rec.get("port") == self.cfg.port and self.status().version:
                return self.status()
            # bizim sürecimiz ama başka portta ya da yanıt vermiyor: sahipsiz bırakmadan kapat
            if not self.stop():
                raise RuntimeStartError(
                    f"uygulamanın önceki Ollama süreci (pid {rec['pid']}) kapatılamadı; "
                    "yeni süreç başlatılmadı")
        elif rec:
            self._clear()  # bayat ya da yabancı kayıt: sürece dokunmadan sil

        if port_in_use(self.cfg.host, self.cfg.port):
            raise PortConflictError(self.cfg.host, self.cfg.port, "sahibi bu uygulama değil")
        binary = self.resolve_binary()
        if not binary:
            raise RuntimeStartError("ollama çalıştırılabilir dosyası bulunamadı; "
                                    "kurulum ile çalışma ortamını kurun (install / repair)")
        for d in (self.paths.runtime / "home", self.paths.models):
            d.mkdir(parents=True, exist_ok=True)
        owner = uuid.uuid4().hex
        env = build_env(self.cfg, self.paths, self.base_env, self.platform, owner)
        log_start = self.log_file.stat().st_size if self.log_file.exists() else 0
        with open(self.log_file, "ab") as log:
            self._proc = self.ops.spawn([binary, "serve"], env, log)
        pid = self._proc.pid
        self.record_file.write_text(json.dumps({
            "pid": pid, "start_id": self.ops.start_id(pid), "port": self.cfg.port,
            "owner": owner, "binary": binary}), encoding="utf-8")

        client = self.client(timeout=2)
        end = time.time() + timeout
        while time.time() < end:
            if self._proc.poll() is not None:
                self._clear()
                tail = self._log_tail(log_start)
                if "address already in use" in tail or "Only one usage of each socket" in tail:
                    raise PortConflictError(self.cfg.host, self.cfg.port, "başlatma sırasında kapıldı")
                raise RuntimeStartError(f"ollama başlatılamadı (çıkış {self._proc.returncode}): {tail}")
            try:
                ver = client.version()
                return RuntimeStatus(True, pid, self.cfg.port, True, ver)
            except OllamaError:
                time.sleep(0.1)
        self.stop()
        raise RuntimeStartError(f"ollama {timeout:.0f} sn içinde yanıt vermedi: {self._log_tail(log_start)}")

    def stop(self, timeout: float = 15) -> bool:
        rec = self._load()
        if not rec:
            return False
        if not self._owned(rec):
            self._clear()
            return False
        try:
            self.ops.terminate(rec["pid"], timeout)
        except PermissionError:
            pass
        if self.ops.alive(rec["pid"]):
            return False  # kapatılamadı: kayıt korunur, süreç sahipsiz bırakılmaz
        if self._proc is not None and self._proc.pid == rec["pid"]:
            try:
                self._proc.wait(timeout=5)
            except Exception:
                pass
            self._proc = None
        self._clear()
        return True

    def restart(self, timeout: float = 60) -> RuntimeStatus:
        self.stop()
        return self.start(timeout)

    def _log_tail(self, offset: int, limit: int = 2000) -> str:
        try:
            with open(self.log_file, "rb") as f:
                f.seek(offset)
                return f.read().decode("utf-8", "replace")[-limit:].strip()
        except OSError:
            return ""
