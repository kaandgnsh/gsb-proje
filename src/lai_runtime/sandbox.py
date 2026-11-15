"""Linux masaüstü güvenlik sınırı: ağ/PID/mount ad alanı + miras kalan seccomp.

Model indirmez; ana makine ağına/firewall'a yazmaz. Windows için fail closed.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import errno
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


class IsolationError(RuntimeError):
    pass


GUIDANCE = (
    "Ağ yalıtımı başlatılamadı; değerlendirme açılmadı. Linux için bubblewrap, "
    "libseccomp ve izin verilen kullanıcı/ağ ad alanları gerekir. Dağıtımınızın "
    "yöneticisiyle bu önkoşulları doğrulayın; uygulama sistem ayarlarını değiştirmez. "
    "Windows ağ yalıtımı henüz doğrulanmadı; M12 doğrulaması tamamlanmadan kullanılamaz."
)


def _seccomp_library():
    name = ctypes.util.find_library("seccomp")
    if not name:
        raise IsolationError("libseccomp bulunamadı. " + GUIDANCE)
    try:
        lib = ctypes.CDLL(name, use_errno=True)
    except OSError as error:
        raise IsolationError("libseccomp yüklenemedi. " + GUIDANCE) from error
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
    lib.seccomp_rule_add_array.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.c_void_p,
    ]
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    return lib


class ArgCompare(ctypes.Structure):
    _fields_ = [
        ("arg", ctypes.c_uint),
        ("op", ctypes.c_int),
        ("a", ctypes.c_uint64),
        ("b", ctypes.c_uint64),
    ]


def install_filter():
    """AF_UNIX yalnız socketpair ile yerel; socket() ile tüm broker erişimi yasak.

    INET/INET6 sadece yeni ağ ad alanının lo arayüzünü görür. Compat ABI varsayılan
    olarak kill; io_uring/socketcall ve namespace/FD kaçış yolları kapalıdır.
    """
    lib = _seccomp_library()
    ctx = lib.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW; unknown architectures killed.
    if not ctx:
        raise IsolationError("seccomp filtresi ayrılamadı")
    try:

        def deny(name, *comparisons, error=errno.EPERM):
            number = lib.seccomp_syscall_resolve_name(name.encode())
            if number < 0:
                return  # Bu mimaride olmayan çağrı çalıştırılamaz.
            array = (ArgCompare * len(comparisons))(*comparisons)
            if (
                lib.seccomp_rule_add_array(
                    ctx, 0x00050000 | error, number, len(array), array
                )
                != 0
            ):
                raise IsolationError("seccomp kuralı yüklenemedi: " + name)

        # socket domain != INET && != INET6. Tek argümanı iki kez karşılaştırmak
        # libseccomp'ta geçersiz olduğundan mevcut/gelecek tüm diğer domainleri
        # unsigned aralıklarla reddet (INET=2, INET6=10).
        deny("socket", ArgCompare(0, 2, socket.AF_INET, 0))  # LT
        for domain in range(socket.AF_INET + 1, socket.AF_INET6):
            deny("socket", ArgCompare(0, 4, domain, 0))  # EQ
        deny("socket", ArgCompare(0, 6, socket.AF_INET6, 0))
        deny("socketpair", ArgCompare(0, 1, socket.AF_UNIX, 0))  # NE
        for name in (
            "setns",
            "unshare",
            "mount",
            "umount2",
            "pivot_root",
            "chroot",
            "open_tree",
            "move_mount",
            "fsopen",
            "fsconfig",
            "fsmount",
            "fspick",
            "ptrace",
            "process_vm_readv",
            "process_vm_writev",
            "pidfd_getfd",
            "open_by_handle_at",
            "bpf",
            "io_uring_setup",
            "socketcall",
            "kexec_load",
            "init_module",
            "finit_module",
            "delete_module",
        ):
            deny(name)
        deny(
            "clone3", error=errno.ENOSYS
        )  # pthread/fork geri dönüşü clone(); yeni namespace yok.
        for flag in (
            0x00020000,
            0x02000000,
            0x04000000,
            0x08000000,
            0x10000000,
            0x20000000,
            0x40000000,
        ):
            deny("clone", ArgCompare(0, 7, flag, flag))  # MASKED_EQ
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
            raise IsolationError("no_new_privs etkinleştirilemedi")
        if lib.seccomp_load(ctx) != 0:
            raise IsolationError("çekirdek seccomp filtresini reddetti")
    finally:
        lib.seccomp_release(ctx)


def kernel_status():
    """Ortam bayrağını kanıt sayma: çekirdeği ve yasak socket çağrısını sınar."""
    status = dict(
        line.split(":", 1)
        for line in Path("/proc/self/status").read_text().splitlines()
        if ":" in line
    )
    host = os.environ.get("LAI_SANDBOX_HOST_NETNS")
    current = str(Path("/proc/self/ns/net").stat().st_ino)
    unix_denied = False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except OSError as error:
        unix_denied = error.errno == errno.EPERM
    else:
        s.close()
    return {
        "network_namespace_changed": bool(host and current != host),
        "loopback_only": {
            line.split(":", 1)[0].strip()
            for line in Path("/proc/net/dev").read_text().splitlines()[2:]
        }
        == {"lo"},
        "no_new_privs": status.get("NoNewPrivs", "").strip() == "1",
        "seccomp_filter": status.get("Seccomp", "").strip() == "2",
        "no_capabilities": int(status.get("CapEff", "1").strip(), 16) == 0,
        "unix_socket_denied": unix_denied,
    }


def require_isolation():
    if sys.platform != "linux":
        raise IsolationError(GUIDANCE)
    try:
        result = kernel_status()
    except (OSError, ValueError) as error:
        raise IsolationError(GUIDANCE) from error
    if not all(result.values()):
        raise IsolationError(
            GUIDANCE
            + " Başarısız kontroller: "
            + ", ".join(k for k, v in result.items() if not v)
        )
    return result


_ENV = ("PATH", "LANG", "LC_ALL", "TZ", "PYTHONPATH", "LAI_HOME")


def clean_env(source, *, gui=False):
    env = {k: source[k] for k in _ENV if k in source}
    env.setdefault("PATH", "/usr/bin:/bin")
    env.update(
        PYTHONDONTWRITEBYTECODE="1",
        QT_QPA_PLATFORM="wayland" if gui else "offscreen",
        QT_OPENGL="software",
        LIBGL_ALWAYS_SOFTWARE="1",
        QT_QUICK_BACKEND="software",
        QT_NO_NETWORKPROXY="1",
        QT_ACCESSIBILITY="0",
        NO_AT_BRIDGE="1",
        OLLAMA_NO_CLOUD="1",
        OLLAMA_HOST="127.0.0.1:11435",
    )
    # Özel Qt eklentisi, DBus/portal, SSH agent ve proxy adresleri taşınmaz.
    return env


def launch(command, *, writable=(), readable=(), gui=False, env=None, **kwargs):
    """Doğrulanmış sınır içinde komut çalıştır; başarısızlıkta asla doğrudan çalıştırma.

    cwd ve okuma erişimi korunur. Yazma yalnız açıkça verilen klasörlere ve özel
    /tmp alanına yapılabilir. Alt süreçlerin ömrü bubblewrap PID namespace'indedir.
    """
    if sys.platform != "linux":
        raise IsolationError(GUIDANCE)
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise IsolationError("bubblewrap bulunamadı. " + GUIDANCE)
    _seccomp_library()
    source = dict(os.environ if env is None else env)
    child_env = clean_env(source, gui=gui)
    child_env["PYTHONPATH"] = os.pathsep.join(
        filter(
            None,
            [str(Path(__file__).resolve().parents[1]), child_env.get("PYTHONPATH")],
        )
    )
    wayland = None
    if gui:
        runtime = source.get("XDG_RUNTIME_DIR")
        display = source.get("WAYLAND_DISPLAY")
        if not runtime or not display:
            raise IsolationError(
                "Wayland oturumu bulunamadı; XDG_RUNTIME_DIR ve WAYLAND_DISPLAY gerekir. "
                + GUIDANCE
            )
        wayland = (Path(runtime) / display).absolute()
        if not wayland.is_socket():
            raise IsolationError("Wayland soketi bulunamadı. " + GUIDANCE)
    writes = sorted({str(Path(p).expanduser().resolve()) for p in writable}, key=len)
    for directory in writes:
        if (
            directory == "/"
            or any(
                directory == p or directory.startswith(p + "/")
                for p in ("/proc", "/sys", "/dev", "/run")
            )
        ) or not Path(directory).is_dir():
            raise IsolationError("Geçersiz yazma klasörü: " + directory)
    with tempfile.TemporaryDirectory(prefix="lai-boundary-") as control:
        control = Path(control)
        payload = control / "launch.json"
        child_env.update(
            LAI_SANDBOX_ACTIVE="1",
            LAI_SANDBOX_HOST_NETNS=str(Path("/proc/self/ns/net").stat().st_ino),
            XDG_RUNTIME_DIR="/run/lai",
            HOME="/run/lai/home",
            TMPDIR="/run/lai/tmp",
        )
        payload.write_text(
            json.dumps(
                {"command": list(command), "env": child_env, "wayland": bool(gui)}
            )
        )
        argv = [
            bwrap,
            "--unshare-all",
            "--unshare-user",
            "--disable-userns",
            "--assert-userns-disabled",
            "--new-session",
            "--die-with-parent",
            "--cap-drop",
            "ALL",
            "--ro-bind",
            "/",
            "/",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/run",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/run/lai",
            "--chmod",
            "0700",
            "/run/lai",
            "--dir",
            "/run/lai/home",
            "--dir",
            "/run/lai/tmp",
        ]
        for directory in writes:
            argv += ["--bind", directory, directory]
        for directory in readable:
            directory = Path(directory).resolve()
            if not directory.is_dir() or directory == Path("/"):
                raise IsolationError("Geçersiz salt okunur paket dizini")
            argv += ["--ro-bind", str(directory), str(directory)]
        argv += ["--ro-bind", str(control), "/run/lai-control"]
        if wayland:
            argv += ["--ro-bind", str(wayland), "/run/lai/wayland"]
        # -I: env/PYTHONPATH/site dışı kontrol kodu filtre öncesinde içe aktarılmaz.
        argv += [
            "--",
            sys.executable,
            "-I",
            "-B",
            str(Path(__file__).resolve()),
            "--bootstrap",
            "/run/lai-control/launch.json",
        ]
        try:
            return subprocess.run(argv, env=clean_env(source), check=False, **kwargs)
        except OSError as error:
            raise IsolationError(GUIDANCE + " " + str(error)) from error


def bootstrap(payload):
    data = json.loads(Path(payload).read_text())
    for entry in list(Path("/proc/self/fd").iterdir()):
        fd = int(entry.name)
        if fd > 2:
            try:
                os.close(fd)
            except OSError:
                pass
    # bwrap FDs >2'yi kapatır; standart FD'lerde host socket bulunamaz.
    for fd in range(3):
        if stat.S_ISSOCK(os.fstat(fd).st_mode):
            raise IsolationError("Devralınan ağ soketi reddedildi")
    os.environ.clear()
    os.environ.update(data["env"])
    if data["wayland"]:
        display = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        display.connect("/run/lai/wayland")
        display.set_inheritable(True)
        os.environ["WAYLAND_SOCKET"] = str(display.fileno())
        # Exec'e kadar canlı tut. Yalnız önceden bağlı görüntü bağlantısı istisnadır.
    install_filter()
    require_isolation()
    os.execvpe(data["command"][0], data["command"], dict(os.environ))


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Yerel ağ yalıtımı: normal çalıştırmada güvenli sınır zorunludur"
    )
    p.add_argument("--write-dir", type=Path, action="append", default=[])
    p.add_argument("--gui", action="store_true")
    p.add_argument("--bootstrap", help=argparse.SUPPRESS)
    p.add_argument("command", nargs=argparse.REMAINDER)
    a = p.parse_args(argv)
    try:
        if a.bootstrap:
            bootstrap(a.bootstrap)
            return 0
        cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
        if not cmd:
            p.error("Çalıştırılacak komut gerekli")
        result = launch(cmd, writable=a.write_dir, gui=a.gui)
        if result.returncode:
            print(GUIDANCE, file=sys.stderr)
        return result.returncode
    except (IsolationError, OSError, ValueError) as error:
        print("HATA: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
