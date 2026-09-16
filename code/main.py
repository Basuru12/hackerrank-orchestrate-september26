"""Buy or Wait — orchestration entry point."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CODE_DIR.parent
DATASET_DIR = REPO_ROOT / "dataset"
OUTPUT_PATH = REPO_ROOT / "output.csv"

OUTPUT_COLUMNS = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]

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


def _decision_to_row(decision: decide.Decision) -> dict[str, str]:
    safe = decision.amount_safe_to_pay
    if abs(safe - round(safe)) < 1e-9:
        safe_str = str(int(round(safe)))
    else:
        safe_str = f"{safe:.2f}"
    return {
        "request_id": decision.request_id,
        "amount_safe_to_pay": safe_str,
        "affordability_status": decision.affordability_status,
        "recommended_payment_method": decision.recommended_payment_method,
        "payment_plan": decision.payment_plan or "none",
        "earliest_date_for_full_payment": decision.earliest_date_for_full_payment or "",
        "spending_changes_needed": decision.spending_changes_needed or "none",
        "decision_explanation": decision.decision_explanation or "",
    }


def _decide_one(data: load_data.Dataset, request: dict) -> decide.Decision:
    user_id = request["user_id"]
    request_id = request["request_id"]
    return decide.decide_for_request(
        request,
        profile=data.profiles_by_user.get(user_id, {}),
        events=data.events_by_user.get(user_id, []),
        options=data.options_by_request.get(request_id, []),
        rates=data.rates,
        messages=data.messages_by_user.get(user_id, []),
        images_by_event=data.images_by_event,
        rates_by_key=data.rates_by_key,
    )


def _run_full_predictions(data: load_data.Dataset, output_path: Path) -> int:
    """Score every eval request and write root output.csv."""
    rows: list[dict[str, str]] = []
    for index, request in enumerate(data.requests, start=1):
        decision = _decide_one(data, request)
        rows.append(_decision_to_row(decision))
        if index == 1 or index % 50 == 0 or index == len(data.requests):
            print(f"scored {index}/{len(data.requests)} {request['request_id']}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    print(f"wrote {len(rows)} rows to {output_path}")
    return 0 if len(rows) == len(data.requests) else 1


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
    safe_fails: list[tuple[float, str]] = []
    focus_ids = {
        "request_01",
        "request_06",
        "request_08",
        "request_11",
        "request_21",
    }
    request_01_ok = True

    for sample in data.sample_requests:
        request_id = sample["request_id"]
        decision = _decide_one(data, sample)

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
        else:
            pct = (
                100.0 * safe_diff / label_safe
                if label_safe > 0
                else (100.0 if pred_safe else 0.0)
            )
            safe_fails.append(
                (
                    safe_diff,
                    f"{request_id}: safe label={label_safe} pred={pred_safe} "
                    f"diff={safe_diff:.2f} ({pct:.1f}%)",
                )
            )
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

        if request_id == "request_01" and not (
            exact_safe and pred_earliest == label_earliest_str and method_ok
        ):
            request_01_ok = False

        if request_id in focus_ids or not method_ok:
            print(
                f"{request_id}: method pred={decision.recommended_payment_method} "
                f"label={label_method} | status pred={decision.affordability_status} "
                f"label={label_status} | changes pred={decision.spending_changes_needed!r} "
                f"label={label_changes!r} | plan_ok={plan_ok} | "
                f"safe {pred_safe}/{label_safe} | ear {pred_earliest!r}/{label_earliest_str!r}"
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
    print(
        "fails "
        f"safe={total - safe_exact} "
        f"safe_1pct={total - safe_within_1pct} "
        f"earliest={total - earliest_exact} "
        f"method={total - method_exact} "
        f"status={total - status_exact} "
        f"plan={total - plan_exact} "
        f"changes={total - changes_exact} "
        f"request_01_ok={request_01_ok}"
    )
    if safe_fails:
        print("worst safe deltas:")
        for _, line in sorted(safe_fails, key=lambda item: item[0], reverse=True)[:8]:
            print(f"  {line}")
    if mismatches:
        print("mismatches (up to 10):")
        for line in mismatches[:10]:
            print(f"  {line}")

    _ = cashflow
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    data = load_data.load_dataset(DATASET_DIR)
    if args.samples:
        return _run_sample_validation(data)
    return _run_full_predictions(data, OUTPUT_PATH)


if __name__ == "__main__":
    raise SystemExit(main())
