"""TutorBench eval module.

ScaleAI/TutorBench: 3 tutoring use cases (Adaptive Explanation, Assessment &
Feedback, Active Learning Support) across 6 STEM subjects, both text-only and
multimodal. Each task has a list of human-authored rubric criteria with
severity weights in {critical=5, not_critical=1, critical_negative=-5}; final
score is the weighted average rubric rating (ARR_w) per paper eq. (1) with
the numerator clipped to >=0.

Model under test: Grok 4.3 (xAI chat completions, OpenAI-compatible).
Judge: Claude Sonnet 4.5 (per-rubric, strict pass/fail with JSON output).
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from tqdm import tqdm

from .._paths import DATA_DIR
from ..base import Eval, EvalMeta, TaskResult
from ..clients import anthropic_message, xai_chat
from ..registry import register

# ---------------------------------------------------------------------------
# System prompts (verbatim from paper Appendix A.6)
# ---------------------------------------------------------------------------

SYS_UC1 = (
    "You are an AI tutor helping a high school student understand a concept. "
    "Answer their question clearly and adjust your explanation based on what "
    "the student says they're confused about."
)

SYS_UC2_TEXT = (
    "You are an AI tutor reviewing a student's answer to a question. Evaluate "
    "whether it is correct, identify any mistakes, and explain your reasoning "
    "clearly. Provide an assessment of the student incorrect solution in the "
    "first response"
)

SYS_UC2_MM = (
    "You are an AI tutor reviewing a student's answer to a question. Evaluate "
    "whether it is correct, identify any mistakes, and explain your reasoning "
    "clearly. Provide an assessment of the student incorrect solution present "
    "in the image."
)

SYS_UC3_TEXT = (
    "You are an AI tutor helping a student who got stuck partway through a "
    "problem. Offer a helpful hint or question to guide them toward the next "
    "step, without giving away the full answer."
)

SYS_UC3_MM = (
    "You are an AI tutor helping a student who got stuck partway through a "
    "problem. Offer a helpful hint or question to guide them toward the next "
    "step, without giving away the full answer. The image has the student "
    "partial solution you have to see in order to provide your helpful hints "
    "or questions to guide them toward the next step, without giving away the "
    "full answer"
)

# Weight mapping per paper §2.3.
WEIGHTS: dict[str, int] = {
    "critical": 5,
    "not_critical": 1,
    "critical_negative": -5,
}

JUDGE_MODEL = "claude-sonnet-4-5-20250929"

# Cache directories
CACHE_DIR = DATA_DIR / "tutorbench" / "cache"
COMP_CACHE = CACHE_DIR / "completions"
JUDGE_CACHE = CACHE_DIR / "judgments"
for _d in (COMP_CACHE, JUDGE_CACHE):
    _d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash(obj: Any) -> str:
    return hashlib.sha1(
        json.dumps(obj, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def _image_b64(img: Any) -> str:
    """PIL Image -> base64 PNG data string."""
    buf = io.BytesIO()
    # Convert to RGB to ensure JPEG/PNG compatibility regardless of source mode.
    if img.mode not in ("RGB", "RGBA", "L"):
        img = img.convert("RGB")
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _user_content(text: str, image_b64: str | None) -> Any:
    """Construct a user message content (str or list-of-parts for multimodal)."""
    if not image_b64:
        return text
    return [
        {"type": "text", "text": text},
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{image_b64}"},
        },
    ]


# ---------------------------------------------------------------------------
# Message construction per use case
# ---------------------------------------------------------------------------


def build_messages(row: dict, image_b64: str | None) -> tuple[str, list[dict]]:
    """Return (system_prompt, messages) for the model under test.

    BATCH values:
      USE_CASE_1_TEXT / USE_CASE_1_MULTIMODAL  -> Adaptive Explanation
      USE_CASE_2_TEXT / USE_CASE_2_MULTIMODAL  -> Assessment & Feedback
      USE_CASE_3_TEXT / USE_CASE_3_MULTIMODAL  -> Active Learning Support
    """
    batch = row["BATCH"]
    prompt = row["PROMPT"] or ""
    initial = row.get("UC1_INITIAL_EXPLANATION") or ""
    follow_up = row.get("FOLLOW_UP_PROMPT") or ""

    if batch.startswith("USE_CASE_1"):
        # Multi-turn: student question -> tutor initial explanation -> follow-up.
        sysp = SYS_UC1
        messages = [
            {"role": "user", "content": _user_content(prompt, image_b64)},
            {"role": "assistant", "content": initial},
            {"role": "user", "content": follow_up},
        ]
        return sysp, messages

    if batch.startswith("USE_CASE_2"):
        sysp = SYS_UC2_MM if batch.endswith("MULTIMODAL") else SYS_UC2_TEXT
        if batch.endswith("MULTIMODAL"):
            # Student's incorrect solution is in the image attached to the user msg.
            user_text = prompt
            messages = [
                {"role": "user", "content": _user_content(user_text, image_b64)},
            ]
        else:
            # Text: PROMPT is the question; FOLLOW_UP_PROMPT carries the student's
            # solution to assess (verified empirically against the dataset).
            user_text = (
                f"Question:\n{prompt}\n\n"
                f"Student's solution:\n{follow_up}\n\n"
                "Please assess the student's solution above."
            )
            messages = [{"role": "user", "content": user_text}]
        return sysp, messages

    if batch.startswith("USE_CASE_3"):
        sysp = SYS_UC3_MM if batch.endswith("MULTIMODAL") else SYS_UC3_TEXT
        if batch.endswith("MULTIMODAL"):
            user_text = prompt
            messages = [
                {"role": "user", "content": _user_content(user_text, image_b64)},
            ]
        else:
            user_text = (
                f"Problem:\n{prompt}\n\n"
                f"Student's work so far:\n{follow_up}\n\n"
                "Please give a helpful hint to guide the student to the next step "
                "without revealing the full answer."
            )
            messages = [{"role": "user", "content": user_text}]
        return sysp, messages

    raise ValueError(f"unknown BATCH: {batch}")


# ---------------------------------------------------------------------------
# Model under test
# ---------------------------------------------------------------------------


def _system_message(sysp: str) -> dict:
    return {"role": "system", "content": sysp}


def call_model(
    task_id: str,
    sys_prompt: str,
    messages: list[dict],
    model: str,
    max_tokens: int = 2048,
) -> str:
    full_msgs = [_system_message(sys_prompt), *messages]
    # cache key excludes image b64 (already encoded into messages); hashing the
    # full list lets us memoize correctly across runs.
    key = _hash({"model": model, "msgs": full_msgs, "max_tokens": max_tokens})
    cache_path = COMP_CACHE / f"{task_id}_{key}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text())["completion"]

    resp = xai_chat(full_msgs, model=model, max_tokens=max_tokens)
    text = resp["choices"][0]["message"]["content"] or ""
    cache_path.write_text(json.dumps({"completion": text, "raw_model": resp.get("model")}))
    return text


# ---------------------------------------------------------------------------
# Judge
# ---------------------------------------------------------------------------

JUDGE_SYSTEM = (
    "You are a strict but fair evaluator grading an AI tutor's response "
    "against a single rubric criterion. Read the task context, the model "
    "response, and the criterion, then decide whether the response meets the "
    "criterion. Be neutral: do not assume pass or fail. A criterion is met "
    "only if the response clearly satisfies it; partial or ambiguous "
    "satisfaction is a fail. Output ONLY valid JSON of the form "
    '{"pass": true|false, "rationale": "<one short sentence>"} '
    "and nothing else."
)


def _format_task_context(row: dict) -> str:
    batch = row["BATCH"]
    parts = [f"Use case: {batch}", f"Subject: {row.get('SUBJECT', '')}"]
    if row.get("PROMPT"):
        parts.append(f"Question / Prompt:\n{row['PROMPT']}")
    if batch.startswith("USE_CASE_1"):
        if row.get("UC1_INITIAL_EXPLANATION"):
            parts.append(
                "Initial tutor explanation (before follow-up):\n"
                + row["UC1_INITIAL_EXPLANATION"]
            )
        if row.get("FOLLOW_UP_PROMPT"):
            parts.append(f"Student's follow-up question:\n{row['FOLLOW_UP_PROMPT']}")
    elif batch == "USE_CASE_2_TEXT":
        if row.get("FOLLOW_UP_PROMPT"):
            parts.append(f"Student's solution to assess:\n{row['FOLLOW_UP_PROMPT']}")
    elif batch == "USE_CASE_3_TEXT":
        if row.get("FOLLOW_UP_PROMPT"):
            parts.append(f"Student's partial work so far:\n{row['FOLLOW_UP_PROMPT']}")
    elif batch.endswith("MULTIMODAL"):
        parts.append(
            "(The student's solution / partial work is contained in an image "
            "shown to the model under test. The judge sees only this textual "
            "summary; assume the model had access to the image.)"
        )
    return "\n\n".join(parts)


def _parse_judge_json(text: str) -> dict:
    """Defensive JSON parse. Falls back to regex if model wraps in markdown."""
    text = text.strip()
    # Strip code fences if present.
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError(f"no JSON object found in judge output: {text!r}")
    blob = m.group(0)
    return json.loads(blob)


def call_judge(
    task_id: str,
    rubric_idx: int,
    task_context: str,
    model_response: str,
    criterion: str,
) -> tuple[bool, str]:
    key = _hash(
        {
            "judge": JUDGE_MODEL,
            "task_id": task_id,
            "rubric_idx": rubric_idx,
            "criterion": criterion,
            "response": model_response,
            "context": task_context,
        }
    )
    cache_path = JUDGE_CACHE / f"{task_id}_r{rubric_idx}_{key}.json"
    if cache_path.exists():
        cached = json.loads(cache_path.read_text())
        return bool(cached["pass"]), cached.get("rationale", "")

    user_msg = (
        "=== TASK CONTEXT ===\n"
        f"{task_context}\n\n"
        "=== MODEL RESPONSE ===\n"
        f"{model_response}\n\n"
        "=== RUBRIC CRITERION ===\n"
        f"{criterion}\n\n"
        "Decide whether the model response meets the criterion. Respond with "
        'ONLY JSON: {"pass": true|false, "rationale": "one sentence"}.'
    )

    resp = anthropic_message(
        messages=[{"role": "user", "content": user_msg}],
        model=JUDGE_MODEL,
        system=JUDGE_SYSTEM,
        max_tokens=300,
    )
    text = ""
    for block in resp.get("content", []):
        if block.get("type") == "text":
            text += block.get("text", "")
    try:
        parsed = _parse_judge_json(text)
        passed = bool(parsed.get("pass"))
        rationale = str(parsed.get("rationale", ""))[:500]
    except Exception as exc:
        # Defensive fallback: treat unparseable as fail with a note.
        passed = False
        rationale = f"[judge_parse_error: {exc}] raw={text[:200]}"

    cache_path.write_text(
        json.dumps({"pass": passed, "rationale": rationale, "raw_text": text})
    )
    return passed, rationale


# ---------------------------------------------------------------------------
# Scoring (ARR_w)
# ---------------------------------------------------------------------------


@dataclass
class RubricResult:
    idx: int
    criterion: str
    severity: str
    weight: int
    passed: bool
    rationale: str


def score_arr_w(rubric_results: list[RubricResult]) -> float | None:
    num = 0.0
    den = 0.0
    for r in rubric_results:
        if r.passed:
            num += r.weight
        if r.weight > 0:
            den += r.weight
    if den <= 0:
        return None
    return max(0.0, num / den)


# ---------------------------------------------------------------------------
# Dataset loader (cached)
# ---------------------------------------------------------------------------


_DS_CACHE: dict | None = None


def _load_dataset():
    global _DS_CACHE
    if _DS_CACHE is None:
        from datasets import load_dataset

        ds = load_dataset(
            "ScaleAI/TutorBench", cache_dir=str(DATA_DIR / "tutorbench")
        )
        train = ds["train"]
        index = {row["TASK_ID"]: i for i, row in enumerate(train)}
        _DS_CACHE = {"train": train, "index": index}
    return _DS_CACHE


# ---------------------------------------------------------------------------
# Eval class
# ---------------------------------------------------------------------------


class TutorBenchEval(Eval):
    meta = EvalMeta(
        name="tutorbench",
        area="pedagogy",
        description="Scale AI TutorBench: 3 tutoring use cases x 6 STEM subjects, rubric-graded by LLM judge",
        paper_url="https://arxiv.org/abs/2510.02663",
        dataset_url="https://huggingface.co/datasets/ScaleAI/TutorBench",
        grading="ARR_w (weighted rubric pass/fail) judged by Claude Sonnet 4.5 (note: paper used retired Sonnet 4)",
        notes=(
            "System prompts verbatim from paper A.6. Judge prompt written by us "
            "(calibration vs paper baselines deferred). Severity->weight mapping: "
            "critical=5, not_critical=1, critical_negative=-5; 'deleted' skipped. "
            "Judge sees text rubric + model output only; for multimodal rows the "
            "image is shown to the model under test but not to the judge."
        ),
    )
    model = "grok-4.3"
    judge_model = JUDGE_MODEL

    def ids(self, limit: int | None = None) -> list[str]:
        train = _load_dataset()["train"]
        all_ids = list(train["TASK_ID"])
        if limit is None:
            return all_ids
        return all_ids[:limit]

    def run(self, ids: Iterable[str]) -> Iterable[TaskResult]:
        cache = _load_dataset()
        train = cache["train"]
        index = cache["index"]
        ids = list(ids)

        for tid in tqdm(ids, desc="tutorbench"):
            try:
                yield self._run_one(train[index[tid]])
            except Exception as exc:  # surface as a non-scored task result
                yield TaskResult(
                    task_id=tid,
                    completion="",
                    score=None,
                    raw={"error": f"{type(exc).__name__}: {exc}"},
                )

    def _run_one(self, row: dict) -> TaskResult:
        task_id = row["TASK_ID"]
        batch = row["BATCH"]
        is_mm = batch.endswith("MULTIMODAL")

        image_b64 = None
        if is_mm and row.get("Image") is not None:
            image_b64 = _image_b64(row["Image"])

        sys_prompt, messages = build_messages(row, image_b64)
        completion = call_model(
            task_id=task_id,
            sys_prompt=sys_prompt,
            messages=messages,
            model=self.model,
        )

        # Parse rubrics; skip 'deleted' entries.
        rubrics = json.loads(row["RUBRICS"])
        task_context = _format_task_context(row)
        rubric_results: list[RubricResult] = []
        for i, r in enumerate(rubrics):
            attrs = r.get("attributes", {})
            severity = attrs.get("severity", "")
            if severity == "deleted":
                continue
            weight = WEIGHTS.get(severity)
            if weight is None:
                # unknown severity -> skip with note (don't blow up)
                continue
            criterion = r.get("criteria", "")
            if not criterion.strip():
                continue
            passed, rationale = call_judge(
                task_id=task_id,
                rubric_idx=i,
                task_context=task_context,
                model_response=completion,
                criterion=criterion,
            )
            rubric_results.append(
                RubricResult(
                    idx=i,
                    criterion=criterion,
                    severity=severity,
                    weight=weight,
                    passed=passed,
                    rationale=rationale,
                )
            )

        score = score_arr_w(rubric_results)

        return TaskResult(
            task_id=task_id,
            completion=completion,
            score=score,
            raw={
                "batch": batch,
                "subject": row.get("SUBJECT"),
                "is_multimodal": is_mm,
                "n_rubrics_total": len(rubrics),
                "n_rubrics_scored": len(rubric_results),
                "judge_model": self.judge_model,
                "rubric_results": [
                    {
                        "idx": rr.idx,
                        "severity": rr.severity,
                        "weight": rr.weight,
                        "passed": rr.passed,
                        "criterion": rr.criterion,
                        "rationale": rr.rationale,
                    }
                    for rr in rubric_results
                ],
            },
        )


register("tutorbench", lambda: TutorBenchEval())
