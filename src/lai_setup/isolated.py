"""Modeli çalıştıran kurulum işlemleri (doğrulama, içe aktarma, listeleme) ağ yalıtımında.

Kurulum aracı yalıtımsız çalışır; model süreci ise asla. Bu modül işlemi bubblewrap
sınırı içinde ayrı bir Python sürecinde yürütür: modelin kendi Ollama süreci orada,
yalnız loopback bulunan ağ ad alanında başlar ve işlem bitince kapanır. Sonuç tek satır
JSON olarak döner; ilerleme stderr'e akar.

    python -m lai_setup.isolated '<json>'   # yalnız sınır içinden çağrılır
"""
from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from pathlib import Path

from lai_runtime.client import ModelIsolationError, ModelNotFoundError, OllamaError
from lai_runtime.models import ModelSourceError

from .paths import AppPaths

_ERRORS = {
    "ModelNotFoundError": ModelNotFoundError,
    "ModelIsolationError": ModelIsolationError,
    "ModelSourceError": ModelSourceError,
    "OllamaError": OllamaError,
}


def _paths_dict(paths: AppPaths) -> dict:
    return {f.name: str(getattr(paths, f.name)) for f in dataclasses.fields(AppPaths)}


def _paths(data: dict) -> AppPaths:
    return AppPaths(**{k: Path(v) for k, v in data.items()})


def _source(params: dict, model: str):
    from lai_runtime.models import find_source_model, source_from_gguf

    from .m2_steps import offline_model_source

    if params.get("offline_dir"):
        return offline_model_source(Path(params["offline_dir"]), model)
    if params.get("gguf"):
        return source_from_gguf(Path(params["gguf"]), params["target"])
    return find_source_model(Path(params["dir"]), params["name"])


def perform(op: str, paths: AppPaths, params: dict) -> dict:
    """Sınır içinde çalışır; modeli kullanan her çağrı `guard` tarafından ayrıca denetlenir."""
    from dataclasses import replace

    from lai_runtime.config import load_runtime_config
    from lai_runtime.guard import require_model_isolation
    from lai_runtime.models import import_model
    from lai_runtime.process import RuntimeManager

    from .m2_steps import _progress, running, verify_inference

    require_model_isolation()
    cfg = load_runtime_config(paths)
    if params.get("model"):
        cfg = replace(cfg, model=params["model"])
    for d in (paths.runtime, paths.models):
        d.mkdir(parents=True, exist_ok=True)
    mgr = RuntimeManager(cfg, paths, platform="linux")
    if op == "verify":
        ok, message = verify_inference(mgr)
        return {"ok": ok, "message": message}
    if op == "import":
        target = params.get("target") or cfg.model
        source = _source(params, cfg.model)
        with running(mgr) as client:
            import_model(client, source, target, _progress)
        print(file=sys.stderr)
        return {"ok": True, "target": target, "gguf": str(source.gguf)}
    if op == "list":
        with running(mgr) as client:
            return {"ok": True, "models": [
                {"name": m["name"], "digest": m.get("digest", "")} for m in client.list_models()]}
    raise ValueError(f"bilinmeyen işlem: {op}")


def _writable(paths: AppPaths) -> list[Path]:
    paths.runtime.mkdir(parents=True, exist_ok=True)
    return [p for p in (paths.root, paths.config_dir) if p.is_dir()]


def _readable(params: dict) -> list[Path]:
    out = []
    for key in ("offline_dir", "gguf", "dir"):
        if params.get(key):
            p = Path(params[key]).expanduser().resolve()
            out.append(p if p.is_dir() else p.parent)
    return out


def run_isolated(op: str, paths: AppPaths, **params) -> dict:
    """İşlemi yalıtımda yürütür. Yalıtım kurulamazsa hata verir; doğrudan çalıştırma yoktur."""
    from lai_runtime.guard import isolated
    from lai_runtime.sandbox import launch

    params = {k: (str(v) if isinstance(v, Path) else v) for k, v in params.items() if v}
    if isolated():
        return perform(op, paths, params)
    payload = json.dumps({"op": op, "paths": _paths_dict(paths), "params": params})
    result = launch([sys.executable, "-m", "lai_setup.isolated", payload],
                    writable=_writable(paths), readable=_readable(params),
                    stdout=subprocess.PIPE, text=True)
    lines = [ln for ln in (result.stdout or "").splitlines() if ln.strip()]
    try:
        data = json.loads(lines[-1])
    except (IndexError, ValueError):
        raise OllamaError(
            f"yalıtılmış model işlemi sonuç vermedi (çıkış {result.returncode})") from None
    if "error" in data:
        raise _ERRORS.get(data.get("type"), RuntimeError)(data["error"])
    return data


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    request = json.loads(argv[0])
    try:
        data = perform(request["op"], _paths(request["paths"]), request["params"])
        code = 0
    except Exception as e:  # noqa: BLE001 — hata tipi ve iletisi dış sürece taşınır
        name = next((n for n, c in _ERRORS.items() if type(e) is c), type(e).__name__)
        data, code = {"error": str(e), "type": name}, 1
    print(json.dumps(data, ensure_ascii=False), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
