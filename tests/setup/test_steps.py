import json

from fakes import FakeRunner
from lai_setup.detect import SystemInfo
from lai_setup.paths import resolve_paths
from lai_setup.report import Report
from lai_setup.state import InstallState
from lai_setup.steps import Context, Step, StepStatus, run_steps


class FileStep(Step):
    """Bir dosyayı yazan basit adım; çağrı sayılarını tutar."""

    def __init__(self, sid, name, fail=False):
        self.id, self.title, self.name, self.fail = sid, f"{sid} adımı", name, fail
        self.applied = 0

    def check(self, ctx):
        return (ctx.paths.root / self.name).exists()

    def apply(self, ctx):
        self.applied += 1
        if self.fail:
            raise RuntimeError("bilerek başarısız")
        ctx.paths.root.mkdir(parents=True, exist_ok=True)
        (ctx.paths.root / self.name).write_text("x")

    def verify(self, ctx):
        ok = (ctx.paths.root / self.name).exists()
        return ok, "var" if ok else "yok"


def _ctx(tmp_path, **kw):
    paths = resolve_paths("linux", {"LAI_HOME": str(tmp_path / "lai")}, tmp_path)
    return Context(paths=paths, info=SystemInfo.empty(os="linux"), runner=FakeRunner(),
                   state=InstallState.load(paths.state_file), **kw)


def test_second_install_skips_everything(tmp_path):
    steps = [FileStep("a", "a.txt"), FileStep("b", "b.txt")]
    r1 = run_steps(steps, _ctx(tmp_path), "install")
    assert [r.status for r in r1] == [StepStatus.APPLIED, StepStatus.APPLIED]
    r2 = run_steps(steps, _ctx(tmp_path), "install")
    assert [r.status for r in r2] == [StepStatus.SKIPPED, StepStatus.SKIPPED]
    assert [s.applied for s in steps] == [1, 1]


def test_doctor_never_applies(tmp_path):
    s = FileStep("a", "a.txt")
    res = run_steps([s], _ctx(tmp_path), "doctor")
    assert s.applied == 0
    assert res[0].status == StepStatus.FAILED
    assert not (tmp_path / "lai").exists()


def test_dry_run_plans_without_writing(tmp_path):
    s = FileStep("a", "a.txt")
    res = run_steps([s], _ctx(tmp_path, dry_run=True), "install")
    assert res[0].status == StepStatus.PLANNED
    assert s.applied == 0
    assert not (tmp_path / "lai").exists()


def test_failure_skips_following_steps(tmp_path):
    a, b = FileStep("a", "a.txt", fail=True), FileStep("b", "b.txt")
    res = run_steps([a, b], _ctx(tmp_path), "install")
    assert res[0].status == StepStatus.FAILED
    assert "bilerek" in res[0].message
    assert res[1].status == StepStatus.SKIPPED
    assert b.applied == 0


def test_repair_only_reapplies_broken(tmp_path):
    a, b = FileStep("a", "a.txt"), FileStep("b", "b.txt")
    run_steps([a, b], _ctx(tmp_path), "install")
    (tmp_path / "lai" / "b.txt").unlink()
    res = run_steps([a, b], _ctx(tmp_path), "repair")
    assert [r.status for r in res] == [StepStatus.OK, StepStatus.APPLIED]
    assert [a.applied, b.applied] == [1, 2]


def test_state_records_step_results_and_survives_reload(tmp_path):
    ctx = _ctx(tmp_path)
    run_steps([FileStep("a", "a.txt")], ctx, "install")
    ctx.state.save()
    st = InstallState.load(ctx.paths.state_file)
    assert st.steps["a"]["status"] == "APPLIED"
    assert oct(ctx.paths.state_file.stat().st_mode & 0o777) == "0o600"


def test_corrupt_state_does_not_crash(tmp_path):
    f = tmp_path / "install-state.json"
    f.write_text("{yarım json")
    st = InstallState.load(f)
    assert st.corrupt is True
    assert st.steps == {}
    st.save()
    assert json.loads(f.read_text())["steps"] == {}
    assert len(list(tmp_path.glob("install-state.json.corrupt-*"))) == 1


def test_report_written_utf8(tmp_path):
    ctx = _ctx(tmp_path)
    res = run_steps([FileStep("ş", "ğ.txt")], ctx, "install")
    rep = Report(action="install", info=ctx.info, results=res, pins={"model": "qwen3.5:4b"})
    j, t = rep.write(tmp_path / "rapor dizini İ")
    assert j.name.startswith("install-") and j.suffix == ".json"
    assert t.suffix == ".txt"
    data = json.loads(j.read_text(encoding="utf-8"))
    assert data["results"][0]["step_id"] == "ş"
    assert data["pins"]["model"] == "qwen3.5:4b"
    txt = t.read_text(encoding="utf-8")
    assert "ş adımı" in txt and "UYGULANDI" in txt


def test_report_without_steps_has_no_empty_section(tmp_path):
    rep = Report(action="detect", info=_ctx(tmp_path).info, results=[])
    assert "Adımlar" not in rep.to_text()
