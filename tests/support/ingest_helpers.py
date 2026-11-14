"""İçe aktarma testleri için ortak yardımcılar."""
from lai.ingest.model import Locator, RawUnit


def U(*pages):
    """U(["satır", ...], ["2. sayfa satırı", ...]) → RawUnit listesi (PDF benzeri)."""
    out = []
    for pno, lines in enumerate(pages, start=1):
        off = 0
        for ln, t in enumerate(lines, start=1):
            out.append(RawUnit(len(out), t, Locator(page=pno, line=ln, char_start=off,
                                                    char_end=off + len(t))))
            off += len(t) + 1
    return out


# ------------------------------------------------------------------ fikstürler
import pytest  # noqa: E402

from lai.store.store import Store  # noqa: E402


@pytest.fixture
def store(tmp_path):
    s = Store.open(tmp_path / "data" / "lai.db", backup_dir=tmp_path / "data" / "backups")
    yield s
    s.close()


@pytest.fixture
def inventory_id(store):
    return store.inventory.create_inventory("Sentetik Envanter", "test")


@pytest.fixture
def docs_dir(tmp_path):
    return tmp_path / "data" / "documents"
