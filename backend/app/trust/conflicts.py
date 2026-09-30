"""Claim-level conflict / override / outdated analysis."""

from __future__ import annotations

from typing import Any

from app.config import AppConfig
from app.trust.signals import resolve_authority_key


def _doc_authority(doc: dict[str, Any] | None, config: AppConfig) -> float:
    if not doc:
        return 0.15
    key = resolve_authority_key(doc)
    return float(config.trust.authority_table.get(key, 0.15))


def analyse_claims(
    claims: list[dict[str, Any]],
    docs_by_id: dict[int, dict[str, Any]],
    query_ctx: dict[str, Any],
    config: AppConfig,
    *,
    links: list[dict[str, Any]] | None = None,
    scored_docs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Analyse claims relevant to the query topic.

    Returns overrides, conflicts, outdated, winning_claim.
    """
    links = links or []
    topic = query_ctx.get("topic") or "other"
    q_country = (query_ctx.get("country") or "").upper() or None
    q_customer = query_ctx.get("customer")

    # Filter to topic (and near scope)
    relevant: list[dict[str, Any]] = []
    for claim in claims:
        subj = claim.get("subject") or "other"
        if topic != "other" and subj != topic and not (
            topic == "remote_allowance" and subj == "remote_allowance"
        ):
            if subj != topic:
                continue
        c_country = (claim.get("scope_country") or "").upper() or None
        if q_country and c_country and c_country not in {"UNKNOWN"} and c_country != q_country:
            continue
        c_cust = claim.get("scope_customer")
        if q_customer and c_cust and str(c_cust).casefold() != str(q_customer).casefold():
            # Keep generic claims (no customer) and matching ones
            continue
        relevant.append(claim)

    if topic != "other":
        relevant = [c for c in relevant if c.get("subject") == topic] or relevant

    override_pairs: list[dict[str, Any]] = []
    override_doc_ids: set[int] = set()
    for link in links:
        if link.get("relation") != "overrides":
            continue
        override_pairs.append(link)
        override_doc_ids.add(int(link["from_doc"]))

    outdated: list[dict[str, Any]] = []
    live_claims: list[dict[str, Any]] = []
    for claim in relevant:
        doc = docs_by_id.get(int(claim["document_id"]))
        if not doc:
            continue
        status = (doc.get("status") or "").upper()
        if doc.get("retracted") or status == "SUPERSEDED":
            outdated.append(
                {
                    "claim": claim,
                    "document_id": doc["id"],
                    "reason": "retracted" if doc.get("retracted") else "superseded",
                    "path": doc.get("path"),
                }
            )
            continue
        if status == "DRAFT":
            outdated.append(
                {
                    "claim": claim,
                    "document_id": doc["id"],
                    "reason": "draft",
                    "path": doc.get("path"),
                }
            )
            continue
        live_claims.append(claim)

    # Prefer customer-specific / override docs when customer in query
    preferred: list[dict[str, Any]] = []
    if q_customer:
        for claim in live_claims:
            doc = docs_by_id.get(int(claim["document_id"]))
            if not doc:
                continue
            if doc.get("customer") and str(doc["customer"]).casefold() == str(q_customer).casefold():
                preferred.append(claim)
            elif int(claim["document_id"]) in override_doc_ids and (
                doc.get("doc_type") or ""
            ) == "contract":
                preferred.append(claim)
    pool = preferred or live_claims

    # Group by subject/unit and detect conflicts
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for claim in pool:
        key = (claim.get("subject"), claim.get("unit"))
        groups.setdefault(key, []).append(claim)

    conflicts: list[dict[str, Any]] = []
    winning_claim: dict[str, Any] | None = None
    best_score = -1.0

    trust_by_id: dict[int, float] = {}
    if scored_docs:
        for s in scored_docs:
            if s.get("id") is not None:
                trust_by_id[int(s["id"])] = float(s.get("trust") or 0.0)

    for key, group in groups.items():
        # Deduplicate identical values
        by_value: dict[float | str, list[dict[str, Any]]] = {}
        for claim in group:
            vn = claim.get("value_num")
            vk: float | str
            if vn is not None:
                vk = float(vn)
            else:
                vk = (claim.get("value_text") or "").casefold()
            by_value.setdefault(vk, []).append(claim)

        if len(by_value) > 1:
            conflict_entry = {
                "subject": key[0],
                "unit": key[1],
                "values": [
                    {
                        "value": val,
                        "claims": items,
                        "document_ids": [c["document_id"] for c in items],
                    }
                    for val, items in by_value.items()
                ],
            }
            conflicts.append(conflict_entry)

        for claim in group:
            doc = docs_by_id.get(int(claim["document_id"]))
            auth = _doc_authority(doc, config)
            trust = trust_by_id.get(int(claim["document_id"]), auth)
            # Contract override bonus
            if doc and (doc.get("doc_type") or "") == "contract" and q_customer:
                trust += 0.15
            if doc and doc.get("retracted"):
                continue
            score = trust * float(claim.get("confidence") or 0.7)
            if score > best_score:
                best_score = score
                winning_claim = {
                    **claim,
                    "document": {
                        "id": doc.get("id") if doc else None,
                        "path": doc.get("path") if doc else None,
                        "title": doc.get("title") if doc else None,
                        "doc_type": doc.get("doc_type") if doc else None,
                    },
                    "score": round(score, 4),
                }

    # Authority-close conflicts stay as conflicts; large authority gap → prefer winner
    resolved_conflicts: list[dict[str, Any]] = []
    for conflict in conflicts:
        auths = []
        for val_block in conflict["values"]:
            max_a = 0.0
            for c in val_block["claims"]:
                doc = docs_by_id.get(int(c["document_id"]))
                max_a = max(max_a, _doc_authority(doc, config))
            auths.append(max_a)
        if len(auths) >= 2 and abs(max(auths) - min(auths)) < config.trust.conflict_authority_delta:
            resolved_conflicts.append(conflict)
        elif len(auths) >= 2:
            # Keep as soft conflict / note
            conflict["authority_separated"] = True
            resolved_conflicts.append(conflict)

    return {
        "overrides": override_pairs,
        "conflicts": resolved_conflicts,
        "outdated": outdated,
        "winning_claim": winning_claim,
        "relevant_claim_count": len(relevant),
    }
