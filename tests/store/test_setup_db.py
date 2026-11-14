import hashlib
import json
import time
from pathlib import Path

import pytest

from lai.store.db import LATEST_VERSION
from lai.store.store import Store
from lai_setup.cli import main
from lai_setup.runner import SystemRunner
from synthetic import load_into, make_inventory, make_responses

FIX = Path(__file__).resolve().parents[1] / "setup" / "fixtures" / "fsroot_arch"


def _no_http(url, timeout):
    raise OSError("ağ yok")


def run(argv, home, tty=False, answers=()):
    it = iter(answers)
    return main(argv, env={"LAI_HOME": str(home), "HOME": str(home.parent), "PATH": "/usr/bin:/bin"},
                runner=SystemRunner(), isatty=tty, input_fn=lambda p="": next(it), fs_root=FIX,
                http_get=_no_http)


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "lai kök İ"
    req = tmp_path / "req.txt"
    req.write_text("")
    (tmp_path / "bos" / "wheels").mkdir(parents=True)
    args = ["install", "--skip-ai", "--offline", str(tmp_path / "bos"), "--requirements", str(req)]
    assert run(args, h) == 0
    return h, args


def _state(h):
    return json.loads((h / "install-state.json").read_text(encoding="utf-8"))


def _tree(root: Path):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_install_creates_database_idempotently(home):
    h, args = home
    db = h / "data" / "lai.db"
    st = _state(h)
    assert st["steps"]["database"]["status"] == "APPLIED"
    assert st["pins"]["schema_version"] == str(LATEST_VERSION)
    assert oct(db.stat().st_mode & 0o777) == "0o600"
    sha = hashlib.sha256(db.read_bytes()).hexdigest()
    assert run(args, h) == 0
    assert _state(h)["steps"]["database"]["status"] == "SKIPPED"
    assert hashlib.sha256(db.read_bytes()).hexdigest() == sha


def test_doctor_is_read_only_for_database(home):
    h, _ = home
    data = h / "data"
    before = _tree(data)
    assert run(["doctor"], h) == 0
    assert _tree(data) == before


def test_doctor_reports_corrupt_database(home, capsys):
    h, _ = home
    (h / "data" / "lai.db").write_bytes(b"bozuk" * 1000)
    assert run(["doctor"], h) == 1
    assert "Veritabanı" in capsys.readouterr().out


def test_db_backup_status_and_restore(home, capsys):
    h, _ = home
    db = h / "data" / "lai.db"
    s = Store.open(db)
    s.inventory.create_inventory("Yedekten önce")
    s.close()
    assert run(["db", "backup"], h) == 0
    backups = sorted((h / "data" / "backups").glob("lai-*-manual.db"))
    assert len(backups) == 1
    s = Store.open(db)
    s.inventory.create_inventory("Yedekten sonra")
    s.close()
    assert run(["db", "restore", str(backups[0])], h) == 2  # etkileşimsiz, --yes yok
    assert run(["db", "restore", str(backups[0]), "--yes"], h) == 0
    s = Store.open(db)
    assert [r[0] for r in s.conn.execute("SELECT name FROM inventories")] == ["Yedekten önce"]
    s.close()
    capsys.readouterr()
    assert run(["db", "status"], h) == 0
    out = capsys.readouterr().out
    assert f"v{LATEST_VERSION}" in out and "bütünlük: ok" in out


def test_restore_refused_while_database_in_use(home):
    h, _ = home
    db = h / "data" / "lai.db"
    assert run(["db", "backup"], h) == 0
    b = next((h / "data" / "backups").glob("lai-*-manual.db"))
    s = Store.open(db)
    with s.transaction():  # başka bir süreç/uygulama yazıyor
        s.inventory.create_inventory("açık işlem")
        assert run(["db", "restore", str(b), "--yes"], h) == 1
    s.close()


def test_uninstall_keeps_database_and_backups(home):
    h, _ = home
    assert run(["db", "backup"], h) == 0
    assert run(["uninstall", "--yes"], h) == 0
    assert (h / "data" / "lai.db").exists()
    assert list((h / "data" / "backups").glob("*.db"))


def test_synthetic_scale_400_items_200_participants(tmp_path):
    s = Store.open(tmp_path / "lai.db")
    inv = make_inventory(400)
    t = time.perf_counter()
    iid, ver = load_into(s, inv)
    with s.transaction():
        for r in make_responses(400, 200):
            p = s.participants.create()
            s.participants.add_response_set(p.id, ver, r)
    elapsed = time.perf_counter() - t
    assert len(s.inventory.items(ver)) == 400
    assert s.conn.execute("SELECT COUNT(*) FROM responses").fetchone()[0] == 80_000
    assert s.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert s.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    print(f"\n400 madde + 200×400 cevap: {elapsed:.2f} sn")
    assert elapsed < 10
    s.close()
