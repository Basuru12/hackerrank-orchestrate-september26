"""Plan candidates and ranking for Buy or Wait decisions."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import cashflow

_OPTION_ID_RE = re.compile(r"(\d+)$")
_SENTINEL_OPTION_ID = 10**9


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


@dataclass
class Candidate:
    method: str
    plan_payments: list[tuple[date, float]]
    total_payable: float
    start_date: date
    num_payments: int
    completes_by_deadline: bool
    spending_changes: str
    payment_option_id: int | None
    affordability_status: str


def format_plan(payments: list[tuple[date, float]]) -> str:
    if not payments:
        return "none"
    parts: list[str] = []
    for pay_date, amount in payments:
        rounded = round(float(amount), 2)
        if abs(rounded - int(rounded)) < 1e-9:
            amount_str = str(int(rounded))
        else:
            amount_str = f"{rounded:.2f}"
        parts.append(f"{pay_date.isoformat()}:{amount_str}")
    return "|".join(parts)


def option_id_num(payment_option_id: str | None) -> int | None:
    if not payment_option_id:
        return None
    match = _OPTION_ID_RE.search(str(payment_option_id))
    if not match:
        return None
    return int(match.group(1))


def installment_months(option: dict[str, Any]) -> int:
    n = int(option["number_of_payments"])
    freq = option.get("payment_frequency_days")
    if freq is not None and 28 <= int(freq) <= 31:
        return n
    first = option["first_payment_date"]
    if freq is None or n <= 1:
        return max(1, n)
    last = first + timedelta(days=int(freq) * (n - 1))
    span_days = (last - first).days
    return max(1, int(math.ceil(span_days / 30)))


def build_installment_schedule(option: dict[str, Any]) -> list[tuple[date, float]]:
    first = option["first_payment_date"]
    n = int(option["number_of_payments"])
    freq = option.get("payment_frequency_days")
    amount = float(option["payment_amount"])
    if n <= 0:
        return []
    if n == 1 or freq is None:
        return [(first, amount)]
    freq_days = int(freq)
    return [(first + timedelta(days=freq_days * i), amount) for i in range(n)]


def plan_is_safe(
    starting_balance: float,
    daily_deltas: dict[date, float],
    minimum_balance_to_keep: float,
    request_date: date,
    payments: list[tuple[date, float]],
    horizon_days: int = 90,
) -> bool:
    """Return True if applying all plan payments keeps the 90-day path safe."""
    pay_by_day: dict[date, float] = {}
    for pay_date, amount in payments:
        pay_by_day[pay_date] = pay_by_day.get(pay_date, 0.0) + amount

    balance = starting_balance
    end_date = request_date + timedelta(days=horizon_days)
    day = request_date
    while day <= end_date:
        balance += daily_deltas.get(day, 0.0)
        if day in pay_by_day:
            balance -= pay_by_day[day]
        if balance + 1e-9 < minimum_balance_to_keep:
            return False
        day += timedelta(days=1)
    return True


def _rank_key(candidate: Candidate) -> tuple:
    return (
        0 if candidate.completes_by_deadline else 1,
        0 if candidate.spending_changes == "none" else 1,
        candidate.total_payable,
        candidate.start_date,
        candidate.num_payments,
        candidate.payment_option_id
        if candidate.payment_option_id is not None
        else _SENTINEL_OPTION_ID,
    )


def rank_candidates(candidates: list[Candidate]) -> Candidate | None:
    """Pick the best eligible safe plan using the challenge ranking rules."""
    if not candidates:
        return None
    return sorted(candidates, key=_rank_key)[0]


def _explanation(
    candidate: Candidate | None,
    currency: str,
    minimum: float,
    requested: float,
) -> str:
    if candidate is None:
        return (
            f"Do not proceed with the {currency} {requested:g} request. "
            f"No eligible payment keeps the {currency} {minimum:g} minimum protected."
        )
    plan = format_plan(candidate.plan_payments)
    if candidate.method == "full_payment":
        return (
            f"Pay {currency} {requested:g} today. "
            f"This keeps the {currency} {minimum:g} minimum available over the next 90 days."
        )
    if candidate.method == "wait":
        when = candidate.start_date.isoformat()
        return (
            f"Pay {currency} {requested:g} in full on {when}. "
            f"Paying earlier would put the {currency} {minimum:g} minimum at risk."
        )
    if candidate.method == "partial_payment":
        first_amt = candidate.plan_payments[0][1]
        second_amt = candidate.plan_payments[1][1]
        second_date = candidate.plan_payments[1][0].isoformat()
        return (
            f"Pay {currency} {first_amt:g} today and the remaining {currency} {second_amt:g} "
            f"on {second_date}. This completes the full request and keeps the "
            f"{currency} {minimum:g} minimum protected."
        )
    # installments
    per = candidate.plan_payments[0][1] if candidate.plan_payments else 0
    start = candidate.start_date.isoformat()
    return (
        f"Use {candidate.num_payments} installments of {currency} {per:g}, starting {start}. "
        f"This leaves at least {currency} {minimum:g} available. Plan: {plan}."
    )


def _build_candidates(
    request: dict[str, Any],
    profile: dict[str, Any],
    options: list[dict[str, Any]],
    safe_amt: float,
    earliest: date | None,
    starting_balance: float,
    daily_deltas: dict[date, float],
    minimum: float,
) -> list[Candidate]:
    methods = set(profile.get("payment_methods_user_will_consider_list") or [])
    max_months = profile.get("max_installment_months")
    request_date: date = request["request_date"]
    deadline: date = request["desired_completion_date"]
    req = float(request["requested_amount"])
    horizon_end = request_date + timedelta(days=90)
    candidates: list[Candidate] = []

    full_options = [o for o in options if o.get("payment_method") == "full_payment"]
    full_option_id = None
    full_total = req
    if full_options:
        full_options_sorted = sorted(
            full_options,
            key=lambda o: option_id_num(o.get("payment_option_id")) or _SENTINEL_OPTION_ID,
        )
        best_full = full_options_sorted[0]
        full_option_id = option_id_num(best_full.get("payment_option_id"))
        full_total = float(best_full.get("total_payable_amount", req))

    # full_payment
    if "full_payment" in methods and safe_amt + 1e-9 >= req:
        payments = [(request_date, req)]
        if plan_is_safe(starting_balance, daily_deltas, minimum, request_date, payments):
            candidates.append(
                Candidate(
                    method="full_payment",
                    plan_payments=payments,
                    total_payable=full_total,
                    start_date=request_date,
                    num_payments=1,
                    completes_by_deadline=request_date <= deadline,
                    spending_changes="none",
                    payment_option_id=full_option_id,
                    affordability_status="affordable_now",
                )
            )

    # partial_payment
    if (
        "partial_payment" in methods
        and bool(request.get("allows_partial_payment"))
        and earliest is not None
        and earliest <= deadline
        and 0 < safe_amt < req - 1e-9
    ):
        first = round(safe_amt, 2)
        second = round(req - first, 2)
        # Fix rare cent drift so legs sum to requested_amount.
        if abs((first + second) - req) > 1e-9:
            second = round(req - first, 2)
        payments = [(request_date, first), (earliest, second)]
        if plan_is_safe(starting_balance, daily_deltas, minimum, request_date, payments):
            candidates.append(
                Candidate(
                    method="partial_payment",
                    plan_payments=payments,
                    total_payable=req,
                    start_date=request_date,
                    num_payments=2,
                    completes_by_deadline=earliest <= deadline,
                    spending_changes="none",
                    payment_option_id=None,
                    affordability_status="affordable_with_plan",
                )
            )

    # installments
    if "installments" in methods and max_months is not None:
        for option in options:
            if option.get("payment_method") != "installments":
                continue
            months = installment_months(option)
            if months > int(max_months):
                continue
            schedule = build_installment_schedule(option)
            if not schedule:
                continue
            last_date = schedule[-1][0]
            if last_date > deadline:
                continue
            if last_date > horizon_end:
                continue
            if not plan_is_safe(
                starting_balance, daily_deltas, minimum, request_date, schedule
            ):
                continue
            candidates.append(
                Candidate(
                    method="installments",
                    plan_payments=schedule,
                    total_payable=float(option["total_payable_amount"]),
                    start_date=schedule[0][0],
                    num_payments=len(schedule),
                    completes_by_deadline=last_date <= deadline,
                    spending_changes="none",
                    payment_option_id=option_id_num(option.get("payment_option_id")),
                    affordability_status="affordable_with_plan",
                )
            )

    # wait
    if (
        "full_payment" in methods
        and earliest is not None
        and earliest <= deadline
        and earliest > request_date
    ):
        payments = [(earliest, req)]
        if plan_is_safe(starting_balance, daily_deltas, minimum, request_date, payments):
            candidates.append(
                Candidate(
                    method="wait",
                    plan_payments=payments,
                    total_payable=req,
                    start_date=earliest,
                    num_payments=1,
                    completes_by_deadline=earliest <= deadline,
                    spending_changes="none",
                    payment_option_id=None,
                    affordability_status="affordable_later",
                )
            )

    return candidates


def decide_for_request(
    request: dict[str, Any],
    profile: dict[str, Any] | None = None,
    events: list[dict[str, Any]] | None = None,
    options: list[dict[str, Any]] | None = None,
    rates: list[dict[str, Any]] | None = None,
) -> Decision:
    """Choose the best eligible safe payment recommendation for one request."""
    request_id = str(request.get("request_id", ""))
    if profile is None:
        profile = {}
    events = events or []
    options = options or []
    rates = rates or []

    request_date: date = request["request_date"]
    req = float(request["requested_amount"])
    starting_balance = float(profile.get("current_available_balance", 0))
    minimum = float(profile.get("minimum_balance_to_keep", 0))
    currency = str(profile.get("home_currency", ""))

    safe_amt = cashflow.amount_safe_to_pay(
        starting_balance,
        request_date,
        req,
        minimum,
        events,
        currency,
        rates,
    )
    earliest = cashflow.earliest_full_payment_date(
        starting_balance,
        request_date,
        req,
        minimum,
        events,
        currency,
        rates,
    )
    daily_deltas = cashflow.build_daily_deltas(events, request_date)

    candidates = _build_candidates(
        request,
        profile,
        options,
        safe_amt,
        earliest,
        starting_balance,
        daily_deltas,
        minimum,
    )
    winner = rank_candidates(candidates)

    earliest_str = earliest.isoformat() if earliest is not None else ""
    if winner is None:
        return Decision(
            request_id=request_id,
            amount_safe_to_pay=round(safe_amt, 2),
            affordability_status="not_affordable",
            recommended_payment_method="not_recommended",
            payment_plan="none",
            earliest_date_for_full_payment=earliest_str,
            spending_changes_needed="none",
            decision_explanation=_explanation(None, currency, minimum, req),
        )

    return Decision(
        request_id=request_id,
        amount_safe_to_pay=round(safe_amt, 2),
        affordability_status=winner.affordability_status,
        recommended_payment_method=winner.method,
        payment_plan=format_plan(winner.plan_payments),
        earliest_date_for_full_payment=earliest_str,
        spending_changes_needed=winner.spending_changes,
        decision_explanation=_explanation(winner, currency, minimum, req),
    )
