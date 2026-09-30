from __future__ import annotations

from app.config import get_config
from app.ingest import store
from app.trust.integrity import check_integrity


def _doc(db_conn, needle: str):
    return next(d for d in store.list_documents(db_conn) if needle in (d.get("path") or ""))


def test_adversarial_critical(db_conn, app_config):
    config = app_config
    for needle, code in [
        ("A01", "BEC"),
        ("A02", "PROMPT_INJECTION"),
        ("A04", "SPOOFED_SENDER"),
    ]:
        findings = check_integrity(_doc(db_conn, needle), config)
        codes = {f["code"] for f in findings}
        assert code in codes or any(f["severity"] == "critical" for f in findings), (
            needle,
            codes,
        )


def test_a03_personal_drive(db_conn, app_config):
    findings = check_integrity(_doc(db_conn, "A03"), app_config)
    codes = {f["code"] for f in findings}
    assert codes & {"PERSONAL_DRIVE", "POSSIBLE_FORGERY", "UNRELIABLE_PROVENANCE"}


def test_a08_missing_country(db_conn, app_config):
    findings = check_integrity(_doc(db_conn, "A08"), app_config)
    assert any(f["code"] == "MISSING_COUNTRY" for f in findings)
