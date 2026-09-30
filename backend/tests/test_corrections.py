from __future__ import annotations

from app.ingest import store


def test_e02_retracted_by_e03(db_conn):
    docs = store.list_documents(db_conn)
    e02 = next(d for d in docs if "E02" in (d.get("path") or ""))
    assert e02["retracted"] in (1, True)

    links = store.list_links(db_conn)
    e03 = next(d for d in docs if "E03" in (d.get("path") or ""))
    assert any(
        lnk["from_doc"] == e03["id"]
        and lnk["to_doc"] == e02["id"]
        and lnk["relation"] in {"retracts", "corrects"}
        for lnk in links
    )
