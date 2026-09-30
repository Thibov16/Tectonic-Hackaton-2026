from __future__ import annotations

from app.trust.claims import extract_claims_from_text


def test_extract_allowance_amounts():
    text = "Allowance: EUR 151.00 per month. Old amount EUR 140. Unsigned copy says EUR 181."
    claims = extract_claims_from_text(text, scope_country="BE")
    values = {c["value_num"] for c in claims}
    assert 151.0 in values
    assert 140.0 in values


def test_extract_deadline_days():
    text = "Submit variable pay by the 18th calendar day. Draft proposes the 15th."
    claims = extract_claims_from_text(text)
    days = {c["value_num"] for c in claims if c["subject"] == "payroll_deadline"}
    assert 18.0 in days


def test_meal_voucher():
    text = "Maximum meal voucher face value is EUR 8.00. Employee min EUR 1.09."
    claims = extract_claims_from_text(text)
    assert any(c["value_num"] == 8.0 for c in claims)
