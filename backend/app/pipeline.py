"""End-to-end deterministic search pipeline."""

from __future__ import annotations

import logging
import time
from typing import Any

from app.config import AppConfig, get_config
from app.ingest import store
from app.ingest.loader import ingest
from app.retrieval.index import HybridIndex
from app.retrieval.query import understand_query
from app.trust.conflicts import analyse_claims
from app.trust.experts import find_experts
from app.trust.integrity import check_integrity
from app.trust.scoring import score_document
from app.trust.verdict import build_verdict

logger = logging.getLogger(__name__)


class SearchService:
    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config or get_config()
        self.index = HybridIndex()
        self._ready = False

    def ensure_ready(self, *, rebuild: bool = False) -> None:
        db = self.config.db_path
        assert db is not None
        if rebuild or not db.exists() or db.stat().st_size < 100:
            logger.info("Ingesting dataset into %s", db)
            ingest(self.config, rebuild=True)
        conn = store.connect(db)
        store.init_db(conn)
        docs = store.list_documents(conn)
        if not docs:
            ingest(self.config, rebuild=True)
            docs = store.list_documents(conn)
        self.index.build(conn)
        conn.close()
        self._ready = True
        logger.info("SearchService ready with %d documents", len(docs))

    def search(self, details: str, today: str | None = None) -> dict[str, Any]:
        t0 = time.perf_counter()
        if not self._ready:
            self.ensure_ready()
        config = self.config
        if today:
            from datetime import date as date_cls

            config.today = date_cls.fromisoformat(today)

        query_ctx = understand_query(details, config)
        candidates = self.index.search(details, top_k=config.retrieval_top_k)

        conn = store.connect(config.db_path)  # type: ignore[arg-type]
        links = store.list_links(conn)
        docs_by_id: dict[int, dict[str, Any]] = {}
        all_claims: list[dict[str, Any]] = []
        peer_docs: list[dict[str, Any]] = []
        evidence_by_id: dict[int, str] = {}
        retrieval_scores: dict[int, float] = {}

        for cand in candidates:
            doc = store.get_document(conn, int(cand["document_id"]))
            if not doc:
                continue
            did = int(doc["id"])
            docs_by_id[did] = doc
            peer_docs.append(doc)
            evidence_by_id[did] = cand.get("evidence") or ""
            retrieval_scores[did] = float(cand.get("score") or 0.0)
            all_claims.extend(store.list_claims(conn, did))

        scored: list[dict[str, Any]] = []
        for doc in peer_docs:
            findings = check_integrity(doc, config)
            item = score_document(
                doc,
                query_ctx,
                config,
                peer_docs=peer_docs,
                links=links,
                integrity_findings=findings,
                evidence=evidence_by_id.get(int(doc["id"]), ""),
                retrieval_score=retrieval_scores.get(int(doc["id"]), 0.0),
            )
            scored.append(item)

        scored.sort(key=lambda x: x.get("trust") or 0.0, reverse=True)

        conflicts_result = analyse_claims(
            all_claims,
            docs_by_id,
            query_ctx,
            config,
            links=links,
            scored_docs=scored,
        )

        # Promote contract override docs to primary role when they win
        winning = conflicts_result.get("winning_claim")
        if winning:
            win_doc = winning.get("document") or {}
            win_id = win_doc.get("id")
            for item in scored:
                if item.get("id") == win_id and item.get("role") not in {
                    "blocked",
                    "historical",
                    "context_only",
                }:
                    item["role"] = "primary"
                    item["reasons"] = [
                        "Winning claim for this query context"
                    ] + list(item.get("reasons") or [])

        experts = find_experts(
            conn,
            topic=query_ctx.get("topic") or "other",
            country=query_ctx.get("country"),
            config=config,
        )

        if query_ctx.get("question_type") == "who_knows":
            if not any(e.get("available") for e in experts):
                # keep gap signal via verdict
                pass

        result = build_verdict(query_ctx, scored, conflicts_result, experts, config)

        mode = "deterministic"
        try:
            from app.llm.client import maybe_phrase_answer
            from app.llm.fallback import compose_answer

            answer = compose_answer(
                {
                    "answer": result.get("answer"),
                    "verdict": result.get("verdict"),
                    "gaps": result.get("gaps"),
                    "sources": result.get("sources"),
                    "winning_claim": (
                        {
                            "claim": winning,
                            "doc": (winning or {}).get("document") or {},
                        }
                        if winning
                        else None
                    ),
                },
                query_ctx,
            )
            phrased = maybe_phrase_answer(
                {
                    **result,
                    "winning_claim": {
                        "claim": winning,
                        "doc": (winning or {}).get("document") or {},
                    }
                    if winning
                    else None,
                },
                query_ctx,
                config,
            )
            if phrased:
                answer = phrased
                mode = "llm"
            result["answer"] = answer
        except Exception:  # noqa: BLE001
            pass

        conn.close()
        elapsed = int((time.perf_counter() - t0) * 1000)
        result["meta"] = {
            **(result.get("meta") or {}),
            "mode": mode,
            "elapsed_ms": elapsed,
            "candidates": len(candidates),
        }
        # Strip non-serialisable winning claim nesting for API
        if isinstance(result.get("meta", {}).get("winning_claim"), dict):
            wc = result["meta"]["winning_claim"]
            result["meta"]["winning_claim"] = {
                "value_text": wc.get("value_text"),
                "value_num": wc.get("value_num"),
                "unit": wc.get("unit"),
                "subject": wc.get("subject"),
                "path": ((wc.get("document") or {}).get("path")),
            }
        return result


_SERVICE: SearchService | None = None


def get_service(config: AppConfig | None = None) -> SearchService:
    global _SERVICE
    if _SERVICE is None or (config is not None and config is not _SERVICE.config):
        _SERVICE = SearchService(config)
    return _SERVICE


def search(
    details: str, today: str | None = None, config: AppConfig | None = None
) -> dict[str, Any]:
    return get_service(config).search(details, today=today)


def explain_query(
    details: str, today: str | None = None, config: AppConfig | None = None
) -> str:
    result = search(details, today=today, config=config)
    lines = [
        f"Query context: {result['query_context']}",
        f"Verdict: {result['verdict']}  confidence={result['confidence']}",
        f"Headline: {result['headline']}",
        f"Answer: {result['answer']}",
        "Sources:",
    ]
    for s in result["sources"][:8]:
        sig = s["signals"]
        lines.append(
            f"  [{s['role']}] trust={s['trust']:.3f} {s['path']}"
            f" A={sig['authority']:.2f} C={sig['currency']:.2f}"
            f" App={sig['applicability']:.2f} Cor={sig['corroboration']:.2f}"
            f" I={sig['integrity']:.2f}"
        )
        for r in (s.get("reasons") or [])[:4]:
            lines.append(f"      - {r}")
    if result.get("flags"):
        lines.append("Flags:")
        for f in result["flags"][:12]:
            lines.append(f"  [{f['severity']}] {f['code']}: {f['message']}")
    if result.get("gaps"):
        lines.append("Gaps: " + "; ".join(result["gaps"]))
    if result.get("experts"):
        lines.append("Experts: " + ", ".join(e["name"] for e in result["experts"]))
    return "\n".join(lines)
