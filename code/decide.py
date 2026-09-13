"""Plan candidates and ranking (Phase 4 will implement real logic)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Decision:
    request_id: str
    amount_safe_to_pay: float
    affordability_status: str
    recommended_payment_method: str
    payment_plan: str
    earliest_date_for_full_payment: str
    spending_changes_needed: str
    decision_explanation: str


def decide_for_request(
    request: dict[str, Any],
    profile: dict[str, Any] | None = None,
    events: list[dict[str, Any]] | None = None,
    options: list[dict[str, Any]] | None = None,
    rates: list[dict[str, Any]] | None = None,
) -> Decision:
    """Return a placeholder decision until cashflow and ranking are implemented."""
    request_id = str(request.get("request_id", ""))
    return Decision(
        request_id=request_id,
        amount_safe_to_pay=0,
        affordability_status="not_affordable",
        recommended_payment_method="not_recommended",
        payment_plan="none",
        earliest_date_for_full_payment="",
        spending_changes_needed="none",
        decision_explanation="placeholder pending cashflow engine",
    )


def rank_candidates(candidates: list[Decision]) -> Decision:
    """Pick the best eligible safe plan. Stub for Phase 4."""
    if not candidates:
        return Decision(
            request_id="",
            amount_safe_to_pay=0,
            affordability_status="not_affordable",
            recommended_payment_method="not_recommended",
            payment_plan="none",
            earliest_date_for_full_payment="",
            spending_changes_needed="none",
            decision_explanation="placeholder pending cashflow engine",
        )
    return candidates[0]
