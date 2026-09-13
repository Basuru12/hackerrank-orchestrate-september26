"""Buy or Wait — orchestration entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CODE_DIR.parent
DATASET_DIR = REPO_ROOT / "dataset"
OUTPUT_PATH = REPO_ROOT / "output.csv"

if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import cashflow  # noqa: E402
import decide  # noqa: E402
import load_data  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Buy or Wait financial decision agent")
    parser.add_argument(
        "--samples",
        action="store_true",
        help="Compare decisions against sample_requests.csv",
    )
    return parser.parse_args(argv)


def _run_load_smoke(data: load_data.Dataset) -> int:
    event_count = sum(len(events) for events in data.events_by_user.values())
    option_count = sum(len(opts) for opts in data.options_by_request.values())
    msg_count = sum(len(msgs) for msgs in data.messages_by_user.values())
    print(
        "loaded "
        f"requests={len(data.requests)} "
        f"profiles={len(data.profiles_by_user)} "
        f"events={event_count} "
        f"options={option_count} "
        f"rates={len(data.rates)} "
        f"samples={len(data.sample_requests)} "
        f"messages={msg_count} "
        f"images={len(data.images_by_event)}"
    )
    first = data.requests[0]
    user_id = first["user_id"]
    profile = data.profiles_by_user.get(user_id)
    user_events = data.events_by_user.get(user_id, [])
    if profile is None or not user_events:
        print(f"join failed {first['request_id']} {user_id}")
        return 1
    print(f"join ok {first['request_id']} {user_id} events={len(user_events)}")
    return 0


def _normalize_plan(plan: str | None) -> str:
    text = (plan or "none").strip()
    return text if text else "none"


def _run_sample_validation(data: load_data.Dataset) -> int:
    safe_exact = 0
    safe_within_1pct = 0
    earliest_exact = 0
    method_exact = 0
    status_exact = 0
    plan_exact = 0
    changes_exact = 0
    total = len(data.sample_requests)
    mismatches: list[str] = []
    focus_ids = {"request_06", "request_11", "request_21", "request_01"}

    for sample in data.sample_requests:
        request_id = sample["request_id"]
        user_id = sample["user_id"]
        profile = data.profiles_by_user[user_id]
        events = data.events_by_user.get(user_id, [])
        options = data.options_by_request.get(request_id, [])

        messages = data.messages_by_user.get(user_id, [])
        decision = decide.decide_for_request(
            sample,
            profile=profile,
            events=events,
            options=options,
            rates=data.rates,
            messages=messages,
            images_by_event=data.images_by_event,
            rates_by_key=data.rates_by_key,
        )

        label_safe = float(sample.get("amount_safe_to_pay", 0) or 0)
        label_earliest = sample.get("earliest_date_for_full_payment")
        label_earliest_str = (
            label_earliest.isoformat()
            if hasattr(label_earliest, "isoformat") and label_earliest is not None
            else (str(label_earliest) if label_earliest else "")
        )
        label_method = sample.get("recommended_payment_method")
        label_status = sample.get("affordability_status")
        label_plan = _normalize_plan(sample.get("payment_plan"))
        label_changes = _normalize_plan(sample.get("spending_changes_needed"))

        pred_safe = decision.amount_safe_to_pay
        pred_earliest = decision.earliest_date_for_full_payment
        safe_diff = abs(pred_safe - label_safe)
        exact_safe = safe_diff < 0.015
        within_1 = (label_safe == 0 and pred_safe == 0) or (
            label_safe > 0 and safe_diff / label_safe <= 0.01
        )
        if exact_safe:
            safe_exact += 1
        if within_1 or exact_safe:
            safe_within_1pct += 1
        if pred_earliest == label_earliest_str:
            earliest_exact += 1

        method_ok = decision.recommended_payment_method == label_method
        status_ok = decision.affordability_status == label_status
        plan_ok = _normalize_plan(decision.payment_plan) == label_plan
        changes_ok = _normalize_plan(decision.spending_changes_needed) == label_changes
        if method_ok:
            method_exact += 1
        if status_ok:
            status_exact += 1
        if plan_ok:
            plan_exact += 1
        if changes_ok:
            changes_exact += 1

        if request_id in focus_ids or not (method_ok and status_ok and changes_ok):
            print(
                f"{request_id}: method pred={decision.recommended_payment_method} "
                f"label={label_method} | status pred={decision.affordability_status} "
                f"label={label_status} | changes pred={decision.spending_changes_needed!r} "
                f"label={label_changes!r} | plan_ok={plan_ok}"
            )
        if not (method_ok and status_ok and plan_ok and changes_ok):
            mismatches.append(
                f"{request_id}: method {decision.recommended_payment_method}!={label_method}; "
                f"status {decision.affordability_status}!={label_status}; "
                f"changes {decision.spending_changes_needed!r}!={label_changes!r}; "
                f"plan pred={decision.payment_plan!r} label={label_plan!r}"
            )

    print(
        f"summary safe_exact={safe_exact}/{total} "
        f"safe_within_1pct={safe_within_1pct}/{total} "
        f"earliest_exact={earliest_exact}/{total} "
        f"method_exact={method_exact}/{total} "
        f"status_exact={status_exact}/{total} "
        f"plan_exact={plan_exact}/{total} "
        f"changes_exact={changes_exact}/{total}"
    )
    if mismatches:
        print("mismatches (up to 10):")
        for line in mismatches[:10]:
            print(f"  {line}")

    _ = cashflow
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _ = OUTPUT_PATH
    data = load_data.load_dataset(DATASET_DIR)
    if args.samples:
        return _run_sample_validation(data)
    return _run_load_smoke(data)


if __name__ == "__main__":
    raise SystemExit(main())
