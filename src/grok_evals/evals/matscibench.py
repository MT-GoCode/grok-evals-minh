"""MatSciBench eval: text-only subset, rule-based grading via vendored grader.

Strategy: hybrid (reimplement-locally + vendor subprocess grader).

  * Generation happens here using xai_chat (OpenAI-compatible).
  * Grading happens in vendor/MatSciBench/.venv via rule_grader_cli.py, which
    wraps evaluation.auto_judge.judge_response_with_rule (sympy + latex2sympy).
    That worker runs each comparison in its own process with a timeout, exactly
    matching the original paper's pipeline.

Why hybrid: the vendor's eval.py drives generation + grading together, but it
hard-codes file I/O, parallelism, and ``methods.*`` plumbing that's awkward to
parse back. Generation through xai_chat is one line; only the rule judge has
nontrivial deps (sympy / latex2sympy2 / regex), so we keep that in the vendor
venv and shell out.
"""
from __future__ import annotations

import concurrent.futures
import csv
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Iterable

from .._paths import DATA_DIR, ROOT, VENDOR_DIR
from ..base import Eval, EvalMeta, TaskResult
from ..clients import xai_chat
from ..registry import register

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_VENDOR_ROOT = VENDOR_DIR / "MatSciBench"
_VENDOR_PY = _VENDOR_ROOT / ".venv" / "bin" / "python"
_VENDOR_GRADER_CLI = _VENDOR_ROOT / "rule_grader_cli.py"
_QA_CSV = _VENDOR_ROOT / "datasets" / "MatSciBench" / "qa.csv"
_CACHE_DIR = DATA_DIR / "matscibench"

# ---------------------------------------------------------------------------
# Prompt (verbatim from vendor/MatSciBench/methods/prompts.py::SYSTEM_PROMPT)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a renowned materials science engineering professor with extensive knowledge in the field. "
    "Your students have presented you with a challenging question related to materials science. "
    "Please reason step by step, and put the final answer inside a single box using \\boxed{...}. "
    "Include only the final answer inside the box, without the unit."
)


# ---------------------------------------------------------------------------
# Helpers (mirrors of vendor/MatSciBench/utils/utils.py::extract_final_answer)
# ---------------------------------------------------------------------------

def _extract_final_answer(response: str) -> str:
    """Extract the contents of the last \\boxed{...} in the response, brace-balanced."""
    if not response:
        return ""
    match_index = response.rfind("oxed{")
    if match_index == -1:
        return ""
    start_index = match_index + len("oxed{")
    brace_count = 1
    end_index = start_index
    while brace_count > 0 and end_index < len(response):
        if response[end_index] == "{":
            brace_count += 1
        elif response[end_index] == "}":
            brace_count -= 1
        end_index += 1
        if brace_count == 0:
            break
    if brace_count > 0:
        return ""
    return response[start_index : end_index - 1].strip()


def _is_text_only(image_field: object) -> bool:
    """Match vendor eval.py's text-only filter."""
    if image_field is None:
        return True
    s = str(image_field).strip()
    if not s or s.lower() == "nan":
        return True
    return False


def _load_rows() -> list[dict]:
    """Load text-only rows from the vendored qa.csv, keyed by qid."""
    if not _QA_CSV.exists():
        raise FileNotFoundError(
            f"Vendored qa.csv missing at {_QA_CSV}. "
            "Run the MatSciBench setup script or clone the repo into vendor/."
        )
    rows: list[dict] = []
    with _QA_CSV.open("r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if _is_text_only(row.get("image")):
                rows.append(row)
    return rows


def _build_prompt(row: dict) -> str:
    """Mirror methods/base.py::base — append unit hint when present."""
    question_text = row["question"]
    unit = (row.get("unit") or "").strip()
    if unit:
        if row.get("number_of_answers") == "single":
            question_text += f"The unit of the answer is {unit}."
        elif row.get("number_of_answers") == "multiple":
            question_text += f"The units of each required answer are {unit}, respectively."
        # else: leave untouched
    return question_text


# ---------------------------------------------------------------------------
# Eval
# ---------------------------------------------------------------------------

class MatSciBenchEval(Eval):
    meta = EvalMeta(
        name="matscibench",
        area="science",
        description=(
            "Materials science benchmark: 1340 college-level problems "
            "(~1025 text-only), 4 fields (Materials/Properties/Fundamental/Structures), "
            "NUM/FORMULA. Smoke runs the text-only subset."
        ),
        paper_url="https://arxiv.org/abs/2510.12171",
        dataset_url="https://huggingface.co/datasets/MatSciBench/MatSciBench",
        grading=(
            "rule-based (math_equal via sympy + latex2sympy + numeric tolerance); "
            "LLM judge available but not used for smoke."
        ),
        notes=(
            "Text-only subset only for this smoke. Vendor harness at vendor/MatSciBench/ "
            "with isolated .venv. Generation in-process via xai_chat; rule judge runs "
            "in vendor venv via rule_grader_cli.py."
        ),
    )
    model = "grok-4.3"

    # ---------------- data ----------------

    def _rows(self) -> list[dict]:
        # Cache on the instance to avoid re-reading the CSV across ids() and run().
        rows = getattr(self, "_rows_cache", None)
        if rows is None:
            rows = _load_rows()
            self._rows_cache = rows
        return rows

    def ids(self, limit: int | None = None) -> list[str]:
        ids = [r["qid"] for r in self._rows()]
        if limit is not None:
            ids = ids[:limit]
        return ids

    # ---------------- run ----------------

    def _generate(self, row: dict, max_tokens: int) -> dict:
        """Single API call to xAI. Returns the raw dict plus extracted fields."""
        question_text = _build_prompt(row)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": question_text},
        ]
        try:
            resp = xai_chat(
                messages,
                model=self.model,
                max_tokens=max_tokens,
                temperature=0.0,
            )
            completion = (resp["choices"][0]["message"].get("content") or "").strip()
            usage = resp.get("usage", {}) or {}
            new_tokens = usage.get("completion_tokens", 0)
            err = None
        except Exception as e:  # network / API errors
            resp = {"error": str(e)}
            completion = ""
            new_tokens = 0
            err = str(e)

        final_answer = _extract_final_answer(completion)
        return {
            "qid": row["qid"],
            "question_text": question_text,
            "completion": completion,
            "final_answer": final_answer,
            "correct_answer": (str(row.get("answer") or "")).strip(),
            "unit": (row.get("unit") or "").strip(),
            "number_of_answers": row.get("number_of_answers") or "",
            "question_type": row.get("type") or "",
            "domain": row.get("primary_category") or "",
            "new_token_nums": new_tokens,
            "error": err,
            "raw_response": resp,
        }

    def _grade(self, items: list[dict]) -> dict[str, dict]:
        """Run the vendored rule judge over all items in a single subprocess.

        Returns a map qid -> {is_correct: bool, judge_reasoning: str}.
        """
        if not _VENDOR_PY.exists():
            raise FileNotFoundError(
                f"Vendor venv python missing at {_VENDOR_PY}. "
                "Run: cd vendor/MatSciBench && uv venv -p 3.12 .venv && "
                "uv pip install --python .venv/bin/python (deps...)"
            )
        if not _VENDOR_GRADER_CLI.exists():
            raise FileNotFoundError(f"rule_grader_cli.py missing at {_VENDOR_GRADER_CLI}")

        payload = {
            "timeout": 30,
            "items": [
                {
                    "qid": it["qid"],
                    "final_answer": it["final_answer"],
                    "correct_answer": it["correct_answer"],
                    "multiple": (it.get("number_of_answers") == "multiple"),
                }
                for it in items
            ],
        }
        proc = subprocess.run(
            [str(_VENDOR_PY), str(_VENDOR_GRADER_CLI)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=str(_VENDOR_ROOT),
            check=False,
            timeout=60 * max(1, len(items)),  # generous; rule judge has its own per-item timeout
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"grader subprocess failed (rc={proc.returncode}). "
                f"stderr (tail): {proc.stderr[-500:]}"
            )
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"grader returned non-JSON: {e}. stdout (tail): {proc.stdout[-500:]}"
            ) from e
        return {r["qid"]: r for r in data.get("results", [])}

    def run(self, ids: Iterable[str]) -> Iterable[TaskResult]:
        chosen_ids = list(ids)
        by_qid = {r["qid"]: r for r in self._rows()}
        missing = [qid for qid in chosen_ids if qid not in by_qid]
        if missing:
            raise KeyError(f"Unknown text-only qids: {missing[:5]}{'...' if len(missing) > 5 else ''}")

        rows = [by_qid[qid] for qid in chosen_ids]

        # ---------------- generation (parallel) ----------------
        # Modest concurrency: xAI handles a few in parallel fine; keeps cost bounded for smoke.
        max_workers = min(8, max(1, len(rows)))
        gens: list[dict] = [None] * len(rows)  # type: ignore[list-item]
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(self._generate, row, 8192): i for i, row in enumerate(rows)}
            for fut in concurrent.futures.as_completed(futures):
                i = futures[fut]
                gens[i] = fut.result()

        # ---------------- rule judging (single subprocess) ----------------
        grades = self._grade(gens)

        for g in gens:
            grade = grades.get(g["qid"], {"is_correct": False, "judge_reasoning": "no grade returned"})
            score = 1.0 if grade.get("is_correct") else 0.0
            # If generation errored, mark score None so it isn't counted in mean.
            if g.get("error"):
                score = None  # type: ignore[assignment]

            yield TaskResult(
                task_id=g["qid"],
                completion=g["completion"],
                score=score,
                raw={
                    "final_answer": g["final_answer"],
                    "correct_answer": g["correct_answer"],
                    "question_type": g["question_type"],
                    "number_of_answers": g["number_of_answers"],
                    "domain": g["domain"],
                    "unit": g["unit"],
                    "judge_reasoning": grade.get("judge_reasoning", ""),
                    "is_correct": bool(grade.get("is_correct", False)),
                    "new_token_nums": g["new_token_nums"],
                    "error": g["error"],
                },
            )


# Register
register("matscibench", lambda: MatSciBenchEval())
