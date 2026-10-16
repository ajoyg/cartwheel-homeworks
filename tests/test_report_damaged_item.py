"""Tests for the student-added report_damaged_item tool (HW1 Part A)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from agent import tools
from agent.agent import TOOLS_BY_ROLE
from agent.auth import AuthContext

SHOPPER_1 = AuthContext(user_id=1, role="shopper")
MERCHANT_STORE_1 = AuthContext(user_id=9001, role="merchant", store_id=1)
MERCHANT_STORE_2 = AuthContext(user_id=9002, role="merchant", store_id=2)
SUPPORT = AuthContext(user_id=9501, role="support")


def _escalation_count(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM escalations").fetchone()[0]
    finally:
        conn.close()


def test_owner_reports_delivered_order(world_copy: Path) -> None:
    before = _escalation_count(world_copy)
    result = tools.report_damaged_item(SHOPPER_1, 4127, "The vase arrived cracked.")
    assert result["ok"] is True
    assert result["order_id"] == 4127
    assert result["sla_hours"] == 24

    conn = sqlite3.connect(world_copy)
    try:
        row = conn.execute(
            "SELECT user_id, order_id, context FROM escalations WHERE id = ?",
            (result["ticket_id"],),
        ).fetchone()
        status = conn.execute("SELECT status FROM orders WHERE id = 4127").fetchone()[0]
    finally:
        conn.close()
    assert row[0] == 1 and row[1] == 4127
    context = json.loads(row[2])
    assert context["type"] == "damaged_item"
    assert context["description"] == "The vase arrived cracked."
    assert status == "delivered"  # a report changes no order or refund state
    assert _escalation_count(world_copy) == before + 1


def test_scope_is_checked_before_status(world_copy: Path) -> None:
    before = _escalation_count(world_copy)
    stranger = AuthContext(user_id=2, role="shopper")
    denied = tools.report_damaged_item(stranger, 4127, "cracked")
    assert denied["ok"] is False
    assert denied["error"] == "permission_denied"
    assert "delivered" not in denied["reason"]

    other_store = tools.report_damaged_item(MERCHANT_STORE_2, 4127, "cracked")
    assert other_store["error"] == "permission_denied"
    assert _escalation_count(world_copy) == before


def test_merchant_and_support_scope(world_copy: Path) -> None:
    assert tools.report_damaged_item(MERCHANT_STORE_1, 4127, "cracked")["ok"] is True
    assert tools.report_damaged_item(SUPPORT, 4455, "chipped rim")["ok"] is True


def test_only_delivered_orders_accept_reports(world_copy: Path) -> None:
    conn = sqlite3.connect(world_copy)
    try:
        not_delivered = {
            status: conn.execute(
                "SELECT id FROM orders WHERE status = ? ORDER BY id LIMIT 1", (status,)
            ).fetchone()[0]
            for status in ("placed", "shipped", "cancelled", "refunded")
        }
    finally:
        conn.close()
    before = _escalation_count(world_copy)
    for status, order_id in not_delivered.items():
        result = tools.report_damaged_item(SUPPORT, order_id, "damaged")
        assert result["ok"] is False
        assert result["error"] == "not_eligible"
        assert status in result["reason"]
    assert _escalation_count(world_copy) == before


def test_missing_order_and_empty_description(world_copy: Path) -> None:
    before = _escalation_count(world_copy)
    assert tools.report_damaged_item(SHOPPER_1, 99_999_999, "cracked")["error"] == "not_found"
    assert tools.report_damaged_item(SHOPPER_1, 4127, "   ")["error"] == "invalid_argument"
    assert _escalation_count(world_copy) == before


def test_registered_for_every_role() -> None:
    for role in ("shopper", "merchant", "support"):
        assert "report_damaged_item" in {tool.name for tool in TOOLS_BY_ROLE[role]}
