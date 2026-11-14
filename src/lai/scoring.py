"""M8 için salt okunur depo → saf puanlama bağdaştırıcısı; kayıt/toplu iş/UI içermez."""

from __future__ import annotations

from .core.scoring import (
    ExamVersion,
    ScoringInputError,
    ScoringProfile,
    score_assessment,
)


def evaluate(store, *, inventory_version_id: int, profile_version_id: int, responses):
    """Seçilen sürümleri yükler. Güncel kurallara veya AI'ya bakmaz, DB'ye yazmaz."""
    row = store.conn.execute(
        "SELECT inventory_id FROM inventory_versions WHERE id=?",
        (inventory_version_id,),
    ).fetchone()
    if row is None:
        raise ScoringInputError("Sınav sürümü bulunamadı")
    version = store.profiles.get_version(profile_version_id)
    inventory = store.conn.execute(
        "SELECT inventory_id FROM scoring_profiles WHERE id=?", (version.profile_id,)
    ).fetchone()[0]
    snapshot = version.snapshot
    bound_version = snapshot.get("inventory_version_id", inventory_version_id)
    order = tuple(
        snapshot.get("dimension_order")
        or [
            e["content"]["body"]["dimension"]
            for e in sorted(snapshot["rules"], key=lambda e: e["entry_id"])
            if e["rule_type"] == "dimension_def"
        ]
    )
    exam = ExamVersion(
        inventory_version_id,
        row[0],
        {
            it["number"]: tuple(c for c, _ in it["options"])
            for it in store.inventory.items(inventory_version_id)
        },
        order,
    )
    profile = ScoringProfile(
        version.id,
        inventory,
        bound_version,
        snapshot,
        version.provisional or snapshot.get("snapshot_format") != 2,
    )
    return score_assessment(exam, profile, responses)
