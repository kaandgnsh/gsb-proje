"""Gerçek paket GUI başlangıcı; yalnız özel Wayland oturumunda sentetik boş DB."""

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

image, work = map(Path, sys.argv[1:3])
work.mkdir(parents=True, exist_ok=True)
root = work / "home"
db = root / "data/lai.db"
p = subprocess.Popen(
    [str(image), "--db", str(db)],
    env={**os.environ, "LAI_HOME": str(root)},
    start_new_session=True,
)
try:
    deadline = time.monotonic() + 45
    while not db.exists() and p.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    assert db.exists() and p.poll() is None, "Paket GUI başlatılamadı"
    with sqlite3.connect(db) as con:
        assert (
            con.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
            > 10
        )

    def children(pid):
        result = [pid]
        try:
            for value in Path(f"/proc/{pid}/task/{pid}/children").read_text().split():
                result += children(int(value))
        except OSError:
            pass
        return result

    host = Path("/proc/self/ns/net").stat().st_ino
    isolated = []
    for pid in children(p.pid):
        try:
            fields = dict(
                line.split(":", 1)
                for line in Path(f"/proc/{pid}/status").read_text().splitlines()
                if ":" in line
            )
            if (
                Path(f"/proc/{pid}/ns/net").stat().st_ino != host
                and fields["Seccomp"].strip() == "2"
                and fields["NoNewPrivs"].strip() == "1"
            ):
                isolated.append(pid)
        except OSError:
            pass
    assert isolated
    proof = {
        "normal_appimage_gui_started": True,
        "database_initialized": True,
        "kernel_filtered_descendants": len(isolated),
        "synthetic_only": True,
        "wayland": os.environ.get("WAYLAND_DISPLAY"),
    }
    (work / "gui-proof.json").write_text(json.dumps(proof, indent=2))
    print(json.dumps(proof))
finally:
    if p.poll() is None:
        os.killpg(p.pid, signal.SIGTERM)
        p.wait(10)
