"""`model` ve `runtime` alt komutları."""
from __future__ import annotations

import signal
import sys
import time
from pathlib import Path

from lai_runtime.client import ModelNotFoundError, OllamaError
from lai_runtime.config import load_runtime_config
from lai_runtime.models import ModelSourceError, find_source_model, import_model, source_from_gguf
from lai_runtime.ports import PortConflictError
from lai_runtime.process import RuntimeManager, RuntimeStartError

from .m2_steps import _progress, model_installed, running, verify_inference
from .state import InstallState

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_CANCEL = 0, 1, 2, 3


def _mgr(paths, platform, model: str | None = None) -> RuntimeManager:
    cfg = load_runtime_config(paths)
    if model:
        from dataclasses import replace
        cfg = replace(cfg, model=model)
    return RuntimeManager(cfg, paths, platform=platform)


def _pin(paths, model: str) -> None:
    st = InstallState.load(paths.state_file)
    st.pins["model"] = model
    st.pins["model_digest"] = model_installed(paths, model) or ""
    if paths.root.is_dir():
        st.save()


def model_command(args, paths, platform, interactive, ask, out, err) -> int:
    try:
        mgr = _mgr(paths, platform, getattr(args, "model", None))
    except ValueError as e:
        err(f"Ayar hatası: {e}")
        return EXIT_USAGE
    model = mgr.cfg.model
    try:
        if args.model_cmd == "pull":
            if not args.online:
                if not interactive:
                    err(f"{model} indirilmedi. İnternetten indirmeye izin vermek için --online "
                        "kullanın ya da 'model import' ile yerel dosyadan aktarın.")
                    return EXIT_USAGE
                out(f"{model} modeli internetten (ollama.com) indirilecek. Boyutu indirme başında "
                    "gösterilir; yalnız bu indirme için ağ kullanılır.")
                if ask("İndirmeye izin veriyor musunuz? [e/H]: ").lower() not in ("e", "evet", "y", "yes"):
                    out("İptal edildi; hiçbir şey indirilmedi.")
                    return EXIT_CANCEL
            with running(mgr) as client:
                client.pull(model, _progress)
            print(file=sys.stderr)
            _pin(paths, model)
            out(f"{model} indirildi.")
            return EXIT_OK

        if args.model_cmd == "import":
            src = Path(args.source).expanduser()
            target = args.target or model
            if src.is_file():
                source = source_from_gguf(src, target)
            elif src.is_dir():
                if not args.name:
                    err("Dizinden içe aktarmada --name (kaynaktaki model adı) gerekir.")
                    return EXIT_USAGE
                source = find_source_model(src, args.name)
            else:
                err(f"Kaynak bulunamadı: {src}")
                return EXIT_USAGE
            out(f"{source.gguf} → {target} (kaynak yalnız okunur)")
            with running(mgr) as client:
                import_model(client, source, target, _progress)
            print(file=sys.stderr)
            _pin(paths, target)
            out(f"{target} içe aktarıldı.")
            return EXIT_OK

        if args.model_cmd == "verify":
            t = time.time()
            ok, msg = verify_inference(mgr)
            (out if ok else err)(f"{'Doğrulandı' if ok else 'Doğrulanamadı'}: {msg} "
                                 f"({time.time() - t:.1f} sn)")
            return EXIT_OK if ok else EXIT_FAIL

        if args.model_cmd == "list":
            with running(mgr) as client:
                for m in client.list_models():
                    out(f"  {m['name']}  {m.get('digest', '')[:19]}")
            return EXIT_OK
    except (ModelSourceError, ModelNotFoundError) as e:
        err(f"Model hatası: {e}")
        return EXIT_FAIL
    except (PortConflictError, RuntimeStartError, OllamaError) as e:
        err(f"Çalışma ortamı hatası: {e}")
        return EXIT_FAIL
    return EXIT_USAGE


def runtime_command(args, paths, platform, interactive, ask, out, err) -> int:
    try:
        mgr = _mgr(paths, platform)
    except ValueError as e:
        err(f"Ayar hatası: {e}")
        return EXIT_USAGE
    if args.runtime_cmd == "status":
        st = mgr.status()
        if st.running:
            out(f"Çalışıyor: pid {st.pid}, {mgr.base_url}, Ollama {st.version}")
        else:
            out(f"Uygulamanın Ollama süreci çalışmıyor (port {mgr.cfg.port}).")
        return EXIT_OK
    if args.runtime_cmd == "stop":
        out("Durduruldu." if mgr.stop() else "Uygulamaya ait çalışan süreç yok; hiçbir şeye dokunulmadı.")
        return EXIT_OK
    if args.runtime_cmd == "run":
        try:
            st = mgr.start()
        except (PortConflictError, RuntimeStartError) as e:
            err(str(e))
            return EXIT_FAIL
        out(f"Çalışıyor: pid {st.pid}, {mgr.base_url}. Durdurmak için Ctrl+C.")
        try:
            signal.signal(signal.SIGTERM, lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
            while mgr.status().running:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            mgr.stop()
        return EXIT_OK
    return EXIT_USAGE
