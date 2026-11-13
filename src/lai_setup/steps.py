"""İdempotent kurulum adımları ve çalıştırıcı."""
from __future__ import annotations

import enum
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .detect import SystemInfo
from .paths import AppPaths
from .runner import CommandRunner
from .state import InstallState

Action = Literal["install", "repair", "doctor"]
Mode = Literal["online", "offline", "none"]


class StepStatus(enum.Enum):
    OK = "OK"
    APPLIED = "APPLIED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    PLANNED = "PLANNED"


STATUS_TR = {StepStatus.OK: "SAĞLAM", StepStatus.APPLIED: "UYGULANDI",
             StepStatus.SKIPPED: "ATLANDI", StepStatus.FAILED: "HATA",
             StepStatus.PLANNED: "PLANLANDI"}


@dataclass
class StepResult:
    step_id: str
    title: str
    status: StepStatus
    message: str = ""
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"step_id": self.step_id, "title": self.title, "status": self.status.value,
                "message": self.message, "details": self.details}


@dataclass
class Context:
    paths: AppPaths
    info: SystemInfo
    runner: CommandRunner
    state: InstallState
    mode: Mode = "none"
    offline_dir: Path | None = None
    dry_run: bool = False
    assume_yes: bool = False
    interactive: bool = False
    requirements_file: Path | None = None
    python: str = ""


class Step(ABC):
    id: str
    title: str

    @abstractmethod
    def check(self, ctx: Context) -> bool:
        """Adım zaten tamamlanmış mı? Yan etkisiz."""

    @abstractmethod
    def apply(self, ctx: Context) -> None:
        """Adımı uygular. Hata durumunda istisna fırlatır."""

    @abstractmethod
    def verify(self, ctx: Context) -> tuple[bool, str]:
        """Yan etkisiz doğrulama: (başarılı mı, açıklama)."""

    def details(self, ctx: Context) -> dict:
        return {}


def _record(ctx: Context, res: StepResult) -> None:
    if ctx.dry_run or res.status in (StepStatus.PLANNED,):
        return
    entry = dict(ctx.state.steps.get(res.step_id, {}))
    entry.update(status=res.status.value, message=res.message,
                 at=time.strftime("%Y-%m-%dT%H:%M:%S"), **res.details)
    ctx.state.steps[res.step_id] = entry


def run_steps(steps: list[Step], ctx: Context, action: Action) -> list[StepResult]:
    results: list[StepResult] = []
    failed = False
    for step in steps:
        if failed:
            results.append(StepResult(step.id, step.title, StepStatus.SKIPPED,
                                      "önceki adım başarısız olduğu için atlandı"))
            continue
        try:
            res = _run_one(step, ctx, action)
        except Exception as exc:  # adım hatası raporlanır, kurulum çökmez
            res = StepResult(step.id, step.title, StepStatus.FAILED,
                             f"{type(exc).__name__}: {exc}")
        if action != "doctor":
            _record(ctx, res)
        results.append(res)
        failed = res.status == StepStatus.FAILED
    return results


def _run_one(step: Step, ctx: Context, action: Action) -> StepResult:
    if action == "doctor":
        ok, msg = step.verify(ctx)
        return StepResult(step.id, step.title, StepStatus.OK if ok else StepStatus.FAILED, msg)

    if action == "install" and step.check(ctx):
        return StepResult(step.id, step.title, StepStatus.SKIPPED, "zaten tamam")
    if action == "repair":
        ok, msg = step.verify(ctx)
        if ok:
            return StepResult(step.id, step.title, StepStatus.OK, msg)
    if ctx.dry_run:
        return StepResult(step.id, step.title, StepStatus.PLANNED, "uygulanacak (deneme modu)")
    step.apply(ctx)
    ok, msg = step.verify(ctx)
    status = StepStatus.APPLIED if ok else StepStatus.FAILED
    return StepResult(step.id, step.title, status, msg, step.details(ctx))
