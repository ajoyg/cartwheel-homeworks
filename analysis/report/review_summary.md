# HW4 review summary

## Sample

I reviewed **109 distinct conversations** from the 250 `support-*` conversations recorded in Homework 3. One conversation is one review item, even when it has two turns (one Langfuse trace per turn), and its ID is the first turn's trace ID. The sample has 66 shopper, 22 merchant and 21 support conversations; 18 have two turns. No conversation appears in more than one batch.

| Batch | Conversations | How it was chosen |
| --- | --- | --- |
| `initial_random` | 15 | Uniform random, seed 20261028 |
| `cluster` | 15 | Nearest to the centre of k-means clusters on conversation features, with no outcome data |
| `dimension` | 30 | 10 per user role, random within role, seed 20261030 |
| `depth` | 25 | One deterministic retrieval signal per candidate mode, seed 20261031 |
| `final_random` | 15 | Uniform random, seed 20261032, no suggestions shown |
| `search_round2` | 9 | The repeat search below |

Part C (Raindrop Workshop) was skipped.

## New modes in the final 15

**One** of the final 15 produced a previously unseen consequential mode: support-0183, where the agent says socks were in order 1982, but `list_my_orders` returns no product names and 1982 is a different product. I recorded it as `unsupported_record_match` under `unadopted_modes` in `patterns.json` and did not adopt it. It has one confirmed positive, the taxonomy is at the 8-mode limit, and refund code already checks ownership and eligibility. It therefore has a failure note but no final mode. One new mode did not call for another batch.

## Sample fractions

These are fractions of the reviewed sample, **not prevalence estimates**. Clustering, filtering and focused searches deliberately changed the composition of the sample. Every mode was judged present or absent on every conversation: 872 judgments, with matching Langfuse scores.

| Mode | Present | Fraction | Close negatives | Evaluator | Requirement |
| --- | --- | --- | --- | --- | --- |
| `uncited_policy_claim` | 56 | 51.4% | 3 | LLM judge | RESP-1 |
| `incorrect_policy_claim` | 14 | 12.8% | 12 | LLM judge | PURPOSE-1, RESP-1, AUTH-1 |
| `asks_instead_of_acting` | 12 | 11.0% | 3 | LLM judge | ESC-1, ESC-2, PURPOSE-1 |
| `missing_data_not_flagged` | 6 | 5.5% | 3 | Code | RESP-3, ESC-4 |
| `escalation_offered_after_denial` | 5 | 4.6% | 1 | LLM judge | AUTH-1, RESP-4 |
| `incomplete_answer` | 4 | 3.7% | 4 | Code | RESP-7 |
| `malformed_tool_argument` | 4 | 3.7% | 3 | Code | Section 4 (TOOL-3, TOOL-6), RESP-5 |
| `cancellation_payment_not_explained` | 3 | 2.8% | 4 | LLM judge | RESP-6 |

80 conversations have at least one mode present and 29 have none. `escalation_offered_after_denial` has only 1 close negative because the data does not offer more: a text search of the 9 permission denials in all 250 conversations finds only support-0227 with no offer to escalate.

## A taxonomy revision

`incorrect_policy_claim` first said a reply "contradicts" policy. Support-0062 added that refund timing depends on the bank, which no policy says, so I widened it to "contradicts or adds to". I also considered a separate store-policy mode for 0211 and 0214 and rejected it, because the same product change fixes both, and filed them here.

The second search round then exposed a mistake in the boundary, which read "verdicts that ignore or contradict a store's own return policy". I accepted 0208 and 0213 but rejected 0212, although all three give the same correct verdict from the `refund_eligible` flag and state no rule. "Ignore" described how the agent worked, not what the reply said. I removed it: a correct verdict with no rule stated is `uncited_policy_claim`, even if the store's policy was never read. The same round found 0216, which promises money back on an opened item against Cascade Audio's restocking fee, as the clear case the boundary should catch.

## Searches

The depth batch searched for each candidate mode. I then revised definitions and searched again for `incorrect_policy_claim`, covering 7 new store-policy conversations and rechecks of 10 earlier ones, plus 2 close-negative candidates for `malformed_tool_argument`. Of 44 suggestions, 19 were accepted and 25 rejected, each rejection with a recorded boundary. For example, sug-040 (0148, "Order IDs usually do not match user IDs") was rejected because that is not a policy rule. The recheck found 0070, first reviewed as clean: it says 24 to 48 hours where `cw-escalations` says within 24 hours.

The search signal has a known limit. It relies on `order.store_name`, but `find_order` returns no store name, so support-0210 was not retrieved.

## SPEC.md revisions

- **RESP-6** (buyers are told a cancellation is at no cost, with full payment returned, citing `cw-cancellations`). Motivating annotation: `ann-mvsyf51z-4op1` on support-0055.
- **RESP-7** (status answers include the matching date; partial lists say how many were returned). Motivating annotation: `ann-mvsy7l4m-ihgk` on support-0149.

A proposed RESP-8 for support-0183 was not adopted. These edits do not change the running application.

## AgentDebug comparison

I compared the taxonomy with Table 2 in the AgentDebug paper and added no mode. Parameter Error corresponds to `malformed_tool_argument`, Planning-Action Disconnect to `asks_instead_of_acting`, and Outcome Misinterpretation to `missing_data_not_flagged`. Causal Misattribution appears in the replies of 0085 and 0193 and has the same fix as `malformed_tool_argument`, so it stayed merged. A regex over all 250 conversations for Progress Misassessment (RESP-2) returned 2 false hits. `uncited_policy_claim`, `incomplete_answer` and `cancellation_payment_not_explained` have no counterpart, since they come from this product's requirements.

## Limits of this review

- **Notes and labels differ.** Notes record my first read, and labels are the final judgment. Of 55 conversations first noted as clean, 27 now have a mode present, 23 of them only `uncited_policy_claim`, after a filter for verdicts with no policy identifier disagreed with my labels. Two were real misses: 0118 and 0127. The note history is kept.
- **Human review with assistance.** I made the decisions. Claude Code built the interface, ran searches and filters, and checked my labels against the traces. Where it drafted the wording of a note from my decision, the note's `revision_reason` says so. 16 suggestion decisions were corrected after these checks, with `decision_history` kept.
- **Scenario flaw.** Support-0029's scripted second turn asks for a refund that was already issued. The agent handled it correctly, so the turn is a scenario problem and not an agent failure.
- **Homework 5.** Only `uncited_policy_claim` has at least 30 Pass and 30 Fail labels. The other modes need synthetic scenarios.
