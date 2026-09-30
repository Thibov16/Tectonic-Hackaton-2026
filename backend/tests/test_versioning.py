from __future__ import annotations

from app.ingest import store


def test_remote_allowance_versions(db_conn):
    docs = store.list_documents(db_conn)
    hr017 = [d for d in docs if d.get("doc_id") == "POL-HR-017"]
    assert hr017
    by_ver = {d.get("version"): d for d in hr017}
    assert by_ver["3.0"]["status"] == "CURRENT"
    assert by_ver["2.1"]["status"] == "SUPERSEDED"


def test_deadline_draft_stays_draft(db_conn):
    docs = store.list_documents(db_conn)
    drafts = [
        d
        for d in docs
        if d.get("doc_id") == "POL-PAY-004" and "DRAFT" in (d.get("path") or "").upper()
    ]
    assert drafts
    assert drafts[0]["status"] == "DRAFT"
