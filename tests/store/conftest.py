import pytest

from lai.store.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store.open(tmp_path / "data" / "lai.db", backup_dir=tmp_path / "data" / "backups")
    yield s
    s.close()


@pytest.fixture
def inv(store):
    iid = store.inventory.create_inventory("Sentetik Envanter", "test")
    ver = store.inventory.add_version(iid, label="v1")
    return iid, ver
