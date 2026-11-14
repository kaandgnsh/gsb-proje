"""M9 için yalnız sentetik envanter ve doğrulanmış profil kuralları."""

from pathlib import Path

from synthetic_docs import make_doc_inventory, write_json
from synthetic_rules import DIMS, truth_rules

from lai.core.rules import rule_key
from lai.ingest.service import import_document
from lai.store.store import Store


def document(path, n=8):
    inv = make_doc_inventory(n)
    for item in inv["items"]:
        item["options"] = item["options"][:2]
    return write_json(inv, Path(path))


def rules(store, inventory, n=8, *, missing_policy=False):
    for t, b in truth_rules(n, {7} if n >= 7 else set()):
        store.knowledge.apply_user_correction(
            inventory,
            entry_key=rule_key(t, b),
            content={"body": b},
            rule_type=t,
            dimension_code=b["dimension"],
            item_number=b.get("item"),
            summary="Sentetik açık kural",
            explanation="El hesabı için sentetik kural",
            user_text="Doğruluyorum",
        )
    if not missing_policy:
        b = {"dimension": "*", "policy": "skip"}
        store.knowledge.apply_user_correction(
            inventory,
            entry_key=rule_key("missing_answer_policy", b),
            content={"body": b},
            rule_type="missing_answer_policy",
            dimension_code="*",
            summary="Cevapsız madde atlanır",
            explanation="Sentetik",
            user_text="Atla",
        )


def ready(path, *, missing_policy=False):
    store = Store.open(Path(path))
    inv = store.inventory.create_inventory("Sentetik Türkçe Envanter")
    doc = document(Path(path).parent / "envanter.json")
    rep = import_document(
        store, inv, doc, documents_dir=Path(path).parent / "documents"
    )
    for d, ps in DIMS.items():
        store.inventory.add_dimension(inv, d, poles=[(p, None) for p in ps])
    rules(store, inv, missing_policy=missing_policy)
    pid = store.profiles.ensure_profile(inv)
    pv = store.profiles.publish_version(pid)
    store.close()
    return inv, rep.inventory_version_id, pid, pv.id
