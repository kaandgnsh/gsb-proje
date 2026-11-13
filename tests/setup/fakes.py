"""Test yardımcıları: gerçek sisteme dokunmayan sahte komut çalıştırıcı."""
from __future__ import annotations

import subprocess
from collections.abc import Mapping


class FakeRunner:
    """Komutları kaydeder; önceden tanımlı yanıtları döner.

    `responses` anahtarı argv'nin bir öneki (tuple) olabilir; en uzun eşleşen kullanılır.
    """

    def __init__(self, responses: Mapping[tuple[str, ...], tuple[int, str]] | None = None,
                 which: Mapping[str, str] | None = None):
        self.responses = dict(responses or {})
        self._which = dict(which or {})
        self.calls: list[list[str]] = []

    def run(self, argv, timeout: float = 10, env=None, cwd=None):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        best = None
        for key, val in self.responses.items():
            if tuple(argv[: len(key)]) == key and (best is None or len(key) > len(best[0])):
                best = (key, val)
        code, out = best[1] if best else (127, "")
        return subprocess.CompletedProcess(argv, code, stdout=out, stderr="")

    def which(self, name: str) -> str | None:
        return self._which.get(name)
