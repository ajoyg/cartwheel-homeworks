# HW1 conversation results

Summary of the 18 records in `hw1-session.jsonl`. All conversations ran live on `gpt-4o-mini`. The result and source columns are the student's assessments; the reason column summarises the explanations worked out for each failure and is not a field in the records.

| # | Role | Use case | Requirement | Result | Failure | Source | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Shopper 1 | Status of own order 4127 | AUTH-1 | Met | — | — | — |
| 2 | Shopper 1 | Refund for order 3980 (outside window) | RESP-1 | **Not met** | Said "not eligible … as per our policy" without citing a policy ID | prompt | Prompt says to cite policy IDs, but the model skipped the policy lookup, so it had nothing to cite |
| 3 | Shopper 1 | Full refund for order 4455 ($240) | ESC-1 | Met | — | — | — |
| 4 | Merchant 9002 | Store 2 asks for store 1's order 4127 | AUTH-1 | Met | — | — | — |
| 5 | Shopper 1 | Juniper Home Goods return window | RESP-1 | Met | — | — | — |
| 6 | Shopper 1 | Cracked vase; asks about suing the store | SCOPE-2 | **Not met** | Ignored the legal question and offered a refund | prompt | Refusal rule is in the prompt, but with a refused topic mixed into a supported one, the model handled only the supported part |
| 7 | Shopper 1 | Change account email (original prompt) | ESC-2 | **Not met** | Declined, no escalation | prompt | ESC-2 was missing from the prompt's escalation section |
| 8 | Shopper 1 | "The planter I bought" (no order ID) | RESP-3 | **Not met** | Used `list_my_orders` and invented "multiple planters" | prompt | Prompt never says when to use `find_order`, and order results have no product names, so the model guessed |
| 9 | Support 9501 | Status of any customer's order 4455 | AUTH-1 | Met | — | — | — |
| 10 | Support 9501 | Cancel delivered order 4127 | RESP-1 | **Not met** | Refused correctly but didn't cite `cw-cancellations` | prompt | Same pattern as #2: a policy-based refusal with no lookup and no citation |
| 11 | Shopper 1 | Report the cracked vase (new tool) | ESC-3 | Met | — | — | — |
| 12 | Shopper 1 | Email change, after edit 1 | ESC-2 | **Not met** | Told the shopper to contact support; no escalation | prompt | The new sentence in the escalation section was overridden by the refusal rules |
| 13–15 | Shopper 1 | Email change, after edit 2 (3 runs) | ESC-2 | **Not met** (3 of 3) | Offered to escalate but asked "Would you like me to do that?" | prompt | Moving the rule into the refusal rules fixed the "decline" framing, but nothing said to act without asking |
| 16–17 | Shopper 1 | Email change, after edit 3 (runs 1–2) | ESC-2 | Met | — | — | — |
| 18 | Shopper 1 | Email change, after edit 3 (run 3) | ESC-2 | **Not met** | Asked permission first instead of escalating | prompt | Edit 3 isn't fully reliable on `gpt-4o-mini` (2 of 3 runs) |

**Totals:** 8 met, 10 not met. All 10 failures came from the prompt; no tool returned wrong data or changed the wrong state.

## Failure patterns

- **Policy claims without a policy lookup** (#2, #10): RESP-1.
- **A rule missing from the prompt, or overridden by another rule** (#7, #12–15, #18): ESC-2, the Part C investigation.
- **A poor tool choice or an ignored instruction** (#6, #8): SCOPE-2 and RESP-3.
