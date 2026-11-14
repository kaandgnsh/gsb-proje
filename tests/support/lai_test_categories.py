"""pytest eklentisi: testleri iki kategoride raporlar.

- technical      : kod, entegrasyon, güvenlik, deterministik hesap. Başarısızlık = hata.
- model_quality  : modelin dil/akıl yürütme kalitesi. Başarısızlık = model sınırlaması
                   (kod hatası değildir; gizlenmez, ayrı raporlanır).
İşaretsiz testler `technical` sayılır.
"""
from collections import Counter

import pytest

CATEGORIES = ("technical", "model_quality")
_KEY = pytest.StashKey[Counter]()


def pytest_configure(config):
    config.stash[_KEY] = Counter()
    config.addinivalue_line("markers", "technical: kod/entegrasyon doğruluğu (varsayılan)")
    config.addinivalue_line("markers",
                            "model_quality: model kalite ölçümü; başarısızlık model sınırlamasıdır")


def category_of(item) -> str:
    return "model_quality" if item.get_closest_marker("model_quality") else "technical"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    rep = (yield).get_result()
    if rep.when == "call" or (rep.when == "setup" and not rep.passed):
        item.config.stash[_KEY][(category_of(item), rep.outcome)] += 1


def pytest_terminal_summary(terminalreporter, config):
    _counts = config.stash[_KEY]
    if not _counts:
        return
    terminalreporter.section("Test kategorileri")
    for cat in CATEGORIES:
        row = {o: _counts[(cat, o)] for o in ("passed", "failed", "skipped")}
        if sum(row.values()):
            note = "  (başarısızlık = model sınırlaması, kod hatası değil)" if cat == "model_quality" else ""
            terminalreporter.write_line(
                f"{cat:<14} geçti {row['passed']:>3}  başarısız {row['failed']:>3}  "
                f"atlandı {row['skipped']:>3}{note}")
