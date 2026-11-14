# HW5 development record: `uncited_policy_claim`

Judge model: `gpt-4o-mini` for every version. Development split: 40 conversations (18 Pass, 22 Fail). Pass = 1 is the positive class.

## Versions

| Version | Prompt | TP | FN | TN | FP | TPR (95% Wilson) | TNR (95% Wilson) |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| v0 | `analysis/prompts/uncited_policy_claim-v0.txt` | 15 | 3 | 22 | 0 | 0.833 (0.608–0.942) | 1.000 (0.851–1.000) |
| v1 | `analysis/prompts/uncited_policy_claim-v1.txt` | 16 | 2 | 21 | 1 | 0.889 (0.672–0.969) | 0.955 (0.782–0.992) |
| v2 | `analysis/prompts/uncited_policy_claim-v2.txt` | 15 | 3 | 22 | 0 | 0.833 (0.608–0.942) | 1.000 (0.851–1.000) |

Metrics: `analysis/report/dev-uncited_policy_claim-v0.json`, `-v1.json`, `-v2.json`. Verdicts and critiques: `analysis/state/judges/`.

## Disagreements and decisions

| Conversation | Human | v0 | v1 | v2 | Decision |
| --- | --- | --- | --- | --- | --- |
| support-0010 | Pass | Fail | Fail | Fail | Judge wrong: a refund-eligibility field inside a list of order fields is a record fact. Fix the prompt. |
| support-0143 | Pass | Fail | Fail | Fail | Same as 0010. |
| support-0146 | Pass | Fail | Pass | Fail | Same as 0010. |
| support-0227 | Fail | Fail | Pass | Fail | First appeared in v1. "I can only look up your own orders" states the roles rule; v2 added that rule explicitly. |

No human label was changed during development.

## Revisions

1. **v0 → v1.** Added a Pass rule that a refund-eligibility field inside a list of order fields is a record fact, and a note to the Example 3 critique. Fixed 0146 only; 0227 flipped to Pass with no related prompt change, likely run-to-run variation.
2. **v1 → v2.** A constructed example was considered and rejected because it would not come from the labeled data. Instead, the critique became a stepwise procedure (quote, give each statement's form, classify, check citation, decide), and the Fail rules gained a sentence that limiting lookups to the user's own records states the roles rule. Fixed 0227; the three field-list cases stayed Fail, with the judge placing the field line in the wrong form.

No further revisions: the handout allows two.

## Frozen version

**v2**, frozen 2026-11-13. Reason: v2 changes how the judge reasons. Statements made by the agent that could be policy backed should include it. Only statements made to the user are verdicts, record facts are not. Label the reasoning and check the citation. 

Remaining known weakness: across all three versions, `gpt-4o-mini` reads a refund-eligibility field in an order list as a verdict, against the human boundary. The training split has no Pass example of that pattern, and a constructed example was not used.

## Test result (frozen v2, run once)

Test split: 41 conversations (18 Pass, 23 Fail). Judge model `gpt-4o-mini`. Metrics: `analysis/report/test-uncited_policy_claim-v2.json`.

| TP | FN | TN | FP | TPR (95% Wilson) | TNR (95% Wilson) |
| ---: | ---: | ---: | ---: | --- | --- |
| 15 | 3 | 22 | 1 | 0.833 (0.608–0.942) | 0.957 (0.790–0.992) |

| Conversation | Human | Judge | Judge critique |
| --- | --- | --- | --- |
| support-0150 | Pass | Fail | Read a refund-eligibility field in the order list as a verdict (the known development weakness). |
| support-0232 | Pass | Fail | Read "permission restrictions as a merchant" as an uncited roles rule. The conversation fails a different mode, `escalation_offered_after_denial`. |
| support-0141 | Pass | Fail | Found both cited identifiers in the same reply but required each claim to be cited next to it, against the "citation anywhere" rule. |
| support-0055 | Fail | Pass | Read "hasn't shipped yet. I can proceed to cancel it" as record facts and an action, not a cancellation verdict. |

No labels or prompts were changed after the test run.

Would I use this judge: PENDING — student to write this in their own words.
