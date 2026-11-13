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
from .paths import resolve_paths
from .report import Report
from .runner import CommandRunner, SystemRunner
from .state import InstallState
from .steps import Context, StepResult, StepStatus, run_steps
from .uninstall import PURGE_WORD, is_managed_root, plan_uninstall, execute

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_CANCEL = 0, 1, 2, 3
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REQUIREMENTS = REPO_ROOT / "requirements" / "app.txt"


def all_steps():
    """Kurulum adımlarının tamamı. Sonraki kilometre taşları buraya ekler."""
    return m1_steps()


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lai-setup", description="Local Assessment AI kurulum motoru")
    p.add_argument("--version", action="version", version=__version__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--lai-home", help="tüm uygulama dizinleri için kök (gelişmiş)")
    common.add_argument("--requirements", help="Python bağımlılık listesi (gelişmiş)")
    sub = p.add_subparsers(dest="cmd", required=True)

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

    doc = sub.add_parser("doctor", parents=[common], help="salt okunur sağlık denetimi")
    doc.add_argument("--json", action="store_true")
    doc.add_argument("--save-report", action="store_true", help="raporu logs/ altına da yaz")

    u = sub.add_parser("uninstall", parents=[common], help="kaldır (veriler korunur)")
    u.add_argument("--purge-data", action="store_true",
                   help="değerlendirme verilerini ve raporları da sil (geri alınamaz)")
    u.add_argument("--yes", action="store_true")
    return p


def main(argv: list[str] | None = None, *, env: Mapping[str, str] | None = None,
         runner: CommandRunner | None = None, isatty: bool | None = None,
         input_fn: Callable[[str], str] = input, fs_root: Path = Path("/"),
         http_get=None, platform: str | None = None) -> int:
    args = _parser().parse_args(argv)
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

    if args.cmd == "uninstall":
        return _uninstall(args, paths, env, interactive, ask, out, err)

    state = InstallState.load(paths.state_file)
    ctx = Context(paths=paths, info=info, runner=runner, state=state,
                  requirements_file=requirements, python=sys.executable,
                  interactive=interactive)

    if args.cmd == "doctor":
        results = run_steps(all_steps(), ctx, "doctor")
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
    if args.online:
        ctx.mode = "online"
    elif args.offline:
        ctx.mode, ctx.offline_dir = "offline", Path(args.offline).expanduser().resolve()
        if not ctx.offline_dir.is_dir():
            err(f"Çevrimdışı paket dizini bulunamadı: {ctx.offline_dir}")
            return EXIT_USAGE
    elif not args.dry_run and _needs_source(all_steps(), ctx, args.cmd):
        if not interactive:
            err("Kaynak seçilmedi. İnternetten indirmeye izin vermek için --online, "
                "yerel paket için --offline <dizin> kullanın. Hiçbir şey indirilmedi.")
            return EXIT_USAGE
        out("Kurulum için eksik dosyalar var. Nereden alınsın?\n"
            "  [1] Çevrimiçi (Python paketleri indirilir, yaklaşık 250 MB)\n"
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

    results = run_steps(all_steps(), ctx, args.cmd)
    rep = Report(action=args.cmd, info=info, results=results, pins=dict(state.pins))
    if args.dry_run:
        rep.notes.append("Deneme modu: hiçbir dosya yazılmadı.")
        out(rep.to_text())
        return EXIT_OK if rep.ok else EXIT_FAIL
    state.save()
    _warn_ports(rep)
    j, t = rep.write(paths.logs) if paths.logs.is_dir() else (None, None)
    out(rep.to_text())
    if t:
        out(f"Rapor: {t}")
    return EXIT_OK if rep.ok else EXIT_FAIL


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
