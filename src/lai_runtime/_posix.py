"""Linux süreç işlemleri: PDEATHSIG, /proc ile kimlik doğrulama, süreç grubu sinyalleri."""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

PR_SET_PDEATHSIG = 1


def _set_pdeathsig():  # fork sonrası, exec öncesi çocukta çalışır
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)


class PosixOps:
    name = "posix"

    def spawn(self, argv: list[str], env: dict, log_file) -> subprocess.Popen:
        # PDEATHSIG, çocuğu oluşturan iş parçacığı bitince tetiklenir; yalnız ana iş
        # parçacığında güvenlidir. Diğer durumda uygulama kapanışta stop() çağırır.
        main = threading.current_thread() is threading.main_thread()
        return subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=log_file,
                                stderr=subprocess.STDOUT, start_new_session=True,
                                preexec_fn=_set_pdeathsig if main else None, close_fds=True)

    def _stat(self, pid: int) -> list[str] | None:
        try:
            return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            return None

    def alive(self, pid: int) -> bool:
        st = self._stat(pid)
        return st is not None and st[0] not in ("Z", "X")

    def start_id(self, pid: int) -> str | None:
        st = self._stat(pid)
        return st[19] if st else None  # alan 22: starttime (")" sonrası 20. öğe)

    def owner_matches(self, pid: int, owner: str, binary: str) -> bool:
        try:
            raw = Path(f"/proc/{pid}/environ").read_bytes()
        except OSError:
            return False
        return f"LAI_RUNTIME_OWNER={owner}".encode() in raw.split(b"\0")

    def terminate(self, pid: int, timeout: float) -> None:
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            return
        # start_new_session ile pgid == pid; yalnız kendi grubumuzu sinyalleriz
        target = (lambda s: os.killpg(pgid, s)) if pgid == pid else (lambda s: os.kill(pid, s))
        try:
            target(signal.SIGTERM)
        except ProcessLookupError:
            return
        end = time.time() + timeout
        while time.time() < end:
            if not self.alive(pid):
                return
            time.sleep(0.05)
        try:
            target(signal.SIGKILL)
        except ProcessLookupError:
            pass
