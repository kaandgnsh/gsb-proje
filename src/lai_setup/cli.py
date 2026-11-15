"""lai_setup komut satırı: detect / install / repair / doctor / uninstall."""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from . import __version__
from .detect import detect_system
from .distro import missing_prereqs, strategy_for, suggest_command
from .m1_steps import m1_steps
from .m2_steps import m2_steps
from .m3_steps import m3_steps
from .paths import resolve_paths
from .report import Report
from .runner import CommandRunner, SystemRunner
from .state import InstallState
from .steps import Context, StepResult, StepStatus, run_steps
from .uninstall import PURGE_WORD, execute, is_managed_root, plan_uninstall

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_CANCEL = 0, 1, 2, 3
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REQUIREMENTS = REPO_ROOT / "requirements" / "app.txt"


def all_steps(skip_ai: bool = False):
    """Kurulum adımlarının tamamı. Sonraki kilometre taşları buraya ekler."""
    return m1_steps() + m3_steps() + ([] if skip_ai else m2_steps())


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lai-setup", description="Local Assessment AI kurulum motoru")
    p.add_argument("--version", action="version", version=__version__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--lai-home", help="tüm uygulama dizinleri için kök (gelişmiş)")
    common.add_argument("--requirements", help="Python bağımlılık listesi (gelişmiş)")
    sub = p.add_subparsers(dest="cmd", required=True)

    bundle = sub.add_parser("bundle", help="yerel AppDir oluştur")
    bundle.add_argument("--output", required=True)
    bundle.add_argument("--appimagetool")
    bundle.add_argument("--appimage-runtime")
    bundle.add_argument("--component",choices=["application","runtime","model"],default="application")
    bundle.add_argument("--source")

    d = sub.add_parser("detect", parents=[common], help="ortamı salt okunur tespit et")
    d.add_argument("--json", action="store_true")

    for name, hlp in (("install", "kur"), ("repair", "bozuk adımları onar")):
        s = sub.add_parser(name, parents=[common], help=hlp)
        src = s.add_mutually_exclusive_group()
        src.add_argument("--online", action="store_true",
                         help="gerekli dosyaları internetten indir (açık izin)")
        src.add_argument("--offline", metavar="DİZİN", help="çevrimdışı paket dizininden kur")
        s.add_argument("--dry-run", action="store_true", help="hiçbir şey yazmadan planı göster")
        s.add_argument("--yes", action="store_true", help="onay sorularını evet kabul et")
        s.add_argument("--skip-ai", action="store_true",
                       help="yalnız uygulamayı kur; Ollama ve modeli sonra kur")

    doc = sub.add_parser("doctor", parents=[common], help="salt okunur sağlık denetimi")
    doc.add_argument("--json", action="store_true")
    doc.add_argument("--network", action="store_true", help="yalnız salt okunur süreç ağacı soket denetimi; yalıtım kanıtı değildir")
    doc.add_argument("--pid", type=int, help="--network için denetlenecek süreç kimliği")
    doc.add_argument("--save-report", action="store_true", help="raporu logs/ altına da yaz")

    m = sub.add_parser("model", parents=[common], help="yapay zekâ modeli işlemleri")
    msub = m.add_subparsers(dest="model_cmd", required=True)
    mp = msub.add_parser("pull", parents=[common], help="modeli internetten indir (açık izin)")
    mp.add_argument("--online", action="store_true", help="indirmeye izin ver")
    mp.add_argument("--model", help="varsayılan: config.toml içindeki model")
    mi = msub.add_parser("import", parents=[common], help="modeli yerel dosyadan içe aktar")
    mi.add_argument("--from", dest="source", required=True,
                    help="başka bir Ollama model dizini ya da .gguf dosyası (yalnız okunur)")
    mi.add_argument("--name", help="kaynak dizindeki model adı (dizin için zorunlu)")
    mi.add_argument("--as", dest="target", help="uygulamadaki adı (varsayılan: config modeli)")
    mv = msub.add_parser("verify", parents=[common], help="modeli çalıştırarak doğrula")
    mv.add_argument("--model")
    msub.add_parser("list", parents=[common], help="uygulamanın modellerini listele")

    dbp = sub.add_parser("db", parents=[common], help="veritabanı: durum, yedek, geri yükleme")
    dsub = dbp.add_subparsers(dest="db_cmd", required=True)
    dsub.add_parser("status", parents=[common])
    db_b = dsub.add_parser("backup", parents=[common], help="yedek al")
    db_b.add_argument("--to", help="yedek dizini (varsayılan: data/backups)")
    db_v = dsub.add_parser("verify", parents=[common], help="yedeği doğrula")
    db_v.add_argument("file")
    db_r = dsub.add_parser("restore", parents=[common], help="yedekten geri yükle")
    db_r.add_argument("file")
    db_r.add_argument("--yes", action="store_true")

    rt = sub.add_parser("runtime", parents=[common], help="uygulamanın Ollama süreci")
    rsub = rt.add_subparsers(dest="runtime_cmd", required=True)
    rsub.add_parser("status", parents=[common])
    rsub.add_parser("stop", parents=[common], help="yalnız uygulamanın kendi sürecini durdurur")
    rsub.add_parser("run", parents=[common], help="ön planda çalıştır (Ctrl+C ile durur)")

    u = sub.add_parser("uninstall", parents=[common], help="kaldır (veriler korunur)")
    u.add_argument("--purge-data", action="store_true",
                   help="değerlendirme verilerini ve raporları da sil (geri alınamaz)")
    u.add_argument("--yes", action="store_true")
    for command in ("install", "repair", "doctor", "uninstall"):
        sub.choices[command].add_argument("--bundle", help="doğrulanmış AppDir paketi")
    return p


def main(argv: list[str] | None = None, *, env: Mapping[str, str] | None = None,
         runner: CommandRunner | None = None, isatty: bool | None = None,
         input_fn: Callable[[str], str] = input, fs_root: Path = Path("/"),
         http_get=None, platform: str | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.cmd == 'bundle':
        from .bundle import main as bundle_main
        return bundle_main(['--output',args.output] + (['--appimagetool',args.appimagetool] if args.appimagetool else []) + (['--appimage-runtime',args.appimage_runtime] if args.appimage_runtime else []) + ['--component',args.component] + (['--source',args.source] if args.source else []))
    package_home = Path(getattr(args,'lai_home',None) or (env or os.environ).get('LAI_HOME') or Path.home()/'.local/share/local-assessment-ai')
    if getattr(args,'bundle',None) or (args.cmd in ('doctor','uninstall','repair') and (package_home/'.lai-package.json').is_file() and not getattr(args,'network',False)):
        from .bundle import manage
        try:
            if args.cmd=='uninstall' and not args.yes:
                print('Paket kaldırma için --yes gerekli; veriler korunur.',file=sys.stderr)
                return EXIT_USAGE
            if getattr(args,'dry_run',False):
                from .bundle import verify
                verify(args.bundle)
                print(json.dumps({'dry_run':True,'target':str(package_home)},ensure_ascii=False))
                return EXIT_OK
            if getattr(args,'purge_data',False):
                raise ValueError('Paket kaldırma verileri korur; purge işlemi bu yolda desteklenmiyor')
            print(json.dumps(manage(args.cmd,package_home,getattr(args,'bundle',None)),ensure_ascii=False))
            return EXIT_OK
        except (ValueError,OSError) as error:
            print('Paket hatası: '+str(error),file=sys.stderr)
            return EXIT_FAIL
    if args.cmd == "doctor" and args.network:
        from lai_runtime.netaudit import diagnostic
        try:
            data = diagnostic(args.pid if args.pid is not None else os.getpid())
        except (OSError, ValueError) as error:
            print("Ağ denetimi yapılamadı (Linux /proc gerekir): " + str(error), file=sys.stderr)
            return EXIT_FAIL
        print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else
            data["notice"] + "\n" + "\n".join(data["violations"]) + f"\nUnix soket sayısı: {len(data['unix_sockets'])}")
        return EXIT_FAIL if data["violations"] else EXIT_OK
    env = dict(os.environ if env is None else env)
    if getattr(args, "lai_home", None):
        env["LAI_HOME"] = args.lai_home
    platform = platform or ("windows" if sys.platform.startswith("win") else "linux")
    runner = runner or SystemRunner()
    interactive = sys.stdin.isatty() if isatty is None else isatty
    home = Path(env.get("HOME") or env.get("USERPROFILE") or Path.home())
    paths = resolve_paths(platform, env, home)
    info = detect_system(runner, paths=paths, fs_root=fs_root, platform=platform, http_get=http_get)

    def out(msg=""):
        print(msg, flush=True)

    def err(msg):
        print(msg, file=sys.stderr, flush=True)

    def ask(prompt: str) -> str:
        try:
            return input_fn(prompt).strip()
        except (EOFError, StopIteration, KeyboardInterrupt):
            return ""

    if args.cmd == "detect":
        if args.json:
            out(json.dumps(info.to_dict(), ensure_ascii=False, indent=2))
        else:
            out(Report(action="detect", info=info, results=[]).to_text())
        return EXIT_OK

    requirements = Path(args.requirements) if args.requirements else DEFAULT_REQUIREMENTS

    if args.cmd in ("install", "repair") and not requirements.is_file():
        err(f"Bağımlılık listesi bulunamadı: {requirements}")
        return EXIT_USAGE
    if args.cmd in ("install", "repair") and _foreign_root(paths):
        err(f"{paths.root} boş değil ve bir Local Assessment AI kurulumu değil. "
            "Güvenlik için bu dizine kurulmaz; boş ya da yeni bir dizin seçin (--lai-home).")
        return EXIT_USAGE

    if args.cmd == "db":
        from .db_cli import db_command
        return db_command(args, paths, interactive, ask, out, err)

    if args.cmd in ("model", "runtime"):
        from .ai_cli import model_command, runtime_command
        fn = model_command if args.cmd == "model" else runtime_command
        return fn(args, paths, platform, interactive, ask, out, err)

    if args.cmd == "uninstall":
        return _uninstall(args, paths, env, interactive, ask, out, err)

    state = InstallState.load(paths.state_file)
    ctx = Context(paths=paths, info=info, runner=runner, state=state,
                  requirements_file=requirements, python=sys.executable,
                  interactive=interactive)

    if args.cmd == "doctor":
        results = run_steps(all_steps(skip_ai=_ai_skipped(state)), ctx, "doctor")
        if state.corrupt:
            results.insert(0, StepResult("state", "Kurulum durum dosyası", StepStatus.FAILED,
                                         "bozuk; 'repair' ile yeniden oluşturulur"))
        rep = Report(action="doctor", info=info, results=results, pins=state.pins)
        _warn_ports(rep)
        out(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2) if args.json else rep.to_text())
        if args.save_report and paths.logs.is_dir():
            rep.write(paths.logs)
        return EXIT_OK if rep.ok else EXIT_FAIL

    # install / repair
    ctx.dry_run, ctx.assume_yes = args.dry_run, args.yes
    steps = all_steps(skip_ai=args.skip_ai)
    if args.online:
        ctx.mode = "online"
    elif args.offline:
        ctx.mode, ctx.offline_dir = "offline", Path(args.offline).expanduser().resolve()
        if not ctx.offline_dir.is_dir():
            err(f"Çevrimdışı paket dizini bulunamadı: {ctx.offline_dir}")
            return EXIT_USAGE
    elif not args.dry_run and _needs_source(steps, ctx, args.cmd):
        if not interactive:
            err("Kaynak seçilmedi. İnternetten indirmeye izin vermek için --online, "
                "yerel paket için --offline <dizin> kullanın. Hiçbir şey indirilmedi.")
            return EXIT_USAGE
        out("Kurulum için eksik dosyalar var. Nereden alınsın?\n"
            "  [1] Çevrimiçi (Python paketleri ~250 MB; model gerekiyorsa o da indirilir,\n"
            "      boyutu indirme başında gösterilir)\n"
            "  [2] Çevrimdışı paket dizini\n  [3] İptal")
        choice = ask("Seçiminiz [1/2/3]: ")
        if choice == "1":
            ctx.mode = "online"
        elif choice == "2":
            d = Path(ask("Paket dizini: ")).expanduser()
            if not d.is_dir():
                err(f"Dizin bulunamadı: {d}")
                return EXIT_USAGE
            ctx.mode, ctx.offline_dir = "offline", d.resolve()
        else:
            out("İptal edildi; hiçbir değişiklik yapılmadı.")
            return EXIT_CANCEL

    if not args.dry_run:
        rc = _ensure_prereqs(ctx, ask, out, err)
        if rc != EXIT_OK:
            return rc

    results = run_steps(steps, ctx, args.cmd)
    if args.skip_ai:
        state.pins["ai_skipped"] = "1"
    else:
        state.pins.pop("ai_skipped", None)
    rep = Report(action=args.cmd, info=info, results=results, pins=dict(state.pins))
    if args.dry_run:
        rep.notes.append("Deneme modu: hiçbir dosya yazılmadı.")
        out(rep.to_text())
        return EXIT_OK if rep.ok else EXIT_FAIL
    state.save()
    _warn_ports(rep)
    _j, t = rep.write(paths.logs) if paths.logs.is_dir() else (None, None)
    out(rep.to_text())
    if t:
        out(f"Rapor: {t}")
    return EXIT_OK if rep.ok else EXIT_FAIL


def _ai_skipped(state: InstallState) -> bool:
    return state.pins.get("ai_skipped") == "1"


def _foreign_root(paths) -> bool:
    root = paths.root
    if not root.is_dir() or is_managed_root(paths) or paths.state_file.exists():
        return False
    return any(root.iterdir())


def _needs_source(steps, ctx: Context, action: str) -> bool:
    """Herhangi bir adım indirme/paket gerektiriyor ve henüz tamam değil mi?"""
    for s in steps:
        if getattr(s, "needs_source", False):
            done = s.verify(ctx)[0] if action == "repair" else s.check(ctx)
            if not done:
                return True
    return False


def _ensure_prereqs(ctx: Context, ask, out, err) -> int:
    missing = missing_prereqs(ctx.python, ctx.runner)
    if not missing:
        return EXIT_OK
    cmd = suggest_command(strategy_for(ctx.info), missing, ctx.runner)
    err("Python 'venv' modülü eksik; uygulama ortamı oluşturulamaz.")
    if cmd is None:
        err("Dağıtımınız için otomatik öneri yok. Lütfen python3-venv karşılığını kurun.")
        return EXIT_FAIL
    out("Sistem paketi kurmak yönetici yetkisi gerektirir (yalnız bu komut için):\n  "
        + " ".join(cmd))
    if not ctx.interactive:
        err("Etkileşimsiz modda sistem paketi kurulmaz. Komutu kendiniz çalıştırın.")
        return EXIT_FAIL
    if not ctx.assume_yes and ask("Bu komut şimdi çalıştırılsın mı? [e/H]: ").lower() not in (
            "e", "evet", "y", "yes"):
        return EXIT_CANCEL
    r = ctx.runner.run(cmd, timeout=1800)
    if r.returncode != 0 or missing_prereqs(ctx.python, ctx.runner):
        err(f"Sistem paketi kurulamadı: {(r.stderr or r.stdout).strip()}")
        return EXIT_FAIL
    return EXIT_OK


def _warn_ports(rep: Report) -> None:
    o = rep.info.ollama
    if o.wildcard_11435:
        rep.results.append(StepResult("port", "Uygulama portu 11435", StepStatus.FAILED,
                                      "0.0.0.0 üzerinde dinleyen bir süreç var; ağdan erişilebilir"))
    if o.listening_11434:
        rep.notes.append("Sistem Ollama servisi (11434) algılandı; kurulum ona dokunmaz.")


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _uninstall(args, paths, env, interactive, ask, out, err) -> int:
    if not is_managed_root(paths):
        err(f"{paths.root} bir Local Assessment AI kurulum dizini değil "
            "(.lai-root işareti yok). Güvenlik için hiçbir şey silinmedi.")
        return EXIT_FAIL
    plan = plan_uninstall(paths, args.purge_data)
    out("Silinecekler:")
    for p in plan.remove:
        out(f"  - {p}")
    out(f"Toplam: {_human(plan.total_bytes())}")
    if plan.keep:
        out("Korunacak kullanıcı verileri:")
        for p in plan.keep:
            out(f"  + {p}")
    if not args.yes:
        if not interactive:
            err("Etkileşimsiz kaldırma için --yes gerekir.")
            return EXIT_USAGE
        if ask("Devam edilsin mi? [e/H]: ").lower() not in ("e", "evet", "y", "yes"):
            out("İptal edildi.")
            return EXIT_CANCEL
    if args.purge_data:
        if interactive:
            out("UYARI: değerlendirme verileri ve raporlar kalıcı olarak silinecek.")
            if ask(f"Onaylamak için '{PURGE_WORD}' yazın: ") != PURGE_WORD:
                out("Onay verilmedi; hiçbir şey silinmedi.")
                return EXIT_CANCEL
        elif env.get("LAI_CONFIRM_PURGE") != PURGE_WORD:
            err(f"Etkileşimsiz veri silme için LAI_CONFIRM_PURGE={PURGE_WORD} de gerekir. "
                "Hiçbir şey silinmedi.")
            return EXIT_USAGE
    errors = execute(plan, paths, args.purge_data)
    for e in errors:
        err(e)
    out("Kaldırma tamamlandı." + ("" if args.purge_data else " Verileriniz korundu."))
    return EXIT_FAIL if errors else EXIT_OK
