"""Eval ABC. Each eval implements: metadata + ids() + run(ids) -> JSONL."""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ._paths import RESULTS_DIR


@dataclass
class EvalMeta:
    name: str
    area: str  # "science" | "pedagogy" | "mobile"
    description: str
    paper_url: str
    dataset_url: str
    grading: str  # short description of grading method
    notes: str = ""


@dataclass
class TaskResult:
    task_id: str
    completion: str
    score: float | None
    raw: dict = field(default_factory=dict)


class Eval(ABC):
    """Minimal contract. Subclasses do the heavy lifting; CLI just dispatches."""

    meta: EvalMeta
    model: str = "grok-4.3"

    @abstractmethod
    def ids(self, limit: int | None = None) -> list[str]:
        ...

    @abstractmethod
    def run(self, ids: Iterable[str]) -> Iterable[TaskResult]:
        ...

    def aggregate(self, results: list[TaskResult]) -> dict:
        scores = [r.score for r in results if r.score is not None]
        return {
            "n": len(results),
            "n_scored": len(scores),
            "mean_score": sum(scores) / len(scores) if scores else None,
        }

    def write_jsonl(self, results: list[TaskResult], tag: str = "") -> Path:
        ts = time.strftime("%Y%m%d-%H%M%S")
        slug = self.meta.name.replace("/", "_")
        out_dir = RESULTS_DIR / slug
        out_dir.mkdir(parents=True, exist_ok=True)
        suffix = f"_{tag}" if tag else ""
        out = out_dir / f"{ts}{suffix}.jsonl"
        with out.open("w") as f:
            for r in results:
                f.write(json.dumps({
                    "task_id": r.task_id,
                    "completion": r.completion,
                    "score": r.score,
                    "raw": r.raw,
                }) + "\n")
        agg = self.aggregate(results)
        with (out_dir / f"{ts}{suffix}.summary.json").open("w") as f:
            json.dump({"model": self.model, "meta": self.meta.__dict__, "aggregate": agg}, f, indent=2)
        return out
