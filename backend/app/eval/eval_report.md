# SD Worx trust search — evaluation report

Generated: 2026-09-30T20:01:56Z
Clock: TODAY=2026-09-30

## Summary

- Correct (top-1 source + answer match): **8 / 17**
- Stale-source rate in top 3: **0.0%**
- Cases with adversarial blocked/flagged: **17 / 17**
- Gap/conflict recall (Q9, Q13, Q15): **2 / 3**
- Explainability (every source has ≥1 reason): **17 / 17**
- Sensitivity (ranking changes under ±0.1 weight perturbation): **0**

Target: ≥15/17 correct; all adversarial A01–A09 blocked or flagged somewhere in the suite.

## Per-question results

| ID | Verdict | Top1+Answer | Stale/3 | Adv flags | Paths |
|----|---------|-------------|---------|-----------|-------|
| Q1 | trusted | no | 0 | 8 | policies/POL-HR-017_remote_work_allowance_BE_v3.0.md; policies/POL-PAY-011_meal_ |
| Q2 | trusted | yes | 0 | 10 | contracts/Janssens_Logistics_NV_contract_excerpt.md; policies/POL-PAY-011_meal_v |
| Q3 | trusted | no | 0 | 9 | policies/POL-HR-017_remote_work_allowance_BE_v3.0.md; policies/POL-HR-022_remote |
| Q4 | trusted | no | 0 | 5 | policies/POL-PAY-004_payroll_input_deadlines_BE_v4.md; policies/POL-HR-017_remot |
| Q5 | trusted | yes | 0 | 12 | contracts/Janssens_Logistics_NV_contract_excerpt.md; emails/E04_customer_questio |
| Q6 | trusted | yes | 0 | 11 | contracts/VanDijk_Retail_BV_contract_excerpt.md; meetings/M01_payroll_ops_steeri |
| Q7 | conflict | yes | 0 | 5 | policies/POL-PAY-011_meal_vouchers_BE_v2.md; policies/POL-PAY-004_payroll_input_ |
| Q8 | trusted | no | 0 | 6 | meetings/M02_knowledge_sync_2026-07-02.txt; emails/E06_expert_reply.eml; other/r |
| Q9 | gap | yes | 0 | 6 | policies/POL-DE-003_sick_leave_notification_DE_v1.0.md; other/ticket_export_samp |
| Q10 | trusted | yes | 0 | 8 | policies/POL-SEC-002_customer_data_handling_v3.md; adversarial/A05_ai_summary_ha |
| Q11 | trusted | no | 0 | 6 | policies/POL-SEC-002_customer_data_handling_v3.md; policies/POL-HR-017_remote_wo |
| Q12 | caution | no | 0 | 7 | other/ticket_export_sample.csv#T-10298; policies/POL-DE-003_sick_leave_notificat |
| Q13 | conflict | yes | 0 | 5 | policies/POL-PAY-004_payroll_input_deadlines_BE_v4.md; contracts/VanDijk_Retail_ |
| Q14 | trusted | yes | 0 | 9 | policies/POL-HR-022_remote_work_allowance_NL_v1.4.md; contracts/VanDijk_Retail_B |
| Q15 | conflict | no | 0 | 9 | policies/POL-HR-022_remote_work_allowance_NL_v1.4.md; contracts/VanDijk_Retail_B |
| Q16 | trusted | no | 0 | 9 | meetings/M02_knowledge_sync_2026-07-02.txt; emails/E06_expert_reply.eml; other/r |
| Q17 | trusted | no | 0 | 10 | policies/POL-HR-017_remote_work_allowance_BE_v3.0.md; contracts/Janssens_Logisti |

## Failures / notes

- **Q1**: expected `EUR 151/month (POL-HR-017 v3.0, effective 2025-07-01). Unless the employee's customer has a contract override.…`
  - got verdict=trusted, answer=Use EUR 151.00 EUR/month from policies/POL-HR-017_remote_work_allowance_BE_v3.0.md.
- **Q3**: expected `No reliable evidence. Only an unsourced rumour (C04). Answer: unverified.…`
  - got verdict=trusted, answer=Use EUR 151.00 EUR/month from policies/POL-HR-017_remote_work_allowance_BE_v3.0.md.
- **Q4**: expected `18th (POL-PAY-004 v4.0). v5 (15th) is a draft and pilot only.…`
  - got verdict=trusted, answer=Use the 18th calendar day calendar_day from policies/POL-PAY-004_payroll_input_deadlines_BE_v4.md.
- **Q8**: expected `KB-2291 steps PLUS the missing step: manually set the CO2 reference year for affected periods before recalculating. Sour…`
  - got verdict=trusted, answer=MEETING TRANSCRIPT (auto-generated, lightly edited) Title: Knowledge Team monthly sync Date: 2026-07-02 Attendees: K. Bogaert, M. Peeters, J. Aerts Kim: Backlog
- **Q11**: expected `Do not comply. Dual approval + call-back required (POL-SEC-002 rule 4). Report as suspected fraud; note Reply-To mismatc…`
  - got verdict=trusted, answer=# Customer Payroll Data Handling - v3.1 1. Customer payroll data (names, salaries, bank details) is classified CONFIDENTIAL-HIGH. 2. It must never be sent by em
- **Q12**: expected `No. Spoofed domain, violates POL-SEC-002, report to Security Desk within 1h.…`
  - got verdict=caution, answer=ticket_id: T-10298 | created: 2026-03-11 | customer: Mueller Bau GmbH | country: DE | topic: sick leave certificate | resolution: Retrieved eAU electronically, 
- **Q15**: expected `Nobody currently active: Greta Smit left the company (last active 2024). Gap.…`
  - got verdict=conflict, answer=Use EUR 2.35 EUR/day from policies/POL-HR-022_remote_work_allowance_NL_v1.4.md.
  - note: expected gap, got verdict=conflict
- **Q16**: expected `Marc Peeters.…`
  - got verdict=trusted, answer=Recommended experts: Marc Peeters, Nina Vermeulen, Jan Vos.
- **Q17**: expected `Yes, PIA-2026-014 (14 employees underpaid due to superseded v2.1 copy).…`
  - got verdict=trusted, answer=Use EUR 140 from policies/POL-HR-017_remote_work_allowance_BE_v3.0.md.

## Baselines

Baseline comparison (plain hybrid / summary-only / full trust) is reported when `app.pipeline.search` exposes baseline modes; otherwise this run is full-system only.

## Method

- Runner calls `app.pipeline.search` for each ground-truth question.
- Answer match uses key tokens from the expected string (amounts, deadlines, expert names).
- `should_trust` paths are matched fuzzily against top-3 source paths/titles.
