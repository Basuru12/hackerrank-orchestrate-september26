"""CSV loaders and parsing helpers for the Buy or Wait dataset."""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any


@dataclass
class Dataset:
    requests: list[dict[str, Any]] = field(default_factory=list)
    profiles_by_user: dict[str, dict[str, Any]] = field(default_factory=dict)
    events_by_user: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    options_by_request: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    rates: list[dict[str, Any]] = field(default_factory=list)
    rates_by_key: dict[tuple[date, str, str], float] = field(default_factory=dict)
    sample_requests: list[dict[str, Any]] = field(default_factory=list)
    fx_missing_count: int = 0


def parse_pipe_list(value: str | None) -> list[str]:
    """Split a pipe-separated preference/category field into non-empty parts."""
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    return [part.strip() for part in text.split("|") if part.strip()]


def parse_date(value: str) -> date:
    """Parse YYYY-MM-DD dates used by most dataset files."""
    return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()


def parse_rate_date(value: str) -> date:
    """Parse DD/MM/YYYY dates used by exchange_rates.csv."""
    return datetime.strptime(str(value).strip(), "%d/%m/%Y").date()


def parse_flexible_date(value: str) -> date:
    """Parse YYYY-MM-DD, then DD/MM/YYYY (sample_requests date quirk)."""
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date format: {value!r}")


def parse_bool(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes"}


def parse_optional_float(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return float(text)


def parse_optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return int(float(text))


def is_cashflow_event(event: dict[str, Any]) -> bool:
    """Return whether an event should participate in cash-flow forecasting."""
    direction = str(event.get("direction", "")).strip().lower()
    status = str(event.get("status", "")).strip().lower()
    if direction == "non_cash":
        return False
    if status in {"failed", "cancelled", "unrealized"}:
        return False
    if status == "pending" and direction == "credit":
        return False
    if status in {"settled", "scheduled"}:
        return direction in {"debit", "credit"}
    if status == "pending" and direction == "debit":
        return True
    return False


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _lookup_rate(
    rates_by_key: dict[tuple[date, str, str], float],
    settlement: date,
    from_currency: str,
    to_currency: str,
) -> float | None:
    exact = rates_by_key.get((settlement, from_currency, to_currency))
    if exact is not None:
        return exact
    earlier = [
        (d, rate)
        for (d, frm, to), rate in rates_by_key.items()
        if frm == from_currency and to == to_currency and d <= settlement
    ]
    if not earlier:
        return None
    earlier.sort(key=lambda item: item[0])
    return earlier[-1][1]


def convert_amount_home(
    amount: float | None,
    currency: str,
    home_currency: str,
    settlement: date,
    rates_by_key: dict[tuple[date, str, str], float],
) -> tuple[float | None, bool]:
    """Convert amount to home currency. Returns (amount_home, fx_missing)."""
    if amount is None:
        return None, False
    if currency == home_currency:
        return amount, False
    rate = _lookup_rate(rates_by_key, settlement, currency, home_currency)
    if rate is None:
        return None, True
    return amount * rate, False


def normalize_profile(row: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = dict(row)
    out["current_available_balance"] = float(row["current_available_balance"])
    out["minimum_balance_to_keep"] = float(row["minimum_balance_to_keep"])
    out["financial_priorities_list"] = parse_pipe_list(row.get("financial_priorities"))
    out["expense_categories_to_protect_list"] = parse_pipe_list(
        row.get("expense_categories_to_protect")
    )
    out["expense_categories_user_is_willing_to_reduce_list"] = parse_pipe_list(
        row.get("expense_categories_user_is_willing_to_reduce")
    )
    out["expense_categories_user_is_willing_to_stop_list"] = parse_pipe_list(
        row.get("expense_categories_user_is_willing_to_stop")
    )
    out["payment_methods_user_will_consider_list"] = parse_pipe_list(
        row.get("payment_methods_user_will_consider")
    )
    out["max_installment_months"] = parse_optional_int(row.get("max_installment_months"))
    return out


def normalize_request(row: dict[str, str], *, flexible_dates: bool = False) -> dict[str, Any]:
    date_parser = parse_flexible_date if flexible_dates else parse_date
    out: dict[str, Any] = dict(row)
    out["request_date"] = date_parser(row["request_date"])
    out["desired_completion_date"] = date_parser(row["desired_completion_date"])
    out["requested_amount"] = float(row["requested_amount"])
    out["allows_partial_payment"] = parse_bool(row.get("allows_partial_payment"))
    # Sample rows may include label columns with mixed date formats.
    if "earliest_date_for_full_payment" in row:
        earliest = (row.get("earliest_date_for_full_payment") or "").strip()
        out["earliest_date_for_full_payment"] = (
            date_parser(earliest) if earliest else None
        )
    if "amount_safe_to_pay" in row and (row.get("amount_safe_to_pay") or "").strip():
        out["amount_safe_to_pay"] = float(row["amount_safe_to_pay"])
    return out


def normalize_option(row: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = dict(row)
    out["payment_amount"] = float(row["payment_amount"])
    out["number_of_payments"] = int(float(row["number_of_payments"]))
    out["first_payment_date"] = parse_date(row["first_payment_date"])
    out["payment_frequency_days"] = parse_optional_int(row.get("payment_frequency_days"))
    out["financing_fee"] = float(row["financing_fee"] or 0)
    out["total_payable_amount"] = float(row["total_payable_amount"])
    return out


def normalize_rate(row: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = dict(row)
    out["rate_date"] = parse_rate_date(row["rate_date"])
    out["rate"] = float(row["rate"])
    return out


def normalize_event(
    row: dict[str, str],
    home_currency: str,
    rates_by_key: dict[tuple[date, str, str], float],
) -> tuple[dict[str, Any], bool]:
    out: dict[str, Any] = dict(row)
    out["event_date"] = parse_date(row["event_date"])
    # Unrealized/non-cash rows may omit settlement_date; fall back to event_date.
    settlement_raw = (row.get("settlement_date") or "").strip()
    out["settlement_date"] = (
        parse_date(settlement_raw) if settlement_raw else out["event_date"]
    )
    out["amount"] = parse_optional_float(row.get("amount"))
    out["minimum_allowed_amount"] = parse_optional_float(row.get("minimum_allowed_amount"))
    amount_home, fx_missing = convert_amount_home(
        out["amount"],
        str(row.get("currency", "")).strip(),
        home_currency,
        out["settlement_date"],
        rates_by_key,
    )
    out["amount_home"] = amount_home
    out["include_in_cashflow"] = is_cashflow_event(out)
    return out, fx_missing


def load_dataset(dataset_dir: Path) -> Dataset:
    """Load and index all participant-facing CSVs under dataset_dir."""
    dataset_dir = Path(dataset_dir)

    raw_rates = _read_csv(dataset_dir / "exchange_rates.csv")
    rates = [normalize_rate(row) for row in raw_rates]
    rates_by_key: dict[tuple[date, str, str], float] = {
        (r["rate_date"], r["from_currency"], r["to_currency"]): r["rate"] for r in rates
    }

    profiles_by_user: dict[str, dict[str, Any]] = {}
    for row in _read_csv(dataset_dir / "financial_profiles.csv"):
        profile = normalize_profile(row)
        profiles_by_user[profile["user_id"]] = profile

    events_by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    fx_missing_count = 0
    for row in _read_csv(dataset_dir / "financial_events.csv"):
        user_id = row["user_id"]
        profile = profiles_by_user.get(user_id)
        home_currency = profile["home_currency"] if profile else row.get("currency", "")
        event, fx_missing = normalize_event(row, home_currency, rates_by_key)
        if fx_missing:
            fx_missing_count += 1
        events_by_user[user_id].append(event)

    options_by_request: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _read_csv(dataset_dir / "request_payment_options.csv"):
        option = normalize_option(row)
        options_by_request[option["request_id"]].append(option)

    requests = [
        normalize_request(row, flexible_dates=False)
        for row in _read_csv(dataset_dir / "requests.csv")
    ]
    sample_requests = [
        normalize_request(row, flexible_dates=True)
        for row in _read_csv(dataset_dir / "sample_requests.csv")
    ]

    return Dataset(
        requests=requests,
        profiles_by_user=profiles_by_user,
        events_by_user=dict(events_by_user),
        options_by_request=dict(options_by_request),
        rates=rates,
        rates_by_key=rates_by_key,
        sample_requests=sample_requests,
        fx_missing_count=fx_missing_count,
    )
