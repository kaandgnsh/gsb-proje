import pytest


@pytest.fixture(autouse=True)
def _model_isolation_for_fakes(request, monkeypatch):
    """Teknik testler sahte Ollama'yı yalıtım dışında kullanır; kapı burada sahte geçer.

    Gerçek kapı `real_guard` işaretli testlerde ve bütün gerçek/native kanıtlarda sınanır.
    """
    if request.node.get_closest_marker("real_guard"):
        return
    from lai_runtime import guard

    monkeypatch.setattr(guard, "_verified", lambda: True)
