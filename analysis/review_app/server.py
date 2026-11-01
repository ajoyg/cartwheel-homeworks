"""Conversation review server for Homework 4.

Adapted from the reference ``analysis/server.py``: a standard-library HTTP
server over plain JSON files in ``analysis/state/``. The differences come from
inspecting the Module 1 traces:

- Cartwheel writes one Langfuse trace per user turn and records the session
  only as ``cartwheel.session_id`` metadata, so the Langfuse Sessions view
  cannot join the turns. This server groups the turns into conversations by
  that attribute and orders them by timestamp. A conversation is the review
  unit; its identifier is the Langfuse identifier of its first turn, which is
  also the identifier the course normalizer keeps when it merges turns.
- Binary judgments are a separate structured layer. Free-text open codes are
  saved to ``annotations.json``; present/absent labels are appended to
  ``labels/<mode>.jsonl`` and written to Langfuse as scores (1 = failure
  present) on the conversation's first trace.

Run from the repository root:

    uv run python analysis/review_app/server.py              # live Langfuse
    uv run python analysis/review_app/server.py --refresh    # re-fetch traces
    uv run python analysis/review_app/server.py --source export

API:

    GET  /                          the review app
    GET  /api/conversations         every conversation (turns, messages, answer key)
    GET  /api/manifest              the review sample (``sample_manifest.json``)
    POST /api/manifest              replace the review sample
    GET  /api/annotations           human open codes
    POST /api/annotations           save open codes (the app posts on every change)
    GET  /api/patterns              the taxonomy
    POST /api/patterns              replace the taxonomy
    GET  /api/suggestions           AI search suggestions
    POST /api/suggestions           replace the suggestion list
    POST /api/suggestions/decide    accept or reject one suggestion
    GET  /api/labels                live present/absent labels by mode
    GET  /api/labels/sync           Langfuse score writes still queued or failed
    POST /api/labels                record one label (file + Langfuse score)
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import queue
import signal
import sys
import threading
from collections import defaultdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from analysis.helpers import langfuse_io  # noqa: E402
from analysis.helpers.normalization import _data, normalize_trace  # noqa: E402

# The same override the course helpers honor, so a test can use a copy.
STATE_DIR = Path(os.environ.get("CARTWHEEL_ANALYSIS_STATE") or ROOT / "analysis" / "state")
LABELS_DIR = STATE_DIR / "labels"
CACHE_PATH = HERE / "cache" / "conversations.json"
EXPORT_PATH = ROOT / "traces" / "support_traces.json"
SCENARIOS_PATH = ROOT / "scenarios" / "support_scenarios.jsonl"
SCENARIO_PREFIX = "support-"

# Endpoint -> (state file, default document). GET reads the file; POST
# replaces it. Labels and suggestion decisions have their own handlers.
API_FILES: dict[str, tuple[Path, Any]] = {
    "/api/manifest": (STATE_DIR / "sample_manifest.json", {"items": []}),
    "/api/annotations": (STATE_DIR / "annotations.json", {"annotations": []}),
    "/api/patterns": (STATE_DIR / "patterns.json", {"modes": []}),
    "/api/suggestions": (STATE_DIR / "suggestions.json", []),
}

_lock = threading.Lock()
CONVERSATIONS: list[dict[str, Any]] = []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: Path, data: Any) -> None:
    """Write atomically so a crash mid-save never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    tmp.replace(path)


def _load_env() -> None:
    """Read LANGFUSE_* from .env without printing or exporting other values."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and key.startswith("LANGFUSE_") and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


# ---------------------------------------------------------------------------
# loading traces and building conversations
# ---------------------------------------------------------------------------


def _scenario_id(metadata: Any) -> str | None:
    if not isinstance(metadata, dict):
        return None
    attributes = metadata.get("attributes")
    if isinstance(attributes, str):
        try:
            attributes = json.loads(attributes)
        except ValueError:
            attributes = {}
    attributes = attributes if isinstance(attributes, dict) else {}
    return attributes.get("cartwheel.scenario_id") or metadata.get("cartwheel.scenario_id")


def _fetch_langfuse() -> list[dict[str, Any]]:
    """Return raw ``support-*`` traces with their observations from Langfuse."""
    client = langfuse_io._client()
    host = os.environ["LANGFUSE_HOST"].rstrip("/")
    summaries, page = [], 1
    while True:
        resp = client.api.trace.list(page=page, limit=100)
        batch = list(resp.data or [])
        summaries.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    wanted = [s for s in summaries if (_scenario_id(s.metadata) or "").startswith(SCENARIO_PREFIX)]
    print(f"fetching {len(wanted)} {SCENARIO_PREFIX}* traces from Langfuse ...", flush=True)
    raw = []
    for i, summary in enumerate(wanted, 1):
        full = _data(client.api.trace.get(summary.id))
        full["permalink"] = f"{host}/project/{full['projectId']}/traces/{full['id']}"
        raw.append(full)
        if i % 50 == 0:
            print(f"  {i}/{len(wanted)}", flush=True)
    return raw


def _load_export() -> list[dict[str, Any]]:
    data = json.loads(EXPORT_PATH.read_text())
    host = os.environ.get("LANGFUSE_HOST", "http://localhost:3000").rstrip("/")
    raw = []
    for trace in data["traces"]:
        if not (trace.get("cartwheel_scenario_id") or "").startswith(SCENARIO_PREFIX):
            continue
        trace = dict(trace)
        trace["permalink"] = f"{host}{trace['htmlPath']}" if trace.get("htmlPath") else None
        raw.append(trace)
    return raw


def _scenarios() -> dict[str, dict[str, Any]]:
    out = {}
    for line in SCENARIOS_PATH.read_text().splitlines():
        if line.strip():
            record = json.loads(line)
            out[record["id"]] = record
    return out


def build_conversations(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group per-turn traces into conversations by ``cartwheel.session_id``."""
    scenarios = _scenarios()
    sessions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in raw:
        trace = normalize_trace(record)
        session = trace["meta"].get("session_id") or trace["trace_id"]
        sessions[session].append(trace)

    conversations = []
    for session_id, turns in sessions.items():
        turns.sort(key=lambda t: t.get("timestamp") or "")
        first = turns[0]
        scenario_id = first["meta"].get("scenario_id")
        scenario = scenarios.get(scenario_id, {})
        metadata = first.get("metadata") or {}
        conversations.append(
            {
                "id": first["trace_id"],
                "session_id": session_id,
                "scenario_id": scenario_id,
                "role": first["meta"].get("role"),
                "user_id": metadata.get("cartwheel.user_id"),
                "store_id": metadata.get("cartwheel.store_id"),
                "prompt_version": first["meta"].get("prompt_version"),
                "models": sorted({m for t in turns for m in t.get("models", [])}),
                "turns": [
                    {
                        "trace_id": t["trace_id"],
                        "timestamp": t.get("timestamp"),
                        "permalink": t.get("permalink"),
                        "messages": t["trace"],
                    }
                    for t in turns
                ],
                # Shown only behind the collapsed "expected outcome" panel.
                "answer_key": {
                    "scenario_group": scenario.get("scenario_group"),
                    "data_quality_case_id": scenario.get("data_quality_case_id"),
                    "tuple": scenario.get("tuple"),
                    "expected": scenario.get("expected"),
                },
            }
        )
    conversations.sort(key=lambda c: c["scenario_id"] or c["id"])
    return conversations


def load_conversations(source: str, refresh: bool) -> list[dict[str, Any]]:
    if not refresh and CACHE_PATH.exists():
        cached = _read_json(CACHE_PATH, None)
        if cached and cached.get("conversations"):
            print(f"loaded {len(cached['conversations'])} conversations from cache "
                  f"({cached.get('source')}, {cached.get('fetched_at')}); use --refresh to re-fetch")
            return cached["conversations"]
    used = source
    if source == "langfuse":
        try:
            raw = _fetch_langfuse()
        except Exception as exc:  # noqa: BLE001 - reported, then fall back
            print(f"Langfuse unavailable ({exc}); falling back to {EXPORT_PATH.name}")
            raw, used = _load_export(), "export"
    else:
        raw = _load_export()
    conversations = build_conversations(raw)
    _write_json(CACHE_PATH, {"source": used, "fetched_at": _now(), "conversations": conversations})
    print(f"built {len(conversations)} conversations from {len(raw)} traces ({used})")
    return conversations


# ---------------------------------------------------------------------------
# labels: append-only files plus Langfuse scores
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _mode_names() -> list[str]:
    patterns = _read_json(API_FILES["/api/patterns"][0], {"modes": []})
    return [m["name"] for m in patterns.get("modes", []) if m.get("name")]


def live_labels() -> dict[str, dict[str, Any]]:
    """Return ``{mode: {conversation_id: row}}`` for the current taxonomy."""
    out: dict[str, dict[str, Any]] = {}
    for mode in _mode_names():
        live = {}
        for row in _read_jsonl(LABELS_DIR / f"{mode}.jsonl"):
            if not row.get("superseded_by"):
                live[row["trace_id"]] = row
        out[mode] = live
    return out


_score_queue: queue.Queue[dict[str, Any]] = queue.Queue()
_score_failures: list[dict[str, str]] = []
_score_worker_started = False


def _score_worker() -> None:
    """Write queued scores one at a time, flushing whenever the queue drains."""
    client = None
    while True:
        item = _score_queue.get()
        try:
            client = client or langfuse_io._client()
            client.create_score(**item)
        except Exception as exc:  # noqa: BLE001 - surfaced through /api/labels/sync
            _score_failures.append({"score_id": item["score_id"], "error": str(exc)})
            print(f"Langfuse score failed: {item['score_id']}: {exc}", file=sys.stderr)
        try:
            if client and _score_queue.empty():
                client.flush()
        except Exception as exc:  # noqa: BLE001 - surfaced through /api/labels/sync
            _score_failures.append({"score_id": item["score_id"], "error": f"flush: {exc}"})
        finally:
            _score_queue.task_done()


def _queue_score(item: dict[str, Any]) -> None:
    global _score_worker_started
    with _lock:
        if not _score_worker_started:
            threading.Thread(target=_score_worker, daemon=True).start()
            atexit.register(_score_queue.join)
            _score_worker_started = True
    _score_queue.put(item)


def record_label(body: dict[str, Any]) -> dict[str, Any]:
    """Append a label, supersede the previous one, and queue the Langfuse score.

    The local file is written first so a Langfuse outage never loses a human
    judgment. The score goes through a background queue because an upload can
    take seconds; ``/api/labels/sync`` reports anything queued or failed.
    Repeating the current label changes nothing, and ``only_if_missing`` never
    replaces a label that already exists.
    """
    mode = str(body["mode"])
    conversation_id = str(body["conversation_id"])
    label = int(body["label"])
    if label not in (0, 1):
        raise ValueError("label must be 0 (absent) or 1 (present)")
    if mode not in _mode_names():
        raise ValueError(f"unknown mode {mode!r}")
    path = LABELS_DIR / f"{mode}.jsonl"
    with _lock:
        rows = _read_jsonl(path)
        previous = [r for r in rows if r["trace_id"] == conversation_id]
        current = next((r for r in previous if not r.get("superseded_by")), None)
        if current and (
            body.get("only_if_missing")
            or (current["label"] == label and current.get("evidence", "") == (body.get("evidence") or ""))
        ):
            return {"ok": True, "row": current, "langfuse": "unchanged"}
        row = {
            "trace_id": conversation_id,
            "label": label,
            "source": body.get("source") or "human",
            "evidence": body.get("evidence") or "",
            "ts": _now(),
            "label_id": f"{conversation_id}#{len(previous)}",
        }
        if body.get("suggestion_id"):
            row["suggestion_id"] = body["suggestion_id"]
        for old in rows:
            if old["trace_id"] == conversation_id and not old.get("superseded_by"):
                old["superseded_by"] = row["label_id"]
        rows.append(row)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        tmp.replace(path)

    result = {"ok": True, "row": row, "langfuse": "skipped (not configured)"}
    if langfuse_io.is_configured():
        # A fixed score id per (mode, conversation) makes a flip replace the
        # earlier score instead of adding a second one.
        _queue_score(
            {
                "name": mode,
                "value": label,
                "trace_id": conversation_id,
                "score_id": f"hw4-{mode}-{conversation_id}",
                "data_type": "NUMERIC",
                "comment": row["evidence"] or None,
            }
        )
        result["langfuse"] = "queued"
    return result


def decide_suggestion(body: dict[str, Any]) -> dict[str, Any]:
    status = body.get("status")
    if status not in ("accepted", "rejected"):
        raise ValueError("status must be accepted or rejected")
    if status == "rejected" and not (body.get("reason") or "").strip():
        raise ValueError("a rejection needs a reason (the boundary that excludes it)")
    path = API_FILES["/api/suggestions"][0]
    with _lock:
        suggestions = _read_json(path, [])
        match = next((s for s in suggestions if s.get("id") == body.get("id")), None)
        if match is None:
            raise ValueError(f"unknown suggestion {body.get('id')!r}")
        match["status"] = status
        match["decision_reason"] = (body.get("reason") or "").strip()
        match["decided_at"] = _now()
        match["decided_by"] = "human"
        _write_json(path, suggestions)
    result: dict[str, Any] = {"ok": True, "suggestion": match}
    if status == "accepted" and match.get("mode") in _mode_names():
        result["label"] = record_label(
            {
                "mode": match["mode"],
                "conversation_id": match["conversation_id"],
                "label": 1,
                "source": "accepted_suggestion",
                "evidence": match.get("quote") or match.get("rationale") or "",
                "suggestion_id": match["id"],
            }
        )
    return result


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A002
        return

    def _json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> Any:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return None
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return None

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            body = (HERE / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/conversations":
            self._json(CONVERSATIONS)
        elif path == "/api/labels":
            self._json(live_labels())
        elif path == "/api/labels/sync":
            self._json({"pending": _score_queue.unfinished_tasks, "failed": _score_failures})
        elif path in API_FILES:
            file, default = API_FILES[path]
            self._json(_read_json(file, default))
        else:
            self._json({"error": f"unknown path: {path}"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        data = self._body()
        if data is None:
            self._json({"error": "expected a JSON body"}, 400)
            return
        try:
            if path == "/api/labels":
                self._json(record_label(data))
            elif path == "/api/suggestions/decide":
                self._json(decide_suggestion(data))
            elif path in API_FILES:
                with _lock:
                    _write_json(API_FILES[path][0], data)
                self._json({"ok": True})
            else:
                self._json({"error": f"cannot POST to {path}"}, 404)
        except (KeyError, ValueError) as exc:
            self._json({"error": str(exc)}, 400)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8020)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--source", choices=["langfuse", "export"], default="langfuse")
    parser.add_argument("--refresh", action="store_true", help="re-fetch traces instead of using the cache")
    args = parser.parse_args()

    _load_env()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # lets queued scores finish
    CONVERSATIONS.extend(load_conversations(args.source, args.refresh))
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"review app on http://{args.host}:{args.port}/  (state: {STATE_DIR})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
