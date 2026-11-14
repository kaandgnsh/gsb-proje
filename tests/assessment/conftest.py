import csv

import pytest
from synthetic_rules import DIMS, truth_rules

from lai.core.rules import rule_key
from lai.store.store import Store


def build_store(path, n=8):
    s = Store.open(path)
    inv = s.inventory.create_inventory("Sentetik M8")
    ver = s.inventory.add_version(inv)
    s.inventory.add_items(
        ver,
        [
            {
                "number": i,
                "text": f"Sentetik {i}",
                "options": [("A", "Bir"), ("B", "İki")],
            }
            for i in range(1, n + 1)
        ],
    )
    for d, ps in DIMS.items():
        s.inventory.add_dimension(inv, d, poles=[(p, None) for p in ps])
    rules = truth_rules(n, set(range(7, n + 1, 7))) + [
        ("missing_answer_policy", {"dimension": "*", "policy": "skip"})
    ]
    for t, b in rules:
        s.knowledge.apply_user_correction(
            inv,
            entry_key=rule_key(t, b),
            content={"body": b},
            rule_type=t,
            dimension_code=b["dimension"],
            item_number=b.get("item"),
            summary="Sentetik",
            explanation="Açık sentetik kural",
            user_text="Onay",
        )
    pid = s.profiles.ensure_profile(inv)
    pv = s.profiles.publish_version(pid)
    return s, inv, ver, pid, pv.id


@pytest.fixture
def saved(tmp_path):
    data = build_store(tmp_path / "synthetic.db")
    yield data
    data[0].close()


def csv_file(path, n=8, rows=None, headers=None):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers or ["participant_id", *[f"Q{i}" for i in range(1, n + 1)]])
        w.writerows(
            rows if rows is not None else [[f"SYN-{j}", *["A"] * n] for j in range(3)]
        )
    return path


@pytest.fixture
def response_file(tmp_path):
    return csv_file(tmp_path / "sentetik.csv")
