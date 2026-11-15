"""Modeli kullanan CLI işlemleri için yalıtım kapısı ve uygulamanın kendi model süreci."""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path


class NotIsolated(RuntimeError):
    """Yalıtım doğrulanmadı; modele hiçbir veri gönderilmedi."""


@contextmanager
def model_runtime(cfg):
    """Yalıtım doğrulanmadan modele dokunulmaz.

    Gerçek sınırın içinde (`LAI_SANDBOX_ACTIVE`) uygulamanın kendi süreci başlatılır ya da
    çalışan kendi süreci yeniden kullanılır; yalnız bu blokta başlatılan süreç kapatılır.
    """
    from lai_runtime.guard import MESSAGE, isolated

    if not isolated():
        raise NotIsolated(MESSAGE)
    if not os.environ.get("LAI_SANDBOX_ACTIVE"):
        yield
        return
    from lai_runtime.process import RuntimeManager
    from lai_setup.paths import resolve_paths

    paths = resolve_paths("linux", os.environ, Path.home())
    paths.runtime.mkdir(parents=True, exist_ok=True)
    mgr = RuntimeManager(cfg, paths)
    already = mgr.status().running
    mgr.start()
    try:
        yield
    finally:
        if not already:
            mgr.stop()
