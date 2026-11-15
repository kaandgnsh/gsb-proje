"""Model erişim kapısı: yerel yapay zekâ yalnız doğrulanmış ağ yalıtımı içinde çalışır.

Ortam bayrağı tek başına kanıt sayılmaz; `sandbox.require_isolation` çekirdekten ağ ad
alanını, yalnız loopback'i, seccomp'u, no_new_privs'i ve Unix soket reddini denetler.
Seccomp ve ad alanı süreç içinde geri alınamadığı için doğrulanan sonuç önbelleğe alınır.
Yalıtımsız çalıştırma seçeneği yoktur; tek istisna kurulum aracının açık izinli model
indirmesidir (bkz. `OllamaClient(download_only=True)`).
"""
from __future__ import annotations

import os
from functools import cache

MESSAGE = (
    "Yerel yapay zekâ yalnız ağ yalıtımı içinde çalışır; bu süreçte yalıtım doğrulanamadı, "
    "modele hiçbir veri gönderilmedi. Modeli uygulamadan ya da "
    "`python -m lai_runtime.sandbox --write-dir DİZİN -- KOMUT` ile kullanın."
)


@cache
def _verified() -> bool:
    if not os.environ.get("LAI_SANDBOX_ACTIVE"):
        return False
    try:
        from .sandbox import require_isolation

        require_isolation()
    except Exception:  # noqa: BLE001 — her başarısızlık "yalıtım yok" demektir
        return False
    return True


def isolated() -> bool:
    return _verified()


def require_model_isolation() -> None:
    if not isolated():
        from .client import ModelIsolationError

        raise ModelIsolationError(MESSAGE)
