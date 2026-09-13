"""90-day forecast and safe-amount helpers."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any


@dataclass(frozen=True)
class RecurringSeries:
    category: str
    direction: str
    event_type: str
    interval_days: int
    amount: float
    last_settlement: date
    is_income: bool


def _signed_amount(direction: str, amount: float) -> float:
    if direction == "credit":
        return amount
    return -amount


def _group_key(event: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(event.get("category", "")),
        str(event.get("direction", "")),
        str(event.get("event_type", "")),
    )


def _normalize_interval(mode_gap: int) -> int:
    """Snap near-monthly gaps to 30 days for stable projections."""
    if 28 <= mode_gap <= 31:
        return 30
    return mode_gap


def series_key_for_event(event: dict[str, Any]) -> tuple[str, str, str]:
    """Public helper: (category, direction, event_type)."""
    return _group_key(event)


def detect_recurring_series(
    events: list[dict[str, Any]],
    as_of: date,
    *,
    include_flexible: bool = True,
) -> list[RecurringSeries]:
    """Detect recurring cash series from settled history (and scheduled income)."""
    by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if not event.get("include_in_cashflow"):
            continue
        if event.get("amount_home") is None:
            continue
        status = str(event.get("status", "")).lower()
        direction = str(event.get("direction", "")).lower()
        if status == "settled" and event["settlement_date"] < as_of:
            by_key[_group_key(event)].append(event)
        elif status == "scheduled" and direction == "credit":
            # Confirm ongoing income without inventing unsupported salary.
            by_key[_group_key(event)].append(event)

    series_list: list[RecurringSeries] = []
    for key, items in by_key.items():
        # Prefer later row when duplicate settlement dates exist.
        deduped: dict[date, dict[str, Any]] = {}
        for event in sorted(items, key=lambda e: e["settlement_date"]):
            deduped[event["settlement_date"]] = event
        items = list(deduped.values())
        category, direction, event_type = key
        is_income = direction == "credit" or event_type == "income"

        if is_income:
            settled = [
                e
                for e in items
                if str(e.get("status", "")).lower() == "settled"
                and e["settlement_date"] < as_of
            ]
            has_scheduled = any(str(e.get("status", "")).lower() == "scheduled" for e in items)
            # Split mixed pay streams (base salary vs irregular bonus) by amount band.
            if not has_scheduled and settled:
                by_band: dict[int, list[dict[str, Any]]] = defaultdict(list)
                for event in settled:
                    band = int(round(float(event["amount_home"]) / 1000.0))
                    by_band[band].append(event)
                settled = max(by_band.values(), key=len)
            if has_scheduled:
                if len(items) < 2:
                    continue
            elif len(settled) >= 3:
                items = settled
            else:
                continue
        else:
            settled = [
                e
                for e in items
                if str(e.get("status", "")).lower() == "settled"
            ]
            if len(settled) < 3:
                continue
            flex_counts = Counter(
                str(e.get("flexibility") or "").lower() for e in settled
            )
            dominant_flex = flex_counts.most_common(1)[0][0] if flex_counts else ""
            if not include_flexible and dominant_flex in {
                "reducible",
                "stoppable",
                "reducible_or_stoppable",
                "flexible",
            }:
                continue
            items = settled

        gaps = [
            (items[i]["settlement_date"] - items[i - 1]["settlement_date"]).days
            for i in range(1, len(items))
        ]
        if not gaps:
            continue
        mode_gap, _ = Counter(gaps).most_common(1)[0]
        mode_gap = _normalize_interval(mode_gap)
        if mode_gap <= 0:
            continue
        tolerance = max(1, mode_gap // 10)
        in_band = sum(1 for g in gaps if abs(_normalize_interval(g) - mode_gap) <= tolerance)
        if in_band / len(gaps) < 0.60:
            continue

        last_three = items[-3:]
        amounts = [float(e["amount_home"]) for e in last_three]
        if direction == "debit":
            forecast_amount = sum(amounts) / len(amounts)
        else:
            scheduled = [
                e for e in items if str(e.get("status", "")).lower() == "scheduled"
            ]
            forecast_amount = (
                float(scheduled[-1]["amount_home"]) if scheduled else min(amounts)
            )

        series_list.append(
            RecurringSeries(
                category=category,
                direction=direction,
                event_type=event_type,
                interval_days=mode_gap,
                amount=forecast_amount,
                last_settlement=items[-1]["settlement_date"],
                is_income=is_income,
            )
        )
    return series_list


def _confirmed_future_deltas(
    events: list[dict[str, Any]],
    request_date: date,
    end_date: date,
    stopped_keys: set[tuple[str, str, str]] | None = None,
) -> tuple[dict[date, float], set[tuple[tuple[str, str, str], date]]]:
    deltas: dict[date, float] = defaultdict(float)
    occupied: set[tuple[tuple[str, str, str], date]] = set()
    stopped_keys = stopped_keys or set()

    for event in events:
        if not event.get("include_in_cashflow"):
            continue
        if event.get("amount_home") is None:
            continue
        status = str(event.get("status", "")).lower()
        direction = str(event.get("direction", "")).lower()
        settlement = event["settlement_date"]
        if settlement < request_date or settlement > end_date:
            continue
        key = _group_key(event)
        if direction == "debit" and key in stopped_keys:
            continue
        if status == "pending" and direction == "debit":
            pass
        elif status == "scheduled" and direction in {"debit", "credit"}:
            pass
        else:
            continue
        amount = float(event["amount_home"])
        deltas[settlement] += _signed_amount(direction, amount)
        occupied.add((key, settlement))
    return deltas, occupied


def build_daily_deltas(
    events: list[dict[str, Any]],
    request_date: date,
    horizon_days: int = 90,
    series_overrides: dict[tuple[str, str, str], float | None] | None = None,
) -> dict[date, float]:
    """Net cash deltas per day from confirmed futures + projected recurrings.

    series_overrides maps series key -> None (stop) or a float (reduced amount).
    """
    overrides = series_overrides or {}
    stopped_keys = {key for key, value in overrides.items() if value is None}
    end_date = request_date + timedelta(days=horizon_days)
    deltas, occupied = _confirmed_future_deltas(
        events, request_date, end_date, stopped_keys=stopped_keys
    )

    for event in events:
        if not event.get("include_in_cashflow"):
            continue
        status = str(event.get("status", "")).lower()
        if status not in {"pending", "scheduled"}:
            continue
        settlement = event["settlement_date"]
        key = _group_key(event)
        if key in stopped_keys and str(event.get("direction", "")).lower() == "debit":
            continue
        if request_date <= settlement <= end_date:
            occupied.add((key, settlement))

    for series in detect_recurring_series(events, request_date, include_flexible=True):
        key = (series.category, series.direction, series.event_type)
        if key in overrides:
            if overrides[key] is None:
                continue
            amount = float(overrides[key])  # type: ignore[arg-type]
        else:
            amount = series.amount
        cursor = series.last_settlement + timedelta(days=series.interval_days)
        while cursor <= end_date:
            if cursor > request_date and (key, cursor) not in occupied:
                deltas[cursor] += _signed_amount(series.direction, amount)
                occupied.add((key, cursor))
            cursor += timedelta(days=series.interval_days)
    return dict(deltas)


def is_safe(
    starting_balance: float,
    daily_deltas: dict[date, float],
    minimum_balance_to_keep: float,
    request_date: date,
    horizon_days: int,
    payment_date: date | None,
    payment_amount: float,
) -> bool:
    """Return True if balance never falls below the minimum over the horizon."""
    balance = starting_balance
    end_date = request_date + timedelta(days=horizon_days)
    day = request_date
    while day <= end_date:
        balance += daily_deltas.get(day, 0.0)
        if payment_date is not None and day == payment_date:
            balance -= payment_amount
        if balance + 1e-9 < minimum_balance_to_keep:
            return False
        day += timedelta(days=1)
    return True


def forecast_balances(
    starting_balance: float,
    request_date: date,
    events: list[dict[str, Any]],
    home_currency: str,
    rates: list[dict[str, Any]],
    horizon_days: int = 90,
) -> dict[date, float]:
    """Project end-of-day balances for the forecast horizon (no request payment)."""
    _ = (home_currency, rates)
    deltas = build_daily_deltas(events, request_date, horizon_days)
    balances: dict[date, float] = {}
    balance = starting_balance
    end_date = request_date + timedelta(days=horizon_days)
    day = request_date
    while day <= end_date:
        balance += deltas.get(day, 0.0)
        balances[day] = round(balance, 2)
        day += timedelta(days=1)
    return balances


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
    """Largest amount safe to pay on request_date without spending changes."""
    _ = (home_currency, rates)
    if requested_amount <= 0:
        return 0.0
    deltas = build_daily_deltas(events, request_date, horizon_days)
    max_cents = int(round(requested_amount * 100))
    lo, hi = 0, max_cents
    best = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        pay = mid / 100.0
        if is_safe(
            starting_balance,
            deltas,
            minimum_balance_to_keep,
            request_date,
            horizon_days,
            request_date,
            pay,
        ):
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return round(min(requested_amount, best / 100.0), 2)


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
    """First date a full payment is forecast-safe within the horizon."""
    _ = (home_currency, rates)
    deltas = build_daily_deltas(events, request_date, horizon_days)
    end_date = request_date + timedelta(days=horizon_days)
    day = request_date
    while day <= end_date:
        if is_safe(
            starting_balance,
            deltas,
            minimum_balance_to_keep,
            request_date,
            horizon_days,
            day,
            requested_amount,
        ):
            return day
        day += timedelta(days=1)
    return None
