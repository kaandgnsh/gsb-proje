"""İzole Ollama süreci için temiz ortam değişkenleri.

Üst süreçten yalnız beyaz listedeki değişkenler geçer. Kullanıcının OLLAMA_* ayarları ve
proxy değişkenleri asla geçmez; böylece sistem servisinin yapılandırması sızmaz.
"""
from __future__ import annotations

from collections.abc import Mapping

from .config import RuntimeConfig

_PASS_POSIX = ("PATH", "LANG", "LC_ALL", "TZ", "LD_LIBRARY_PATH", "CUDA_VISIBLE_DEVICES",
               "XDG_RUNTIME_DIR")
_PASS_WINDOWS = ("PATH", "SystemRoot", "SystemDrive", "windir", "ComSpec", "PATHEXT", "TEMP",
                 "TMP", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "CUDA_VISIBLE_DEVICES",
                 "LOCALAPPDATA")
_GPU_HIDE = ("CUDA_VISIBLE_DEVICES", "HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES",
             "GGML_VK_VISIBLE_DEVICES")


def build_env(cfg: RuntimeConfig, paths, base_env: Mapping[str, str], platform: str,
              owner: str) -> dict[str, str]:
    allow = _PASS_WINDOWS if platform == "windows" else _PASS_POSIX
    env = {k: base_env[k] for k in allow if k in base_env}
    home = str(paths.runtime / "home")
    env["USERPROFILE" if platform == "windows" else "HOME"] = home
    env.update(
        OLLAMA_HOST=cfg.address,
        OLLAMA_MODELS=str(paths.models),
        OLLAMA_NO_CLOUD="1",
        OLLAMA_ORIGINS="",
        OLLAMA_KEEP_ALIVE=cfg.keep_alive,
        LAI_RUNTIME_OWNER=owner,
    )
    if cfg.accel == "cpu":
        for k in _GPU_HIDE:
            env[k] = ""
    return env
