"""Retraction/correction detection and contract override links."""

from __future__ import annotations

import re
from typing import Any

from app.ingest import store

RETRACT_PATTERNS = (
    r"\bCORRECTION\b",
    r"sorry,?\s+my earlier",
    r"not correct",
    r"please ignore my previous",
    r"@\w+\s+not correct",
    r"retract",
    r"my previous (?:message|mail|email) (?:was|is) (?:wrong|incorrect)",
)


def apply_corrections(conn: Any) -> list[dict[str, str]]:
    docs = store.list_documents(conn)
    by_type = [d for d in docs if d.get("doc_type") in {"email", "chat"}]
    actions: list[dict[str, str]] = []

    # Email subject-thread retractions
    emails = [d for d in docs if d.get("doc_type") == "email"]
    for later in emails:
        text = later.get("raw_text") or ""
        title = later.get("title") or ""
        if not any(re.search(p, text + "\n" + title, flags=re.I) for p in RETRACT_PATTERNS):
            continue
        later_date = later.get("created_date") or ""
        for earlier in emails:
            if earlier["id"] == later["id"]:
                continue
            earlier_date = earlier.get("created_date") or ""
            if earlier_date and later_date and earlier_date > later_date:
                continue
            # Same subject family
            subj_a = re.sub(r"^(re:|fw:|fwd:)\s*", "", (earlier.get("title") or ""), flags=re.I)
            subj_b = re.sub(r"^(re:|fw:|fwd:|correction:)\s*", "", title, flags=re.I)
            if fuzz_subject(subj_a, subj_b) or "deadline" in subj_a.casefold() and "deadline" in subj_b.casefold():
                store.add_link(conn, later["id"], earlier["id"], "retracts")
                store.add_link(conn, later["id"], earlier["id"], "corrects")
                store.set_retracted(conn, earlier["id"], True)
                actions.append({"from": later["path"], "to": earlier["path"], "relation": "retracts"})

    # Chat self-corrections: later message in same file correcting earlier amount
    chats = [d for d in docs if d.get("doc_type") == "chat"]
    for chat in chats:
        text = chat.get("raw_text") or ""
        if any(re.search(p, text, flags=re.I) for p in RETRACT_PATTERNS) or "not" in text.casefold() and "correct" in text.casefold():
            # Mark document metadata; claims layer will flag corrected messages
            meta = chat.get("metadata_json")
            actions.append({"from": chat["path"], "to": chat["path"], "relation": "self_corrects"})

    # Contract overrides toward policies with same subject keywords
    contracts = [d for d in docs if d.get("doc_type") == "contract" and d.get("customer")]
    policies = [d for d in docs if d.get("doc_type") == "policy"]
    for contract in contracts:
        ctext = (contract.get("raw_text") or "").casefold()
        for policy in policies:
            ptext = (policy.get("title") or "").casefold() + " " + (policy.get("raw_text") or "")[:400].casefold()
            if "allowance" in ctext and "allowance" in ptext:
                store.add_link(conn, contract["id"], policy["id"], "overrides")
                actions.append(
                    {"from": contract["path"], "to": policy["path"], "relation": "overrides"}
                )
            if "deadline" in ctext and "deadline" in ptext:
                store.add_link(conn, contract["id"], policy["id"], "overrides")
                actions.append(
                    {"from": contract["path"], "to": policy["path"], "relation": "overrides"}
                )

    return actions


def fuzz_subject(a: str, b: str) -> bool:
    aa = a.casefold().strip()
    bb = b.casefold().strip()
    if not aa or not bb:
        return False
    return aa in bb or bb in aa or aa.split()[:2] == bb.split()[:2]
