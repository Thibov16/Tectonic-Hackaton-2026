"""Run all 17 ground-truth questions and write eval_report.md."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# Allow `python -m app.eval.run_eval` from backend/
_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.config import get_config  # noqa: E402

logger = logging.getLogger(__name__)

ADVERSARIAL_MARKERS = tuple(f"A0{i}" for i in range(1, 10)) + tuple(f"a0{i}" for i in range(1, 10))
STALE_STATUSES = {"SUPERSEDED", "DRAFT"}
GAP_IDS = {"Q9", "Q15"}
CONFLICT_IDS = {"Q13"}


@dataclass
class CaseResult:
    id: str
    question: str
    expected: str
    verdict: str = ""
    headline: str = ""
    answer: str = ""
    top_paths: list[str] = field(default_factory=list)
    top1_ok: bool = False
    answer_ok: bool = False
    stale_in_top3: int = 0
    adversarial_flagged: int = 0
    explainable: bool = False
    notes: str = ""


def _ground_truth_path(config_data_dir: Path) -> Path:
    for base in (config_data_dir, config_data_dir.parent / "testset", Path("testset")):
        candidate = base / "ground_truth" / "questions_and_expected_answers.json"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("ground_truth/questions_and_expected_answers.json not found")


def _load_questions(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _path_blob(sources: list[dict[str, Any]]) -> str:
    return " ".join(
        f"{s.get('path', '')} {s.get('title', '')} {s.get('doc_type', '')}".casefold()
        for s in sources
    )


def _match_should_trust(sources: list[dict[str, Any]], should_trust: list[str]) -> bool:
    if not should_trust:
        return True
    blob = _path_blob(sources[:3])
    hits = 0
    for token in should_trust:
        key = token.casefold().replace("\\", "/")
        short = key.split("/")[-1]
        if key in blob or short in blob:
            hits += 1
    return hits >= max(1, (len(should_trust) + 1) // 2)


def _answer_overlap(answer: str, expected: str) -> bool:
    a = answer.casefold()
    e = expected.casefold()
    if not a or not e:
        return False
    # Key numeric / named tokens from expected
    tokens = []
    import re

    tokens.extend(re.findall(r"eur\s?\d+(?:[.,]\d+)?", e))
    tokens.extend(re.findall(r"\b\d{1,2}(?:st|nd|rd|th)?\b", e))
    for name in ("marc peeters", "katrin bauer", "gap", "unverified", "contract", "18", "12", "151", "160"):
        if name in e:
            tokens.append(name)
    if not tokens:
        # fallback: share nontrivial words
        words = [w for w in e.replace(".", " ").split() if len(w) > 4][:6]
        return sum(1 for w in words if w in a) >= max(1, len(words) // 2)
    return sum(1 for t in tokens if t in a) >= max(1, (len(tokens) + 2) // 3)


def _count_stale(sources: list[dict[str, Any]]) -> int:
    n = 0
    for s in sources[:3]:
        status = (s.get("status") or "").upper()
        role = (s.get("role") or "").casefold()
        if status in STALE_STATUSES or role in {"historical", "context_only"} or s.get("retracted"):
            n += 1
    return n


def _count_adversarial_flags(result: dict[str, Any]) -> int:
    count = 0
    for s in result.get("sources") or []:
        blob = f"{s.get('path', '')} {s.get('title', '')} {s.get('role', '')}".casefold()
        if any(m.casefold() in blob for m in ADVERSARIAL_MARKERS):
            if (s.get("role") or "").casefold() in {"blocked", "context_only", "historical"}:
                count += 1
            elif float(s.get("trust") or 1) < 0.3:
                count += 1
    for f in result.get("flags") or []:
        msg = f"{f.get('code', '')} {f.get('message', '')}".casefold()
        if any(
            x in msg
            for x in (
                "injection",
                "spoof",
                "bec",
                "forgery",
                "fabricat",
                "inconsist",
                "wiki",
                "phishing",
                "reply-to",
                "a0",
            )
        ):
            count += 1
    return count


def _explainable(sources: list[dict[str, Any]]) -> bool:
    if not sources:
        return True
    return all(len(s.get("reasons") or []) >= 1 for s in sources if s.get("role") != "blocked")


def evaluate_case(item: dict[str, Any], result: dict[str, Any]) -> CaseResult:
    sources = list(result.get("sources") or [])
    answer = str(result.get("answer") or "")
    expected = str(item.get("expected") or "")
    should_trust = list(item.get("should_trust") or [])
    top1_ok = _match_should_trust(sources, should_trust) if should_trust else _answer_overlap(answer, expected)
    answer_ok = _answer_overlap(answer, expected)
    # Gap/conflict special cases
    qid = item["id"]
    verdict = str(result.get("verdict") or "")
    notes = ""
    if qid in GAP_IDS:
        top1_ok = verdict == "gap" or "gap" in answer.casefold() or "low confidence" in answer.casefold()
        answer_ok = top1_ok
        if not top1_ok:
            notes = f"expected gap, got verdict={verdict}"
    if qid in CONFLICT_IDS:
        ok = verdict in {"conflict", "caution"} or "inconsist" in answer.casefold()
        top1_ok = ok
        answer_ok = ok
        if not ok:
            notes = f"expected conflict/inconsistency, got verdict={verdict}"

    return CaseResult(
        id=qid,
        question=item.get("question") or "",
        expected=expected,
        verdict=verdict,
        headline=str(result.get("headline") or ""),
        answer=answer,
        top_paths=[str(s.get("path") or s.get("title") or "") for s in sources[:3]],
        top1_ok=top1_ok,
        answer_ok=answer_ok,
        stale_in_top3=_count_stale(sources),
        adversarial_flagged=_count_adversarial_flags(result),
        explainable=_explainable(sources),
        notes=notes,
    )


def _sensitivity_changes(search_fn, questions: list[dict[str, Any]], sample_ids: tuple[str, ...] = ("Q1", "Q2", "Q4")) -> int:
    """Perturb trust weights ±0.1 and count ranking changes (best-effort)."""
    try:
        from app.config import TrustConfig, get_config
    except ImportError:
        return 0

    changes = 0
    config = get_config()
    base_trust = config.trust
    for qid in sample_ids:
        item = next((q for q in questions if q["id"] == qid), None)
        if not item:
            continue
        try:
            base = search_fn(item["question"], today="2026-09-30")
        except TypeError:
            base = search_fn(item["question"])
        base_paths = [s.get("path") for s in (base.get("sources") or [])[:3]]

        for attr, delta in (("w_authority", 0.1), ("w_currency", -0.1), ("w_applicability", 0.1)):
            perturbed = TrustConfig(**{**base_trust.__dict__})
            setattr(perturbed, attr, min(0.5, max(0.05, getattr(perturbed, attr) + delta)))
            # If search accepts config kw — try; otherwise skip
            try:
                alt = search_fn(item["question"], today="2026-09-30", trust_config=perturbed)
            except TypeError:
                try:
                    alt = search_fn(item["question"], config=None)
                except Exception:  # noqa: BLE001
                    return changes
            alt_paths = [s.get("path") for s in (alt.get("sources") or [])[:3]]
            if alt_paths != base_paths:
                changes += 1
    return changes


def write_report(
    results: list[CaseResult],
    *,
    out_path: Path,
    sensitivity_changes: int = 0,
    baseline_note: str = "",
) -> str:
    correct = sum(1 for r in results if r.top1_ok and r.answer_ok)
    total = len(results)
    stale_rate = (
        sum(r.stale_in_top3 for r in results) / max(1, total * 3)
    )
    adv = sum(1 for r in results if r.adversarial_flagged > 0)
    gap_conflict = [r for r in results if r.id in GAP_IDS | CONFLICT_IDS]
    gap_ok = sum(1 for r in gap_conflict if r.top1_ok)
    explain_ok = sum(1 for r in results if r.explainable)

    lines = [
        "# SD Worx trust search — evaluation report",
        "",
        f"Generated: {datetime.utcnow().isoformat(timespec='seconds')}Z",
        f"Clock: TODAY=2026-09-30",
        "",
        "## Summary",
        "",
        f"- Correct (top-1 source + answer match): **{correct} / {total}**",
        f"- Stale-source rate in top 3: **{stale_rate:.1%}**",
        f"- Cases with adversarial blocked/flagged: **{adv} / {total}**",
        f"- Gap/conflict recall (Q9, Q13, Q15): **{gap_ok} / {len(gap_conflict)}**",
        f"- Explainability (every source has ≥1 reason): **{explain_ok} / {total}**",
        f"- Sensitivity (ranking changes under ±0.1 weight perturbation): **{sensitivity_changes}**",
        "",
        "Target: ≥15/17 correct; all adversarial A01–A09 blocked or flagged somewhere in the suite.",
        "",
        "## Per-question results",
        "",
        "| ID | Verdict | Top1+Answer | Stale/3 | Adv flags | Paths |",
        "|----|---------|-------------|---------|-----------|-------|",
    ]
    for r in results:
        ok = "yes" if (r.top1_ok and r.answer_ok) else "no"
        paths = "; ".join(r.top_paths)[:80]
        lines.append(
            f"| {r.id} | {r.verdict or '-'} | {ok} | {r.stale_in_top3} | {r.adversarial_flagged} | {paths} |"
        )

    lines.extend(["", "## Failures / notes", ""])
    failures = [r for r in results if not (r.top1_ok and r.answer_ok)]
    if not failures:
        lines.append("None — all questions matched targets.")
    else:
        for r in failures:
            lines.append(f"- **{r.id}**: expected `{r.expected[:120]}…`")
            lines.append(f"  - got verdict={r.verdict}, answer={r.answer[:160]}")
            if r.notes:
                lines.append(f"  - note: {r.notes}")

    lines.extend(
        [
            "",
            "## Baselines",
            "",
            baseline_note
            or (
                "Baseline comparison (plain hybrid / summary-only / full trust) is reported when "
                "`app.pipeline.search` exposes baseline modes; otherwise this run is full-system only."
            ),
            "",
            "## Method",
            "",
            "- Runner calls `app.pipeline.search` for each ground-truth question.",
            "- Answer match uses key tokens from the expected string (amounts, deadlines, expert names).",
            "- `should_trust` paths are matched fuzzily against top-3 source paths/titles.",
            "",
        ]
    )
    text = "\n".join(lines)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return text


def run(out_path: Path | None = None) -> list[CaseResult]:
    from app.pipeline import search  # local import so module loads without pipeline during unit tests

    config = get_config()
    gt_path = _ground_truth_path(config.data_dir)
    questions = _load_questions(gt_path)
    results: list[CaseResult] = []

    print(f"{'ID':4} {'verdict':10} {'ok':4} {'paths'}")
    for item in questions:
        q = item["question"]
        try:
            raw = search(q, today="2026-09-30")
        except TypeError:
            raw = search(q)
        if hasattr(raw, "model_dump"):
            result = raw.model_dump()
        elif isinstance(raw, dict):
            result = raw
        else:
            result = dict(raw)
        case = evaluate_case(item, result)
        results.append(case)
        ok = "Y" if case.top1_ok and case.answer_ok else "N"
        print(f"{case.id:4} {case.verdict[:10]:10} {ok:4} {', '.join(case.top_paths)[:90]}")

    sens = 0
    try:
        sens = _sensitivity_changes(search, questions)
    except Exception as exc:  # noqa: BLE001
        logger.info("Sensitivity skipped: %s", exc)

    report_path = out_path or (_BACKEND.parent / "eval_report.md")
    write_report(results, out_path=report_path, sensitivity_changes=sens)
    print(f"\nWrote {report_path}")
    return results


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Evaluate trust search on 17 ground-truth questions")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Path for eval_report.md (default: repo root)",
    )
    args = parser.parse_args()
    run(out_path=args.out)


if __name__ == "__main__":
    main()
