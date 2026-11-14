pytest_plugins = ["pytester"]


def test_category_summary_separates_model_quality(pytester):
    pytester.makepyfile("""
        import pytest
        def test_kod(): assert True
        def test_kod_hata(): assert False
        @pytest.mark.model_quality
        def test_model_zayif(): assert False
        @pytest.mark.model_quality
        def test_model_iyi(): assert True
    """)
    r = pytester.runpytest("-p", "lai_test_categories")
    r.assert_outcomes(passed=2, failed=2)
    out = r.stdout.str()
    assert "technical      geçti   1  başarısız   1" in out
    assert "model_quality  geçti   1  başarısız   1" in out
    assert "model sınırlaması" in out
