"""Recalculate HW5 test metrics from saved files, with no model calls.

    .venv/bin/python -m analysis.recalc_test uncited_policy_claim-v2

Reads three saved files and nothing else:
- analysis/state/splits.json: which conversations are in the test split
- analysis/state/hw5_labels/<mode>.jsonl: human labels (1 = Pass, 0 = Fail)
- analysis/state/judges/<judge_id>.json: the frozen judge's saved verdicts
  (stored with 1 = Pass, 0 = Fail, under the frozen prompt's hash)

Pass is the positive class. The Wilson interval is computed here by hand
rather than imported, so the arithmetic is visible.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "analysis" / "state"


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def main(judge_id: str) -> None:
    judge = json.loads((STATE / "judges" / f"{judge_id}.json").read_text())
    mode = judge["mode"]
    assert judge["status"] == "frozen", "test metrics only for a frozen judge"
    assert judge["label_convention"] == "pass_positive"

    test_ids = json.loads((STATE / "splits.json").read_text())[mode]["test"]
    human = {}
    for line in (STATE / "hw5_labels" / f"{mode}.jsonl").read_text().splitlines():
        row = json.loads(line)
        human[row["trace_id"]] = (row["label"], row["scenario_id"])
    verdict = judge["predictions"][judge["prompt_hash"]]

    tp = fn = tn = fp = 0
    print(f"{judge_id} ({judge['model']}), frozen {judge['frozen_at'][:19]}")
    print(f"test conversations: {len(test_ids)}\n")
    for tid in test_ids:
        h, scenario = human[tid]
        j = verdict[tid]
        if h == 1 and j == 1:
            tp += 1
        elif h == 1 and j == 0:
            fn += 1
        elif h == 0 and j == 0:
            tn += 1
        else:
            fp += 1
        if h != j:
            name = lambda v: "Pass" if v == 1 else "Fail"
            print(f"  disagreement {scenario}: human {name(h)}, judge {name(j)}")

    print(f"\nhuman Pass {tp + fn}, human Fail {tn + fp}")
    print(f"TP {tp}  FN {fn}  TN {tn}  FP {fp}")
    lo, hi = wilson(tp, tp + fn)
    print(f"TPR = {tp}/{tp + fn} = {tp / (tp + fn):.3f}   95% Wilson [{lo:.3f}, {hi:.3f}]")
    lo, hi = wilson(tn, tn + fp)
    print(f"TNR = {tn}/{tn + fp} = {tn / (tn + fp):.3f}   95% Wilson [{lo:.3f}, {hi:.3f}]")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "uncited_policy_claim-v2")
