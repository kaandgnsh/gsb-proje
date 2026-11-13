"""M2 kurulum adımları: Ollama çalışma ortamı, model, doğrulama.

Tüm işlemler uygulamaya ait izole süreç (127.0.0.1:<port>, OLLAMA_NO_CLOUD=1) üzerinden
yapılır. Sistemdeki Ollama servisine, ayarlarına ve modellerine dokunulmaz.
"""
from __future__ import annotations

import re
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path

from lai_runtime.client import ModelNotFoundError, OllamaClient
from lai_runtime.config import RuntimeConfig, load_runtime_config
from lai_runtime.models import (ModelSourceError, find_source_model, import_model,
                                source_from_gguf, split_name)
from lai_runtime.process import RuntimeManager

from .steps import Context, Step

VERIFY_PROMPT = "Yalnızca TAMAM kelimesini yaz."


def runtime_manager(ctx_or_paths, platform: str | None = None) -> RuntimeManager:
    paths = getattr(ctx_or_paths, "paths", ctx_or_paths)
    plat = platform or getattr(getattr(ctx_or_paths, "info", None), "os", None)
    return RuntimeManager(load_runtime_config(paths), paths, platform=plat)


@contextmanager
def running(mgr: RuntimeManager, timeout: float = 120):
    """Çalışma ortamını gerekirse geçici olarak başlatır; yalnız kendi başlattığını kapatır."""
    already = mgr.status().running
    mgr.start(timeout=timeout)
    try:
        yield mgr.client()
    finally:
        if not already:
            mgr.stop()


def model_installed(paths, model: str) -> str | None:
    """Model izole depoda varsa ağırlık özetini döndürür (sunucu başlatmadan)."""
    try:
        return find_source_model(paths.models, model).gguf_digest
    except ModelSourceError:
        return None


def binary_version(ctx: Context, binary: str) -> str | None:
    r = ctx.runner.run([binary, "--version"], timeout=20)
    m = re.search(r"version is (\S+)", (r.stdout or "") + (r.stderr or ""))
    return m.group(1) if r.returncode == 0 and m else None


def offline_model_source(offline_dir: Path, model: str):
    store = offline_dir / "models"
    try:
        return find_source_model(store, model)
    except ModelSourceError:
        pass
    _, _, repo, tag = split_name(model)
    gguf = store / f"{repo.replace('/', '-')}-{tag}.gguf"
    if gguf.is_file():
        return source_from_gguf(gguf, model)
    raise ModelSourceError(
        f"çevrimdışı pakette {model} yok: {store}/ altında Ollama model deposu ya da "
        f"{gguf.name} bekleniyordu")


def _progress(ev: dict) -> None:
    if ev.get("total"):
        pct = 100 * ev.get("completed", 0) / ev["total"]
        print(f"\r  {ev.get('status', '')}: %{pct:5.1f} ({ev['total'] / 1e9:.2f} GB)", end="",
              file=sys.stderr, flush=True)
    elif ev.get("status"):
        print(f"\n  {ev['status']}", end="", file=sys.stderr, flush=True)


class RuntimeBinary(Step):
    id = "runtime"
    title = "Yerel AI çalışma ortamı (Ollama)"
    needs_source = True

    def check(self, ctx: Context) -> bool:
        return self.verify(ctx)[0]

    def apply(self, ctx: Context) -> None:
        exe = "ollama.exe" if ctx.info.os == "windows" else "ollama"
        if ctx.mode == "offline" and ctx.offline_dir and (ctx.offline_dir / "ollama" / "bin" / exe).is_file():
            shutil.copytree(ctx.offline_dir / "ollama", ctx.paths.runtime, dirs_exist_ok=True)
            (ctx.paths.runtime / "bin" / exe).chmod(0o755)
            return
        raise RuntimeError(
            "Ollama çalıştırılabilir dosyası bulunamadı. Çevrimdışı paketin ollama/bin/ dizinine "
            "koyun ya da dağıtımınızın paketini kurun (Arch: pkexec pacman -S ollama — sistem "
            "servisi etkinleştirilmez, uygulama kendi sürecini kullanır).")

    def verify(self, ctx: Context) -> tuple[bool, str]:
        binary = runtime_manager(ctx).resolve_binary()
        if not binary:
            return False, "ollama bulunamadı"
        ver = binary_version(ctx, binary)
        return (ver is not None, f"{binary} {ver}" if ver else f"{binary} çalışmıyor")

    def record_pins(self, ctx: Context) -> None:
        binary = runtime_manager(ctx).resolve_binary()
        ctx.state.pins["ollama_version"] = binary_version(ctx, binary) or "?"
        ctx.state.pins["ollama_binary"] = binary or ""


class ModelAvailable(Step):
    id = "model"
    title = "Yapay zekâ modeli"
    needs_source = True

    def check(self, ctx: Context) -> bool:
        return self.verify(ctx)[0]

    def apply(self, ctx: Context) -> None:
        mgr = runtime_manager(ctx)
        model = mgr.cfg.model
        if ctx.mode == "offline":
            source = offline_model_source(ctx.offline_dir, model)
            with running(mgr) as client:
                import_model(client, source, model, _progress)
        elif ctx.mode == "online":
            with running(mgr) as client:
                client.pull(model, _progress)
        else:
            raise RuntimeError(f"{model} yok ve indirme izni verilmedi (--online / --offline)")
        print(file=sys.stderr)

    def verify(self, ctx: Context) -> tuple[bool, str]:
        model = load_runtime_config(ctx.paths).model
        digest = model_installed(ctx.paths, model)
        return (True, f"{model} {digest[:19]}") if digest else (False, f"{model} kurulu değil")

    def record_pins(self, ctx: Context) -> None:
        model = load_runtime_config(ctx.paths).model
        ctx.state.pins["model"] = model
        ctx.state.pins["model_digest"] = model_installed(ctx.paths, model) or ""


class RuntimeVerify(Step):
    """Modeli gerçekten çalıştırır. doctor'da süreç başlatmaz (bkz. `model verify`)."""
    id = "ai-verify"
    title = "Yerel çıkarım doğrulaması"

    def _key(self, ctx: Context) -> str:
        return f"{ctx.state.pins.get('ollama_version')}|{ctx.state.pins.get('model_digest')}"

    def check(self, ctx: Context) -> bool:
        return ctx.state.steps.get(self.id, {}).get("verified_for") == self._key(ctx) \
            and self.verify(ctx)[0]

    def apply(self, ctx: Context) -> None:
        ok, msg = verify_inference(runtime_manager(ctx))
        if not ok:
            raise RuntimeError(msg)

    def verify(self, ctx: Context) -> tuple[bool, str]:
        ok_bin, m1 = RuntimeBinary().verify(ctx)
        ok_mod, m2 = ModelAvailable().verify(ctx)
        return ok_bin and ok_mod, f"{m1}; {m2}"

    def details(self, ctx: Context) -> dict:
        return {"verified_for": self._key(ctx)}


def verify_inference(mgr: RuntimeManager) -> tuple[bool, str]:
    model = mgr.cfg.model
    try:
        with running(mgr) as client:
            client.show(model)
            opts = {"num_predict": 32, "temperature": 0, "seed": 1}
            if mgr.cfg.accel == "cpu":
                opts["num_gpu"] = 0
            r = client.generate(model, VERIFY_PROMPT, options=opts)
    except ModelNotFoundError:
        return False, f"model bulunamadı: {model}"
    if not r.text.strip():
        return False, "model boş yanıt verdi"
    return True, f"{model} yanıt verdi ({r.eval_count} belirteç): {r.text.strip()[:60]!r}"


def m2_steps() -> list[Step]:
    return [RuntimeBinary(), ModelAvailable(), RuntimeVerify()]


__all__ = ["m2_steps", "runtime_manager", "running", "verify_inference", "model_installed",
           "RuntimeConfig", "OllamaClient"]
