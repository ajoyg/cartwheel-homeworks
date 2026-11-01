"""Add a review batch to ``analysis/state/sample_manifest.json``.

Selection never reads outcomes, answer keys or annotations: it uses only the
conversation's shape, so a batch is not chosen because a failure is predicted.
Conversations already in the manifest are excluded, so no conversation counts
toward more than one batch.

Run from the repository root (the review app's cache must exist; start the
server once to build it):

    .venv/bin/python analysis/review_app/select_batch.py initial
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from analysis.helpers.selection import _kmeans, _standardize  # noqa: E402

CACHE = HERE / "cache" / "conversations.json"
MANIFEST = HERE.parent / "state" / "sample_manifest.json"
SUGGESTIONS = HERE.parent / "state" / "suggestions.json"
PATTERNS = HERE.parent / "state" / "patterns.json"
FEATURES = ("turn_count", "tool_call_count", "distinct_tools", "has_retrieval", "words")
RETRIEVAL_TOOLS = {"search_help_center", "get_policy"}


def features(conversation: dict) -> dict[str, float]:
    """The course clustering features, computed over the whole conversation."""
    messages = [m for turn in conversation["turns"] for m in turn["messages"]]
    calls = [m for m in messages if m["role"] == "tool_call"]
    tools = {m.get("name") for m in calls}
    words = sum(len(str(m.get("text") or m.get("content") or "").split()) for m in messages)
    return {
        "turn_count": len(conversation["turns"]),
        "tool_call_count": len(calls),
        "distinct_tools": len(tools),
        "has_retrieval": int(bool(tools & RETRIEVAL_TOOLS)),
        "words": words,
    }


def cluster_representatives(pool: list[dict], k: int, n_clusters: int, seed: int) -> list[dict]:
    """Pick the members nearest each k-means centroid, spread across clusters."""
    vectors = _standardize([[features(c)[f] for f in FEATURES] for c in pool])
    assign = _kmeans(vectors, k=n_clusters, seed=seed)
    by_cluster: dict[int, list[int]] = {}
    for i, c in enumerate(assign):
        by_cluster.setdefault(c, []).append(i)
    ranked = {}
    for c, members in by_cluster.items():
        centroid = [sum(vectors[i][d] for i in members) / len(members) for d in range(len(FEATURES))]
        ranked[c] = sorted(members, key=lambda i: math.dist(vectors[i], centroid))
    picks, depth = [], 0
    order = sorted(ranked, key=lambda c: -len(ranked[c]))  # larger clusters first on ties
    while len(picks) < k and any(len(ranked[c]) > depth for c in order):
        for c in order:
            if len(picks) < k and len(ranked[c]) > depth:
                conv = pool[ranked[c][depth]]
                f = features(conv)
                picks.append((conv, f"cluster {c} ({len(ranked[c])} conversations), "
                              f"rank {depth + 1} nearest the centre; " +
                              ", ".join(f"{name}={f[name]:g}" for name in FEATURES)))
        depth += 1
    return picks


# ---------------------------------------------------------------------------
# depth searches: retrieval signals per candidate mode (suggestions, not labels)
# ---------------------------------------------------------------------------

CITATION = re.compile(r"\b(cw-[a-z-]+|store-[a-z-]+-policy)\b")
DATE = re.compile(r"\b(20\d\d|January|February|March|April|May|June|July|August|September|October|November|December)\b")


def _located(conversation: dict):
    for t, turn in enumerate(conversation["turns"]):
        for i, m in enumerate(turn["messages"]):
            yield t, i, m


def _result(m: dict) -> dict:
    r = m.get("content")
    if isinstance(r, str):
        try:
            r = json.loads(r)
        except ValueError:
            return {}
    return r if isinstance(r, dict) else {}


def _call_index(conversation: dict, t: int, i: int) -> int:
    """The review app anchors a tool box at its tool_call message."""
    messages = conversation["turns"][t]["messages"]
    return i - 1 if i > 0 and messages[i - 1]["role"] == "tool_call" else i


def _first_sentence(text: str) -> str:
    return re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0][:160]


def _replies(conversation: dict):
    return [(t, i, m) for t, i, m in _located(conversation) if m["role"] == "assistant" and m.get("text")]


def _damaged(record: dict) -> bool:
    if record.get("status") in ("delivered", "refunded") and "delivered_at" in record and not record.get("delivered_at"):
        return True
    if record.get("shipped_at") and record.get("delivered_at") and record["delivered_at"] < record["shipped_at"]:
        return True
    if "price_usd" in record and (record.get("price_usd") or 0) < 0:
        return True
    return "title" in record and not (record.get("title") or "").strip()


def signal(mode: str, c: dict):
    """Return (turn, message, quote, rationale) for the first hit, or None."""
    if mode == "escalation_offered_after_denial":
        for t, i, m in _located(c):
            if m["role"] == "tool_result" and _result(m).get("error") == "permission_denied":
                return t, _call_index(c, t, i), "permission_denied", f"{m.get('name')} returned permission_denied; check whether the reply offers escalation or another route to the data"
    if mode == "cancellation_payment_not_explained":
        for t, i, m in _located(c):
            if m["role"] == "tool_result" and m.get("name") == "cancel_order" and _result(m).get("ok"):
                later = [(rt, ri, rm) for rt, ri, rm in _replies(c) if (rt, ri) > (t, i)]
                if later:
                    rt, ri, rm = later[0]
                    return rt, ri, _first_sentence(rm["text"]), "cancel_order succeeded; check whether the reply says the cancellation is at no cost and the payment is returned (RESP-6)"
    if mode == "malformed_tool_argument":
        for t, i, m in _located(c):
            error = _result(m).get("error") if m["role"] == "tool_result" else None
            if error in ("not_found", "invalid_argument"):
                return t, _call_index(c, t, i), error, f"{m.get('name')} returned {error}; check whether an argument was malformed"
    if mode == "missing_data_not_flagged":
        for t, i, m in _located(c):
            if m["role"] != "tool_result":
                continue
            r = _result(m)
            records = ([r["order"]] if isinstance(r.get("order"), dict) else []) + list(r.get("orders") or []) + list(r.get("products") or [])
            if any(_damaged(x) for x in records if isinstance(x, dict)):
                return t, _call_index(c, t, i), "", f"{m.get('name')} returned a damaged record (missing or reversed dates, negative price or empty title); check whether the reply flags it (RESP-3)"
    if mode == "incorrect_policy_claim":
        for t, i, m in _replies(c):
            hit = re.search(r"[^.!?\n]*\b(bank|card provider|chargeback|dispute)[^.!?\n]*", m["text"], re.I)
            if hit:
                return t, i, hit.group(0).strip()[:160], "reply mentions a bank, card provider, chargeback or dispute; check the stated rule against cw-disputes"
    if mode == "uncited_policy_claim":
        text = " ".join(m["text"] for _, _, m in _replies(c))
        if CITATION.search(text):
            return None
        for t, i, m in _replies(c):
            hit = re.search(r"[^.!?\n]*(not eligible|return window|within \d+ days|policy|payout)[^.!?\n]*", m["text"], re.I)
            if hit:
                return t, i, hit.group(0).strip()[:160], "reply makes an eligibility, window, policy or payout statement and no reply cites a policy identifier"
    if mode == "incomplete_answer":
        called = any(m["role"] == "tool_call" and m.get("name") in ("get_order", "list_my_orders") for _, _, m in _located(c))
        replies = _replies(c)
        if called and replies and not any(DATE.search(m["text"]) for _, _, m in replies):
            t, i, m = replies[-1]
            return t, i, _first_sentence(m["text"]), "get_order or list_my_orders was called and no reply states a date; check RESP-7"
    if mode == "asks_instead_of_acting":
        replies = _replies(c)
        if replies and replies[-1][2]["text"].rstrip().endswith("?"):
            t, i, m = replies[-1]
            question = re.split(r"(?<=[.!])\s", m["text"].strip())[-1][:160]
            return t, i, question, "the final reply ends with a question; check whether it asks for something the request already gave or the tool does not need (possible close negative)"
    return None


POLICIES = HERE.parent.parent / "data" / "policies"
LOOKUPS = {"get_policy", "search_help_center"}
RETURN_QUESTION = re.compile(r"\b(return|refund|money back|restock\w*|exchange)\b", re.I)
SOFTENING = re.compile(r"[^.!?\n]*\b(typically|generally|usually|normally)\b[^.!?\n]*", re.I)
# Named when the student approved round 2: store-name searches that succeed.
MALFORMED_NEGATIVES = ("support-0191", "support-0194")


def override_stores() -> set[str]:
    """Stores with their own policy page, from each page's title."""
    names = set()
    for path in POLICIES.glob("store-*-policy.md"):
        title = re.search(r"^title: (.+) store policy$", path.read_text(), re.M)
        if title:
            names.add(title.group(1).strip())
    return names


def store_policy_signal(c: dict, stores: set[str]):
    """A return or refund question on an order from a store with its own policy, and no policy was looked up."""
    located = list(_located(c))
    asked = " ".join(m.get("text") or "" for _, _, m in located if m["role"] == "user")
    if not RETURN_QUESTION.search(asked):
        return None
    if any(m["role"] == "tool_call" and m.get("name") in LOOKUPS for _, _, m in located):
        return None
    for _, _, m in located:
        if m["role"] != "tool_result":
            continue
        order = _result(m).get("order")
        if isinstance(order, dict) and order.get("store_name") in stores:
            replies = _replies(c)
            if replies:
                t, i, reply = replies[-1]
                return t, i, _first_sentence(reply["text"]), f"order from {order['store_name']}, which has its own policy, and no policy was looked up; check the reply against the store's policy"
    return None


def softening_signal(c: dict):
    for t, i, m in _replies(c):
        hit = SOFTENING.search(m["text"])
        if hit:
            return t, i, hit.group(0).strip()[:160], "reply states a rule with a softening word (typically, generally, usually, normally); check it against the policy text"
    return None


DEPTH_PLAN = [
    ("escalation_offered_after_denial", 4),
    ("cancellation_payment_not_explained", 4),
    ("missing_data_not_flagged", 4),
    ("incorrect_policy_claim", 3),
    ("uncited_policy_claim", 3),
    ("incomplete_answer", 3),
    ("malformed_tool_argument", 1),
    ("asks_instead_of_acting", 3),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("batch", choices=["initial", "dimension", "depth", "final", "round2"])
    parser.add_argument("--seed", type=int, default=20261028)
    args = parser.parse_args()

    conversations = json.loads(CACHE.read_text())["conversations"]
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {"items": [], "batches": {}}
    taken = {item["conversation_id"] for item in manifest["items"]}
    pool = sorted((c for c in conversations if c["id"] not in taken), key=lambda c: c["scenario_id"])
    added_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def add(conv: dict, batch: str, reason: str) -> None:
        manifest["items"].append({"conversation_id": conv["id"], "scenario_id": conv["scenario_id"],
                                  "batch": batch, "reason": reason, "added_at": added_at})

    if args.batch == "initial":
        if any(i["batch"] in ("initial_random", "cluster") for i in manifest["items"]):
            raise SystemExit("batch 1 is already in the manifest")
        rng = random.Random(args.seed)
        randoms = rng.sample(pool, 15)
        for conv in randoms:
            add(conv, "initial_random", f"uniform random draw (seed {args.seed})")
        rest = [c for c in pool if c not in randoms]
        n_clusters = 8
        for conv, reason in cluster_representatives(rest, k=15, n_clusters=n_clusters, seed=7):
            add(conv, "cluster", reason)
        manifest.setdefault("batches", {})
        manifest["batches"]["initial_random"] = {
            "method": "uniform random sample of all conversations", "seed": args.seed, "size": 15, "added_at": added_at}
        manifest["batches"]["cluster"] = {
            "method": f"k-means ({n_clusters} clusters, course helper) on standardized {', '.join(FEATURES)}; "
                      "nearest-to-centre members, spread across clusters; random picks excluded",
            "seed": 7, "size": 15, "added_at": added_at}

    if args.batch == "dimension":
        # The student chose user role before seeing batch 2 outcomes: 10 per role,
        # uniform random within each role.
        if any(i["batch"] == "dimension" for i in manifest["items"]):
            raise SystemExit("the dimension batch is already in the manifest")
        rng = random.Random(args.seed + 2)
        for role in ("shopper", "merchant", "support"):
            members = [c for c in pool if c["role"] == role]
            for conv in rng.sample(members, 10):
                add(conv, "dimension", f"user role = {role}; uniform random within the role (seed {args.seed + 2})")
        manifest["batches"]["dimension"] = {
            "method": "dimension chosen before review: user role; 10 conversations per role, uniform random within each role",
            "dimension": "role", "seed": args.seed + 2, "size": 30, "added_at": added_at}

    if args.batch == "depth":
        if any(i["batch"] == "depth" for i in manifest["items"]):
            raise SystemExit("the depth batch is already in the manifest")
        rng = random.Random(args.seed + 3)
        suggestions = json.loads(SUGGESTIONS.read_text()) if SUGGESTIONS.exists() else []
        chosen: set[str] = set()
        for mode, k in DEPTH_PLAN:
            hits = [(c, s) for c in pool if c["id"] not in chosen and (s := signal(mode, c))]
            for conv, (t, i, quote, rationale) in rng.sample(hits, min(k, len(hits))):
                chosen.add(conv["id"])
                add(conv, "depth", f"depth search for {mode}: {rationale}")
                suggestions.append({
                    "id": f"sug-{len(suggestions) + 1:03d}", "mode": mode, "conversation_id": conv["id"],
                    "scenario_id": conv["scenario_id"], "turn_index": t, "msg_index": i, "quote": quote,
                    "rationale": rationale, "method": "deterministic filter on the trace (retrieval signal, not a label)",
                    "search_round": 1, "status": "pending", "created_by": "agent", "created_at": added_at,
                })
        manifest["batches"]["depth"] = {
            "method": "depth searches: one deterministic retrieval signal per candidate mode, uniform random among hits; "
                      "each pick is also a pending suggestion for the human to accept or reject",
            "plan": dict(DEPTH_PLAN), "seed": args.seed + 3, "size": len(chosen), "added_at": added_at}
        SUGGESTIONS.write_text(json.dumps(suggestions, indent=2) + "\n")

    if args.batch == "final":
        if any(i["batch"] == "final_random" for i in manifest["items"]):
            raise SystemExit("the final batch is already in the manifest")
        seed = args.seed + 4
        for conv in random.Random(seed).sample(pool, 15):
            add(conv, "final_random", f"uniform random draw from unreviewed conversations (seed {seed})")
        manifest["batches"]["final_random"] = {
            "method": "uniform random sample of the conversations not yet reviewed, drawn after the taxonomy draft",
            "seed": seed, "size": 15, "added_at": added_at}

    if args.batch == "round2":
        # Second search for incorrect_policy_claim, after its definition was
        # widened. New picks join the sample; recheck suggestions cover reviewed ones.
        if any(i["batch"] == "search_round2" for i in manifest["items"]):
            raise SystemExit("the second search round is already in the manifest")
        mode = "incorrect_policy_claim"
        suggestions = json.loads(SUGGESTIONS.read_text())
        patterns = json.loads(PATTERNS.read_text())
        already = set(next(m for m in patterns["modes"] if m["name"] == mode)["example_trace_ids"])
        stores = override_stores()
        by_id = {c["id"]: c for c in conversations}

        def suggest(conv: dict, name: str, hit: tuple, method: str) -> None:
            t, i, quote, rationale = hit
            suggestions.append({
                "id": f"sug-{len(suggestions) + 1:03d}", "mode": name, "conversation_id": conv["id"],
                "scenario_id": conv["scenario_id"], "turn_index": t, "msg_index": i, "quote": quote,
                "rationale": rationale, "method": method, "search_round": 2,
                "status": "pending", "created_by": "agent", "created_at": added_at,
            })

        filter_method = "deterministic filter on the trace (retrieval signal, not a label)"
        picked = 0
        for conv in pool:
            hit = store_policy_signal(conv, stores)
            if hit:
                add(conv, "search_round2", f"second search for {mode}: {hit[3]}")
                suggest(conv, mode, hit, filter_method)
                picked += 1
        for conv in (c for c in pool if c["scenario_id"] in MALFORMED_NEGATIVES):
            t, i = next((t, i) for t, i, m in _located(conv) if m["role"] == "tool_call" and m.get("name") == "search_products")
            rationale = "search_products was given a store name and succeeded; possible close negative"
            add(conv, "search_round2", f"close negative search for malformed_tool_argument: {rationale}")
            suggest(conv, "malformed_tool_argument", (t, i, "", rationale), filter_method)
            picked += 1
        # Recheck: reviewed conversations not yet positive for the revised mode.
        rechecked = 0
        for conv_id in sorted(taken - already, key=lambda x: by_id[x]["scenario_id"]):
            conv = by_id[conv_id]
            hit = store_policy_signal(conv, stores) or softening_signal(conv)
            if conv["scenario_id"] in ("support-0166", "support-0172") and not hit:
                replies = _replies(conv)
                t, i, reply = replies[-1]
                hit = (t, i, _first_sentence(reply["text"]), "rejected under the earlier bank search; recheck against the widened definition")
            if hit:
                suggest(conv, mode, hit, filter_method + "; recheck of a reviewed conversation")
                rechecked += 1
        manifest["batches"]["search_round2"] = {
            "method": "second search after the incorrect_policy_claim definition was widened: orders from stores with their "
                      "own policy where no policy was looked up, plus two store-name searches as malformed_tool_argument close "
                      "negatives. Extends the depth searches and is kept separate from the four required batches.",
            "size": picked, "recheck_suggestions": rechecked, "added_at": added_at}
        SUGGESTIONS.write_text(json.dumps(suggestions, indent=2) + "\n")

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    for item in manifest["items"]:
        print(f"{item['batch']:15} {item['scenario_id']}  {item['reason']}")
    print(f"manifest: {len(manifest['items'])} conversations")


if __name__ == "__main__":
    main()
