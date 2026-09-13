"""90-day forecast and safe-amount helpers (Phase 3 will implement bodies)."""

from __future__ import annotations

from datetime import date
from typing import Any


def forecast_balances(
    starting_balance: float,
    request_date: date,
    events: list[dict[str, Any]],
    home_currency: str,
    rates: list[dict[str, Any]],
    horizon_days: int = 90,
) -> dict[date, float]:
    """Project end-of-day balances for the forecast horizon. Stub for Phase 0."""
    return {}


def amount_safe_to_pay(
    starting_balance: float,
    request_date: date,
    requested_amount: float,
    minimum_balance_to_keep: float,
    events: list[dict[str, Any]],
    home_currency: str,
    rates: list[dict[str, Any]],
    horizon_days: int = 90,
) -> float:
    """Largest amount safe to pay on request_date without spending changes. Stub."""
    return 0.0


def earliest_full_payment_date(
    starting_balance: float,
    request_date: date,
    requested_amount: float,
    minimum_balance_to_keep: float,
    events: list[dict[str, Any]],
    home_currency: str,
    rates: list[dict[str, Any]],
    horizon_days: int = 90,
) -> date | None:
    """First date a full payment is forecast-safe. Stub for Phase 0."""
    return None
