"""400 maddelik envanter + uzun yönerge + 50 örnek: keşif hattının doğruluğu ve süresi."""
import json
import time

import pytest
from fake_llm import ScriptedLLM, oracle
from synthetic_docs import make_doc_inventory, write_guide_pdf, write_pdf
from synthetic_rules import guide_statements, make_examples

from lai.ingest.service import import_document, import_reference_document
from lai.learning.discovery import discover_rules
from lai.learning.examples import import_examples

N = 400
REV = set(range(7, N + 1, 7))  # 57 ters madde
STATEMENTS = guide_statements(N, REV)


@pytest.fixture
def big(store, inventory_id, docs_dir, tmp_path):
    t = time.perf_counter()
    import_document(store, inventory_id, write_pdf(make_doc_inventory(N), tmp_path / "e.pdf"),
                    documents_dir=docs_dir)
    import_reference_document(store, inventory_id,
                              write_guide_pdf(tmp_path / "y.pdf", [s for s, _, _ in STATEMENTS]),
                              documents_dir=docs_dir)
    ex = tmp_path / "ö.json"
    ex.write_text(json.dumps(make_examples(N, 50, REV, force_tie=True), ensure_ascii=False))
    import_examples(store, inventory_id, ex, documents_dir=docs_dir)
    return time.perf_counter() - t


def test_400_items_all_true_rules_found_and_strongly_inferred(store, inventory_id, big):
    llm = ScriptedLLM(oracle(STATEMENTS))
    t = time.perf_counter()
    rep = discover_rules(store, inventory_id, llm)
    elapsed = time.perf_counter() - t
    assert rep.status == "completed" and rep.rejected == 0, rep.rejections[:3]
    cur = store.knowledge.current(inventory_id, kind="rule")
    assert len(cur) == len(STATEMENTS) == 20 + len(REV)
    assert {e.confidence for e in cur} == {"STRONGLY_INFERRED"}
    assert rep.chunk_count == len(llm.calls) > 10
    assert elapsed < 20, f"{elapsed:.1f} sn"
    print(f"\n[ölçek] içe aktarma {big:.2f} sn, keşif (LLM hariç) {elapsed:.2f} sn, "
          f"{rep.chunk_count} parça, {rep.candidate_count} öneri")


def test_400_items_one_missed_reverse_flags_only_its_dimension(store, inventory_id, big):
    missed = [s for s in STATEMENTS if s[1] == "reverse" and s[2]["item"] == 14]
    rep = discover_rules(store, inventory_id,
                         ScriptedLLM(oracle([s for s in STATEMENTS if s not in missed])))
    cur = {e.entry_key: e.confidence for e in store.knowledge.current(inventory_id, kind="rule")}
    d = missed[0][2]["dimension"]
    assert cur[f"rule:default_option_map:dim:{d}"] == "WEAKLY_INFERRED"
    reviews = store.conn.execute("SELECT dimension_code,detail FROM dimension_reviews WHERE fit_run_id=?",
                                 (rep.fit.fit_run_id,)).fetchall()
    assert {r[0] for r in reviews} == {d}
    assert f"rule:default_option_map:dim:{d}" in reviews[0][1]
    assert cur[f"rule:threshold:dim:{d}"] == "STRONGLY_INFERRED"  # karar ayrı sınandı
    others = [k for k, v in cur.items() if not k.endswith(f"dim:{d}")]
    assert all(cur[k] == "STRONGLY_INFERRED" for k in others)
    assert rep.status == "completed"
