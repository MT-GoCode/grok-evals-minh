"""Post-hoc LLM-judge for MatSciBench: re-grade existing rule-judge JSONL with Claude.

Uses the verbatim judge prompts from vendor/MatSciBench/evaluation/prompts.py
but routes through Claude Sonnet 4.5 (paper used Gemini 2.5 Flash Lite). Documented
deviation. Writes a parallel `*_llm.jsonl` next to the input file.

Usage:
    .venv/bin/python scripts/matscibench_llm_judge.py results/matscibench/<file>.jsonl
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

from grok_evals.clients import anthropic_message
from grok_evals._paths import VENDOR_DIR

JUDGE_MODEL = "claude-sonnet-4-5-20250929"

# Load question text from the vendored CSV (only field missing from the JSONL).
_QA_CSV = VENDOR_DIR / "MatSciBench" / "datasets" / "MatSciBench" / "qa.csv"


def _load_questions_by_qid() -> dict[str, str]:
    import csv
    out: dict[str, str] = {}
    with _QA_CSV.open("r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            out[row["qid"]] = row["question"]
    return out


_QUESTIONS = _load_questions_by_qid()

JUDGE_SYSTEM_PROMPT = (
    "As an expert judge, evaluate if the following model's answer matches the reference answer. "
    "Focus on the numerical values and key concepts. Small numerical differences are tolerable due to approximation errors. "
    "Don't solve the problem, just judge if the model answer matches the reference answer. "
    "Put the final decision ('correct' (if matching) or 'incorrect' (if not matching)) inside a single box using \\boxed{...}. "
)

JUDGE_USER_PROMPT = (
    "The question is: {question}"
    "Reference answer: {correct_answer} "
    "Model answer: {model_answer} "
    "Is the model answer matching the reference answer? "
)


def _extract_boxed(text: str) -> str:
    """Brace-balanced extract of the last \\boxed{...} contents."""
    if not text:
        return ""
    i = text.rfind("oxed{")
    if i == -1:
        return ""
    start = i + len("oxed{")
    depth = 1
    j = start
    while j < len(text) and depth > 0:
        c = text[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        j += 1
        if depth == 0:
            break
    if depth > 0:
        return ""
    return text[start : j - 1].strip()


def judge_one(row: dict) -> dict:
    raw = row.get("raw", {})
    question = raw.get("question_text") or _QUESTIONS.get(row.get("task_id", ""), "")
    if not question:
        return {**row, "llm_judge": {"is_correct": None, "reasoning": "no question text available"}}

    user = JUDGE_USER_PROMPT.format(
        question=question,
        correct_answer=raw.get("correct_answer", ""),
        model_answer=raw.get("final_answer", ""),
    )
    try:
        resp = anthropic_message(
            messages=[{"role": "user", "content": user}],
            model=JUDGE_MODEL,
            system=JUDGE_SYSTEM_PROMPT,
            max_tokens=512,
            temperature=0.0,
        )
        text = "".join(blk.get("text", "") for blk in resp.get("content", []) if blk.get("type") == "text")
        decision = _extract_boxed(text).lower().strip().strip('"').strip("'")
        is_correct = decision == "correct"
    except Exception as e:
        return {**row, "llm_judge": {"is_correct": None, "reasoning": f"ERROR: {e}"}}

    new_row = dict(row)
    new_row["llm_judge"] = {"is_correct": is_correct, "reasoning": text}
    new_row["score_rule"] = row.get("score")
    new_row["score"] = 1.0 if is_correct else 0.0  # primary score is now llm
    return new_row


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: matscibench_llm_judge.py <results/.../file.jsonl>", file=sys.stderr)
        return 2
    src = Path(sys.argv[1])
    if not src.exists():
        print(f"missing input: {src}", file=sys.stderr)
        return 2

    rows = [json.loads(line) for line in src.read_text().splitlines() if line.strip()]
    print(f"loaded {len(rows)} rows from {src}")

    out_path = src.with_name(src.stem + "_llm" + src.suffix)
    summary_path = out_path.with_suffix(".summary.json")

    # Concurrent calls — Anthropic typically handles 10+ rps comfortably.
    results: list[dict] = [None] * len(rows)  # type: ignore[list-item]
    started = time.time()
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(judge_one, r): i for i, r in enumerate(rows)}
        for fut in tqdm(as_completed(futs), total=len(futs), desc="llm-judge"):
            i = futs[fut]
            results[i] = fut.result()
    elapsed = time.time() - started

    with out_path.open("w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

    n_scored = sum(1 for r in results if isinstance(r.get("score"), (int, float)))
    n_correct_llm = sum(1 for r in results if r.get("llm_judge", {}).get("is_correct"))
    n_correct_rule = sum(1 for r in results if r.get("score_rule") == 1.0)
    n_disagree = sum(
        1 for r in results
        if (r.get("llm_judge", {}).get("is_correct") is not None)
        and ((1.0 if r["llm_judge"]["is_correct"] else 0.0) != r.get("score_rule"))
    )
    summary = {
        "source": str(src),
        "judge_model": JUDGE_MODEL,
        "n_rows": len(rows),
        "n_scored": n_scored,
        "rule_judge": {
            "n_correct": n_correct_rule,
            "accuracy": n_correct_rule / max(1, n_scored),
        },
        "llm_judge": {
            "n_correct": n_correct_llm,
            "accuracy": n_correct_llm / max(1, n_scored),
        },
        "disagreement_count": n_disagree,
        "elapsed_seconds": round(elapsed, 1),
    }
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"\nwrote {out_path}")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
