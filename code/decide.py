"""Plan candidates and ranking for Buy or Wait decisions."""

from __future__ import annotations

import itertools
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import cashflow

_OPTION_ID_RE = re.compile(r"(\d+)$")
_SENTINEL_OPTION_ID = 10**9
_STOP_FLEX = {"stoppable", "reducible_or_stoppable", "flexible"}
_REDUCE_FLEX = {"reducible", "reducible_or_stoppable", "flexible"}


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
    change_count: int = 0


@dataclass(frozen=True)
class SpendingAction:
    event_id: str
    kind: str  # stop | reduce_to
    series_key: tuple[str, str, str]
    new_amount: float | None
    savings: float
    category: str

    def format(self) -> str:
        if self.kind == "stop":
            return f"stop:{self.event_id}"
        amount = self.new_amount if self.new_amount is not None else 0.0
        rounded = round(float(amount), 2)
        if abs(rounded - int(rounded)) < 1e-9:
            amount_str = str(int(rounded))
        else:
            amount_str = f"{rounded:.2f}"
        return f"reduce_to:{self.event_id}:{amount_str}"


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
        candidate.change_count,
        candidate.spending_changes,
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
    # Stable preference for fewer spending-change actions among equal keys.
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
    cuts = ""
    if candidate.spending_changes != "none":
        cuts = f"Apply spending changes ({candidate.spending_changes}), then "
    if candidate.method == "full_payment":
        return (
            f"{cuts}pay {currency} {requested:g} today. "
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
    per = candidate.plan_payments[0][1] if candidate.plan_payments else 0
    start = candidate.start_date.isoformat()
    plan = format_plan(candidate.plan_payments)
    return (
        f"{cuts}use {candidate.num_payments} installments of {currency} {per:g}, starting {start}. "
        f"This leaves at least {currency} {minimum:g} available. Plan: {plan}."
    )


def _latest_settled_event_id(
    events: list[dict[str, Any]],
    series_key: tuple[str, str, str],
    request_date: date,
) -> str | None:
    best: dict[str, Any] | None = None
    for event in events:
        if cashflow.series_key_for_event(event) != series_key:
            continue
        if str(event.get("status", "")).lower() != "settled":
            continue
        if event.get("amount_home") is None:
            continue
        if event["settlement_date"] >= request_date:
            continue
        if best is None or event["settlement_date"] > best["settlement_date"]:
            best = event
    return str(best["event_id"]) if best else None


def _dominant_flexibility(
    events: list[dict[str, Any]],
    series_key: tuple[str, str, str],
    request_date: date,
) -> str:
    counts: dict[str, int] = defaultdict(int)
    latest_by_flex: dict[str, date] = {}
    for event in events:
        if cashflow.series_key_for_event(event) != series_key:
            continue
        if str(event.get("status", "")).lower() != "settled":
            continue
        if event["settlement_date"] >= request_date:
            continue
        flex = str(event.get("flexibility") or "").lower()
        counts[flex] += 1
        prev = latest_by_flex.get(flex)
        if prev is None or event["settlement_date"] > prev:
            latest_by_flex[flex] = event["settlement_date"]
    if not counts:
        return ""
    # Prefer the flexibility on the most recent settled event.
    return max(latest_by_flex.items(), key=lambda item: item[1])[0]


def _minimum_allowed_for_series(
    events: list[dict[str, Any]],
    series_key: tuple[str, str, str],
    request_date: date,
) -> float | None:
    for event in sorted(
        (
            e
            for e in events
            if cashflow.series_key_for_event(e) == series_key
            and str(e.get("status", "")).lower() == "settled"
            and e["settlement_date"] < request_date
        ),
        key=lambda e: e["settlement_date"],
        reverse=True,
    ):
        minimum = event.get("minimum_allowed_amount")
        if minimum is not None:
            return float(minimum)
    return None


def discover_spending_actions(
    events: list[dict[str, Any]],
    profile: dict[str, Any],
    request_date: date,
) -> list[SpendingAction]:
    protect = set(profile.get("expense_categories_to_protect_list") or [])
    reduce_cats = set(profile.get("expense_categories_user_is_willing_to_reduce_list") or [])
    stop_cats = set(profile.get("expense_categories_user_is_willing_to_stop_list") or [])
    actions: list[SpendingAction] = []

    for series in cashflow.detect_recurring_series(
        events, request_date, include_flexible=True
    ):
        if series.direction != "debit" or series.is_income:
            continue
        if series.category in protect:
            continue
        key = (series.category, series.direction, series.event_type)
        event_id = _latest_settled_event_id(events, key, request_date)
        if not event_id:
            continue
        flex = _dominant_flexibility(events, key, request_date)

        if series.category in stop_cats and flex in _STOP_FLEX:
            actions.append(
                SpendingAction(
                    event_id=event_id,
                    kind="stop",
                    series_key=key,
                    new_amount=None,
                    savings=float(series.amount),
                    category=series.category,
                )
            )
        if series.category in reduce_cats and flex in _REDUCE_FLEX:
            floor = _minimum_allowed_for_series(events, key, request_date)
            if floor is None:
                continue
            if floor + 1e-9 >= float(series.amount):
                continue
            actions.append(
                SpendingAction(
                    event_id=event_id,
                    kind="reduce_to",
                    series_key=key,
                    new_amount=float(floor),
                    savings=float(series.amount) - float(floor),
                    category=series.category,
                )
            )

    actions.sort(key=lambda a: (-a.savings, a.event_id, a.kind))
    return actions


def _format_action_set(actions: tuple[SpendingAction, ...] | list[SpendingAction]) -> str:
    ordered = sorted(actions, key=lambda a: (a.event_id, a.kind))
    return "|".join(a.format() for a in ordered)


def _overrides_from_actions(
    actions: tuple[SpendingAction, ...] | list[SpendingAction],
) -> dict[tuple[str, str, str], float | None]:
    overrides: dict[tuple[str, str, str], float | None] = {}
    for action in actions:
        if action.kind == "stop":
            overrides[action.series_key] = None
        else:
            overrides[action.series_key] = action.new_amount
    return overrides


def _build_candidates(
    request: dict[str, Any],
    profile: dict[str, Any],
    options: list[dict[str, Any]],
    safe_amt: float,
    earliest: date | None,
    starting_balance: float,
    daily_deltas: dict[date, float],
    minimum: float,
    spending_changes: str = "none",
) -> list[Candidate]:
    methods = set(profile.get("payment_methods_user_will_consider_list") or [])
    max_months = profile.get("max_installment_months")
    request_date: date = request["request_date"]
    deadline: date = request["desired_completion_date"]
    req = float(request["requested_amount"])
    horizon_end = request_date + timedelta(days=90)
    candidates: list[Candidate] = []
    change_count = 0 if spending_changes == "none" else spending_changes.count("|") + 1

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

    allow_full = safe_amt + 1e-9 >= req or spending_changes != "none"
    if "full_payment" in methods and allow_full:
        payments = [(request_date, req)]
        if plan_is_safe(starting_balance, daily_deltas, minimum, request_date, payments):
            status = (
                "affordable_with_plan"
                if spending_changes != "none"
                else "affordable_now"
            )
            candidates.append(
                Candidate(
                    method="full_payment",
                    plan_payments=payments,
                    total_payable=full_total,
                    start_date=request_date,
                    num_payments=1,
                    completes_by_deadline=request_date <= deadline,
                    spending_changes=spending_changes,
                    payment_option_id=full_option_id,
                    affordability_status=status,
                    change_count=change_count,
                )
            )

    if (
        "partial_payment" in methods
        and bool(request.get("allows_partial_payment"))
        and earliest is not None
        and earliest <= deadline
        and 0 < safe_amt < req - 1e-9
        and spending_changes == "none"
    ):
        first = round(safe_amt, 2)
        second = round(req - first, 2)
        second_date = earliest
        if second_date <= request_date:
            day = request_date + timedelta(days=1)
            second_date = None
            while day <= deadline and day <= horizon_end:
                trial = [(request_date, first), (day, second)]
                if plan_is_safe(
                    starting_balance, daily_deltas, minimum, request_date, trial
                ):
                    second_date = day
                    break
                day += timedelta(days=1)
        if second_date is not None:
            payments = [(request_date, first), (second_date, second)]
            if plan_is_safe(
                starting_balance, daily_deltas, minimum, request_date, payments
            ):
                candidates.append(
                    Candidate(
                        method="partial_payment",
                        plan_payments=payments,
                        total_payable=req,
                        start_date=request_date,
                        num_payments=2,
                        completes_by_deadline=second_date <= deadline,
                        spending_changes="none",
                        payment_option_id=None,
                        affordability_status="affordable_with_plan",
                        change_count=0,
                    )
                )

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
            if last_date > deadline or last_date > horizon_end:
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
                    spending_changes=spending_changes,
                    payment_option_id=option_id_num(option.get("payment_option_id")),
                    affordability_status="affordable_with_plan",
                    change_count=change_count,
                )
            )

    if (
        "full_payment" in methods
        and earliest is not None
        and earliest <= deadline
        and earliest > request_date
        and spending_changes == "none"
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
                    change_count=0,
                )
            )

    return candidates


def _search_with_spending_changes(
    request: dict[str, Any],
    profile: dict[str, Any],
    events: list[dict[str, Any]],
    options: list[dict[str, Any]],
    safe_amt: float,
    earliest: date | None,
    starting_balance: float,
    minimum: float,
    series_amount_forces: dict[tuple[str, str, str], float] | None = None,
) -> list[Candidate]:
    actions = discover_spending_actions(events, profile, request["request_date"])
    if not actions:
        return []

    # Bound search: top 8 by savings.
    actions = actions[:8]
    found: list[Candidate] = []
    request_date = request["request_date"]

    for size in range(1, min(3, len(actions)) + 1):
        for combo in itertools.combinations(actions, size):
            event_ids = [a.event_id for a in combo]
            if len(event_ids) != len(set(event_ids)):
                continue  # stop + reduce same event
            changes = _format_action_set(combo)
            overrides = _overrides_from_actions(combo)
            deltas = cashflow.build_daily_deltas(
                events,
                request_date,
                series_overrides=overrides,
                series_amount_forces=series_amount_forces,
            )
            candidates = _build_candidates(
                request,
                profile,
                options,
                safe_amt,
                earliest,
                starting_balance,
                deltas,
                minimum,
                spending_changes=changes,
            )
            found.extend(candidates)
    return found


def decide_for_request(
    request: dict[str, Any],
    profile: dict[str, Any] | None = None,
    events: list[dict[str, Any]] | None = None,
    options: list[dict[str, Any]] | None = None,
    rates: list[dict[str, Any]] | None = None,
    messages: list[dict[str, Any]] | None = None,
    images_by_event: dict[str, str] | None = None,
    rates_by_key: dict[tuple[date, str, str], float] | None = None,
) -> Decision:
    """Choose the best eligible safe payment recommendation for one request."""
    import evidence

    request_id = str(request.get("request_id", ""))
    if profile is None:
        profile = {}
    events = list(events or [])
    options = options or []
    rates = rates or []
    messages = messages or []
    images_by_event = images_by_event or {}

    events, meta = evidence.apply_evidence(
        events,
        messages,
        images_by_event,
        profile,
        request,
        rates_by_key=rates_by_key,
    )
    salary_forces = evidence.salary_series_overrides(meta)

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
        series_amount_forces=salary_forces,
    )
    methods_considered = set(
        profile.get("payment_methods_user_will_consider_list") or []
    )
    # If the user will not pay in full but capacity covers the request, keep a
    # remainder so partial_payment can be recommended per the output contract.
    if (
        "full_payment" not in methods_considered
        and "partial_payment" in methods_considered
        and bool(request.get("allows_partial_payment"))
        and safe_amt + 1e-9 >= req
        and req > 0.01
    ):
        safe_amt = round(req - 0.01, 2)

    earliest = cashflow.earliest_full_payment_date(
        starting_balance,
        request_date,
        req,
        minimum,
        events,
        currency,
        rates,
        series_amount_forces=salary_forces,
    )
    daily_deltas = cashflow.build_daily_deltas(
        events, request_date, series_amount_forces=salary_forces
    )

    candidates = _build_candidates(
        request,
        profile,
        options,
        safe_amt,
        earliest,
        starting_balance,
        daily_deltas,
        minimum,
        spending_changes="none",
    )
    # Always consider spending-change plans; ranking prefers no-cut winners first.
    cut_candidates = _search_with_spending_changes(
        request,
        profile,
        events,
        options,
        safe_amt,
        earliest,
        starting_balance,
        minimum,
        series_amount_forces=salary_forces,
    )
    candidates.extend(cut_candidates)

    # Pay-today with cuts vs wait: when the request forbids partials, prefer
    # unlocking today; when partials are allowed, keep a deadline-safe wait
    # instead of stop/reduce-funded full payment (sample request_04 vs 11).
    cut_full_today = [
        c
        for c in candidates
        if c.method == "full_payment"
        and c.spending_changes != "none"
        and c.start_date == request_date
        and c.completes_by_deadline
    ]
    no_cut_wait = any(
        c.method == "wait"
        and c.spending_changes == "none"
        and c.completes_by_deadline
        for c in candidates
    )
    allows_partial = bool(request.get("allows_partial_payment"))
    if cut_full_today and allows_partial and no_cut_wait:
        drop = {id(c) for c in cut_full_today}
        candidates = [c for c in candidates if id(c) not in drop]
    elif cut_full_today:
        candidates = [c for c in candidates if c.method != "wait"]

    # Drop cut variants when the same method is already safe with no cuts.
    no_cut_methods = {
        c.method for c in candidates if c.spending_changes == "none"
    }
    candidates = [
        c
        for c in candidates
        if c.spending_changes == "none" or c.method not in no_cut_methods
    ]

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
