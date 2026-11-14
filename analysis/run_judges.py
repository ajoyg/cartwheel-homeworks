"""Homework 5: judge inputs, label splits, judge runs, and metrics.

Run from the repository root:

    .venv/bin/python -m analysis.run_judges prepare
    .venv/bin/python -m analysis.run_judges split
    .venv/bin/python -m analysis.run_judges dev analysis/prompts/<mode>-v0.txt
    .venv/bin/python -m analysis.run_judges freeze <judge_id>
    .venv/bin/python -m analysis.run_judges test <judge_id>

The judge input is built from the committed Homework 3 export
(``traces/support_traces.json``) with the same conversation grouping the
review interface uses. It holds only what the agent saw and did: user turns,
assistant replies, tool calls and tool results (including the policy text
``search_help_center`` returned). Labels, review notes, scenario ids, user
roles and answer keys stay out, so nothing leaks the answer to the judge.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from analysis.helpers import _state
from analysis.helpers.guards import is_frozen
from analysis.helpers.tools import (
    _load_judge,
    freeze_judge,
    judge_alignment,
    register_judge,
    run_judge,
    split_labels,
)
from analysis.review_app.server import _load_export, build_conversations

MODE = "uncited_policy_claim"
ROOT = Path(__file__).resolve().parents[1]
INPUTS_PATH = ROOT / "analysis" / "state" / "hw5_trace_inputs.json"
HW5_LABELS_DIR = ROOT / "analysis" / "state" / "hw5_labels"
REPORT_DIR = ROOT / "analysis" / "report"
JUDGE_MODEL = "gpt-4o-mini"

# Close scenario variants: conversations built on the same planted record.
# One record per group is evaluated; the others keep their labels but are
# not judge inputs. The student kept the lowest-numbered scenario of each
# group on 2026-11-08.
EXCLUDED_VARIANTS: dict[str, str] = {
    "support-0177": "dq-order-missing-delivery-date (order 8002), kept support-0176",
    "support-0180": "dq-order-missing-delivery-date (order 8002), kept support-0176",
    "support-0182": "dq-order-reversed-dates (order 8001), kept support-0181",
    "support-0183": "dq-order-reversed-dates (order 8001), kept support-0181",
    "support-0193": "dq-product-duplicate-title (Heavy-Duty Vase), kept support-0191",
    "support-0194": "dq-product-duplicate-title (Heavy-Duty Vase), kept support-0191",
    "support-0199": "dq-product-invalid-price (Rustic Pitcher), kept support-0196",
    "support-0200": "dq-product-invalid-price (Rustic Pitcher), kept support-0196",
}


def _hw5_labels(mode: str) -> list[dict[str, Any]]:
    rows = _state.read_jsonl(HW5_LABELS_DIR / f"{mode}.jsonl")
    live = {row["trace_id"]: row for row in rows if not row.get("superseded_by")}
    return list(live.values())


def _judge_message(message: dict[str, Any]) -> dict[str, Any] | None:
    """Keep a message's role and its text or tool data, nothing else."""
    role = message.get("role")
    if role in ("user", "assistant"):
        text = message.get("text") or ""
        return {"role": role, "text": text} if text.strip() else None
    if role == "tool_call":
        name = message.get("name") or "tool"
        args = message.get("arguments") or ""
        return {"role": "tool_call", "name": name, "arguments": f"{name}({args})"}
    if role == "tool_result":
        name = message.get("name") or "tool"
        content = message.get("content")
        content = content if isinstance(content, str) else json.dumps(content)
        return {"role": "tool_result", "name": name, "content": content,
                "text": f"[{name}] {content}"}
    return None


def prepare_inputs(mode: str = MODE) -> list[dict[str, Any]]:
    """Write one judge input record per eligible labeled conversation."""
    labels = {row["trace_id"]: row for row in _hw5_labels(mode)}
    if not labels:
        raise ValueError(f"no HW5 labels for {mode}; export them first")
    excluded = {sid for sid in EXCLUDED_VARIANTS}
    conversations = {conv["id"]: conv for conv in build_conversations(_load_export())}

    records = []
    for trace_id, row in sorted(labels.items(), key=lambda kv: kv[1].get("scenario_id", "")):
        if row.get("scenario_id") in excluded:
            continue
        conv = conversations.get(trace_id)
        if conv is None:
            raise ValueError(f"labeled conversation {trace_id} is missing from the export")
        messages = []
        for turn in conv["turns"]:
            for message in turn["messages"]:
                kept = _judge_message(message)
                if kept:
                    messages.append(kept)
        records.append({"trace_id": trace_id, "trace": messages})

    eligible = {tid for tid, row in labels.items() if row.get("scenario_id") not in excluded}
    if {r["trace_id"] for r in records} != eligible:
        raise AssertionError("judge inputs do not match the eligible labels one to one")
    INPUTS_PATH.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    return records


def split_data(mode: str = MODE) -> dict[str, list[str]]:
    """Split the eligible HW5 labels 20/40/40 (train/dev/test). Run once."""
    records = json.loads(INPUTS_PATH.read_text())
    return split_labels(
        mode,
        fractions=(0.20, 0.40, 0.40),
        seed=7,
        min_per_class=10,
        eligible_trace_ids=[record["trace_id"] for record in records],
    )


def _use_saved_inputs() -> None:
    """Judge the saved export, never a fresh Langfuse fetch, and read the
    model key from the local .env without printing it."""
    load_dotenv(ROOT / ".env", override=False)
    os.environ["CARTWHEEL_JUDGE_TRACE_SOURCE"] = str(INPUTS_PATH)


def _judge_for_prompt(mode: str, prompt_text: str) -> str:
    """Reuse the registered judge for this exact prompt, else register one.

    Resuming an interrupted run must not register a new version.
    """
    history = _state.read_json(_state.state_path("judges", f"_history_{mode}.json"),
                               default={"versions": []})
    for version in history["versions"]:
        judge = _load_judge(version["judge_id"])
        if judge["prompt_text"] == prompt_text and judge["model"] == JUDGE_MODEL:
            return judge["judge_id"]
    return register_judge(mode=mode, prompt_text=prompt_text, judge_model=JUDGE_MODEL)["judge_id"]


def run_development(mode: str, prompt_path: str | Path) -> dict[str, Any]:
    """Run one prompt version on the development split and save its metrics."""
    _use_saved_inputs()
    prompt_path = Path(prompt_path)
    judge_id = _judge_for_prompt(mode, prompt_path.read_text(encoding="utf-8"))
    run_judge(judge_id, split="dev", batch_size=10)
    development = judge_alignment(judge_id, split="dev")
    development["prompt_path"] = str(prompt_path.relative_to(ROOT) if prompt_path.is_absolute()
                                     else prompt_path)
    development["model"] = JUDGE_MODEL
    out = REPORT_DIR / f"dev-{judge_id}.json"
    out.write_text(json.dumps(development, indent=2) + "\n", encoding="utf-8")
    return development


def freeze(judge_id: str) -> dict[str, Any]:
    """Freeze the chosen version once. Freezing is one-way per version."""
    judge = _load_judge(judge_id)
    return judge if is_frozen(judge) else freeze_judge(judge_id)


def run_test(judge_id: str) -> dict[str, Any]:
    """Freeze the chosen judge, run it once on the test split, save metrics.

    A rerun after an interruption reuses the frozen judge and the cached
    predictions, so only missing test traces are sent to the model.
    """
    _use_saved_inputs()
    judge = freeze(judge_id)
    run_judge(judge_id, split="test", batch_size=10)
    test = judge_alignment(judge_id, split="test")
    test["model"] = judge["model"]
    test["frozen_at"] = judge["frozen_at"]
    out = REPORT_DIR / f"test-{judge_id}.json"
    out.write_text(json.dumps(test, indent=2) + "\n", encoding="utf-8")
    return test


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["prepare", "split", "dev", "freeze", "test"])
    parser.add_argument("target", nargs="?", help="prompt file (dev) or judge id (freeze, test)")
    parser.add_argument("--mode", default=MODE)
    args = parser.parse_args()
    if args.command == "prepare":
        records = prepare_inputs(args.mode)
        print(f"wrote {len(records)} judge inputs to {INPUTS_PATH.relative_to(ROOT)}")
    elif args.command == "split":
        splits = split_data(args.mode)
        labels = {row["trace_id"]: row["label"] for row in _hw5_labels(args.mode)}
        for name, ids in splits.items():
            passes = sum(labels[t] == 1 for t in ids)
            print(f"{name:5} {len(ids):3}  Pass {passes:3}  Fail {len(ids) - passes:3}")
    elif args.command == "dev":
        if not args.target:
            parser.error("dev needs a prompt file")
        m = run_development(args.mode, args.target)
        print(f"{m['judge_id']} on dev (n={m['n']}): TP {m['tp']} FN {m['fn']} TN {m['tn']} FP {m['fp']}")
        print(f"TPR {m['tpr']:.3f} {m['tpr_interval']}  TNR {m['tnr']:.3f} {m['tnr_interval']}")
        print(f"{len(m['disagreements'])} disagreements; saved analysis/report/dev-{m['judge_id']}.json")
    elif args.command == "freeze":
        judge = freeze(args.target)
        print(f"{judge['judge_id']} frozen at {judge['frozen_at']}")
    elif args.command == "test":
        m = run_test(args.target)
        passes, fails = m["tp"] + m["fn"], m["tn"] + m["fp"]
        print(f"{m['judge_id']} on test (n={m['n']}, {passes} Pass, {fails} Fail): "
              f"TP {m['tp']} FN {m['fn']} TN {m['tn']} FP {m['fp']}")
        print(f"TPR {m['tpr']:.3f} {m['tpr_interval']}  TNR {m['tnr']:.3f} {m['tnr_interval']}")
        print(f"saved analysis/report/test-{m['judge_id']}.json")


if __name__ == "__main__":
    main()
