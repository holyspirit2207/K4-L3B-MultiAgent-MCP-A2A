from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from student_agent.contracts import Contracts
from student_agent.trace import TraceWriter
from student_agent.workflow import solve_case, verify_cross_field_consistency


def test_solve_case_produces_valid_output_and_trace(tmp_path: Path) -> None:
    asyncio.run(_async_test_solve_case(tmp_path))


async def _async_test_solve_case(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    contracts = Contracts(root / "contracts" / "schemas")
    trace_path = tmp_path / "trace.jsonl"
    trace = TraceWriter(trace_path, contracts)

    gateway = MagicMock()
    gateway._contracts = contracts

    async def mock_call(tool_name: str, *, case_id: str, **kwargs: str) -> dict:
        dummy_hash = "sha256:" + "a" * 64
        dummy_ref = f"ev_0123456789012345678901_{tool_name}"
        if tool_name == "get_customer_history": # noqa: SIM116
            return {
                "schema_version": "day09-mcp-evidence-v1",
                "evidence_ref": dummy_ref,
                "result_hash": dummy_hash,
                "domain": "customer",
                "data": {"orders": ["ORD_001"]},
            }
        elif tool_name == "get_order":
            return {
                "schema_version": "day09-mcp-evidence-v1",
                "evidence_ref": dummy_ref,
                "result_hash": dummy_hash,
                "domain": "order",
                "data": {
                    "order_id": "ORD_001",
                    "order_status": "canceled",
                    "order_total_brl": 150.0,
                },
            }
        elif tool_name == "get_order_items":
            return {
                "schema_version": "day09-mcp-evidence-v1",
                "evidence_ref": dummy_ref,
                "result_hash": dummy_hash,
                "domain": "item",
                "data": [{"order_item_id": "ITEM_001", "seller_id": "SEL_001"}],
            }
        elif tool_name == "get_shipment_summary":
            return {
                "schema_version": "day09-mcp-evidence-v1",
                "evidence_ref": dummy_ref,
                "result_hash": dummy_hash,
                "domain": "shipment",
                "data": {"shipment_id": "SHIP_001", "shipped_date": "2026-01-01T00:00:00Z"},
            }
        elif tool_name == "get_order_payments":
            return {
                "schema_version": "day09-mcp-evidence-v1",
                "evidence_ref": dummy_ref,
                "result_hash": dummy_hash,
                "domain": "payment",
                "data": [{"payment_reference": "PAY_001", "payment_value": 150.0}],
            }
        elif tool_name == "get_refund_timeline":
            return {
                "schema_version": "day09-mcp-evidence-v1",
                "evidence_ref": dummy_ref,
                "result_hash": dummy_hash,
                "domain": "refund",
                "data": {"total_refunded_brl": 0.0, "refund_status": "none"},
            }
        elif tool_name == "get_policy":
            return {
                "schema_version": "day09-mcp-evidence-v1",
                "evidence_ref": dummy_ref,
                "result_hash": dummy_hash,
                "domain": "policy",
                "data": {"policy_name": "dispute_v1"},
            }
        return {
            "schema_version": "day09-mcp-evidence-v1",
            "evidence_ref": dummy_ref,
            "result_hash": dummy_hash,
            "domain": "order",
            "data": {},
        }

    gateway.call = AsyncMock(side_effect=mock_call)

    case = {
        "case_id": "L3B_CASE_001",
        "customer_unique_id": "CUST_999",
        "exact_order_id": "ORD_001",
    }

    # Simulate cli.py lifecycle events wrapping solve_case
    trace.emit(case_id=case["case_id"], event_type="case_received", actor="coordinator")
    output = await solve_case(case, gateway, trace)
    trace.emit(case_id=case["case_id"], event_type="case_finalized", actor="coordinator")

    # Validate output schema
    contracts.validate_output(output, "test_output")
    assert output["case_id"] == "L3B_CASE_001"
    assert output["schema_version"] == "day09-l3b-output-v2"
    assert output["assessment"]["primary_issue"] == "canceled_order_paid"
    assert output["financial_resolution"]["recommended_refund_brl"] == 150.0
    assert output["assessment"]["confidence"] > 0.0

    # Validate full lifecycle trace events sequence
    trace_lines = trace_path.read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in trace_lines]
    event_types = [e["event_type"] for e in events]

    required = [
        "case_received",
        "task_assigned",
        "tool_result_consumed",
        "handoff",
        "policy_decided",
        "verification_completed",
        "case_finalized",
    ]
    for req in required:
        assert req in event_types, f"Missing required lifecycle event: {req}"

    for event in events:
        contracts.validate_trace(event, "test_trace")


def test_cross_field_consistency_no_action() -> None:
    output = {
        "assessment": {"case_status": "no_action", "primary_issue": "unsupported_claim"},
        "financial_resolution": {
            "recommended_refund_brl": 100.0,
            "refund_lines": [{"amount_brl": 100.0}],
        },
        "resolution_actions": ["APPROVE_REFUND"],
        "root_cause_analysis": {
            "responsible_parties": [{"party_type": "customer", "party_id": "CUST_1"}]
        },
    }
    verify_cross_field_consistency(output)
    assert output["financial_resolution"]["recommended_refund_brl"] == 0.0
    assert output["financial_resolution"]["refund_lines"] == []
    assert "APPROVE_REFUND" not in output["resolution_actions"]
    assert "CLOSE_CASE" in output["resolution_actions"]
      