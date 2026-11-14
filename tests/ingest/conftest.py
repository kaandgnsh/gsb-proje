import pytest

from lai.store.store import Store


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
