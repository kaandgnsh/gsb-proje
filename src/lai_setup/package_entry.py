"""AppDir/AppImage ve kurulu komut için ortak, doğrulanan başlangıç."""

import json
import os
import socket
import sys
from pathlib import Path

from .bundle import verify


def main():
    root = Path(sys.argv[1])
    try:
        verify(root)
        args = sys.argv[2:]
        if args[:1] == ["--setup"]:
            from .cli import main as setup

            setup_args = args[1:]
            if setup_args[:1] not in (
                ["install"],
                ["repair"],
                ["doctor"],
                ["uninstall"],
            ):
                raise ValueError(
                    "Paket bakım yolu yalnız install/repair/doctor/uninstall kabul eder; yalıtımsız çalışma ortamı başlatılmaz"
                )
            if (
                setup_args[:1] in (["install"], ["repair"])
                and "--bundle" not in setup_args
            ):
                setup_args += ["--bundle", str(root)]
            return setup(setup_args)
        from lai_runtime.sandbox import GUIDANCE, launch, require_isolation

        if os.environ.get("LAI_SANDBOX_ACTIVE"):
            require_isolation()
            if args[:1] == ["--probe"]:
                targets = json.loads(Path(args[1]).read_text())["targets"]
                results = {}
                for name, spec in targets.items():
                    try:
                        with socket.socket(
                            getattr(socket, spec["family"]),
                            getattr(socket, spec["kind"]),
                        ) as sock:
                            sock.settimeout(0.35)
                            address = spec["address"]
                            sock.connect(
                                tuple(address) if isinstance(address, list) else address
                            )
                            sock.sendall(b"SENTETIK-M12")
                            results[name] = sock.recv(16).startswith(b"ACK")
                    except OSError:
                        results[name] = False
                try:
                    results["glibc_nss_resolve"] = (
                        socket.getaddrinfo(
                            "sentetik-m11.invalid", None, socket.AF_INET
                        )[0][4][0]
                        == "198.18.0.1"
                    )
                except OSError:
                    results["glibc_nss_resolve"] = False
                print(json.dumps(results))
                return 0
            if args[:1] == ["--ingest"]:
                from lai.ingest.__main__ import main as ingest

                return ingest(args[1:])
            if args[:1] == ["--cli"]:
                from lai.learning.__main__ import main as cli

                return cli(args[1:])
            from lai.ui.__main__ import main as ui

            return ui(args)
        from lai_setup.paths import resolve_paths

        paths = resolve_paths("linux", os.environ, Path.home())
        for folder in (paths.root, paths.config_dir):
            folder.mkdir(parents=True, exist_ok=True)
        writes = [paths.root, paths.config_dir]
        # CLI dosya işlemleri için açık çalışma alanı; varsayılan yalnız uygulama kökü.
        if os.environ.get("LAI_WORKSPACE"):
            writes.append(Path(os.environ["LAI_WORKSPACE"]).resolve())
        result = launch(
            [str(root / "AppRun"), *args],
            writable=writes,
            readable=[root],
            gui=args[:1] not in (["--cli"], ["--probe"], ["--ingest"]),
            env={**os.environ, "LAI_HOME": str(paths.root)},
        )
        if result.returncode:
            print(GUIDANCE, file=sys.stderr)
        return result.returncode
    except (OSError, ValueError, RuntimeError) as e:
        print("Paket başlatılamadı: " + str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
