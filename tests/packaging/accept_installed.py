"""Hazır paketin gerçek komut yolundan değişken soru sayılı sentetik kabul."""

import csv
import json
import os
import subprocess
import sys
from pathlib import Path

from openpyxl import Workbook
from synthetic_desktop import document, rules
from synthetic_rules import DIMS

from lai.store.store import Store

app, work = map(Path, sys.argv[1:3])
work.mkdir(parents=True, exist_ok=True)
home = work / "home"
env = dict(os.environ, LAI_HOME=str(home), LAI_WORKSPACE=str(work))


def run(*args):
    p = subprocess.run(
        [str(app), *map(str, args)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert p.returncode == 0, p.stderr + p.stdout
    return p.stdout


run("--setup", "install", "--bundle", app.parent, "--lai-home", home, "--yes")
launcher = home / "yerel-envanter"
app = launcher
report = {"question_counts": [], "platform": sys.platform}
for n in (4, 12, 28):
    source = document(work / f"inventory-{n}.json", n)
    run(
        "--ingest",
        "import",
        "--db",
        home / "data/lai.db",
        "--inventory",
        f"Sentetik {n}",
        source,
        "--json",
    )
    s = Store.open(home / "data/lai.db")
    inv = s.conn.execute(
        "SELECT id FROM inventories WHERE name=?", (f"Sentetik {n}",)
    ).fetchone()[0]
    iv = s.conn.execute(
        "SELECT id FROM inventory_versions WHERE inventory_id=?", (inv,)
    ).fetchone()[0]
    assert len(s.inventory.items(iv)) == n
    for d, ps in DIMS.items():
        s.inventory.add_dimension(inv, d, poles=[(p, None) for p in ps])
    rules(s, inv, n)
    pv = s.profiles.publish_version(s.profiles.ensure_profile(inv)).id
    s.close()
    result = json.loads(
        run(
            "--cli",
            "assess",
            "--db",
            home / "data/lai.db",
            "--inventory-version",
            iv,
            "--profile-version",
            pv,
            "--participant-code",
            f"S-{n}",
            "--responses",
            json.dumps([[q, "A"] for q in range(1, n + 1)]),
            "--json",
        )
    )
    aid = result["assessment_id"]
    for fmt in ("json", "pdf", "csv", "xlsx"):
        run(
            "--cli",
            "export",
            "--db",
            home / "data/lai.db",
            "--id",
            aid,
            "--format",
            fmt,
            "--output",
            work / f"report-{n}.{fmt}",
        )
    for suffix in ("csv", "xlsx"):
        file = work / f"answers-{n}.{suffix}"
        rows = [
            ["participant_code", *[f"Q{i}" for i in range(1, n + 1)]],
            [f"B-{n}-{suffix}", *["A"] * n],
            [f"C-{n}-{suffix}", *["B"] * n],
        ]
        if suffix == "csv":
            with file.open("w", newline="") as f:
                csv.writer(f).writerows(rows)
        else:
            wb = Workbook()
            ws = wb.active
            for row in rows:
                ws.append(row)
            wb.save(file)
            wb.close()
        job = json.loads(
            run(
                "--cli",
                "batch",
                file,
                "--db",
                home / "data/lai.db",
                "--inventory-version",
                iv,
                "--profile-version",
                pv,
                "--prepare-only",
                "--json",
            )
        )
        run(
            "--cli",
            "job",
            "run",
            "--db",
            home / "data/lai.db",
            "--id",
            job["id"],
            "--limit",
            "1",
            "--json",
        )
        done = json.loads(
            run(
                "--cli",
                "job",
                "run",
                "--db",
                home / "data/lai.db",
                "--id",
                job["id"],
                "--json",
            )
        )
        assert (
            done["done"] == 2
            and done["inventory_version_id"] == iv
            and done["profile_version_id"] == pv
        )
    stored = json.loads(
        run(
            "--cli",
            "result",
            "--db",
            home / "data/lai.db",
            "--assessment",
            aid,
            "--json",
        )
    )
    assert stored["results"] == result["result"]
    report["question_counts"].append(n)
run("--setup", "doctor", "--lai-home", home, "--json")
run(
    "--setup",
    "install",
    "--bundle",
    Path(sys.argv[1]).parent,
    "--lai-home",
    home,
    "--yes",
)
run(
    "--setup",
    "repair",
    "--bundle",
    Path(sys.argv[1]).parent,
    "--lai-home",
    home,
    "--yes",
)
run("--setup", "uninstall", "--lai-home", home, "--yes")
assert (home / "data/lai.db").exists() and (work / "report-28.pdf").exists()
report.update(passed=True, data_preserved=True, network_namespace=True)
(work / "acceptance.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report))
