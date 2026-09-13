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
    # Reserved for Phase 7 sample comparison; unused in Phase 2.
    parser.add_argument(
        "--samples",
        action="store_true",
        help="Compare predictions against sample_requests.csv (Phase 7)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    parse_args(argv)
    _ = (cashflow, decide, OUTPUT_PATH)

    data = load_data.load_dataset(DATASET_DIR)
    event_count = sum(len(events) for events in data.events_by_user.values())
    option_count = sum(len(opts) for opts in data.options_by_request.values())

    print(
        "loaded "
        f"requests={len(data.requests)} "
        f"profiles={len(data.profiles_by_user)} "
        f"events={event_count} "
        f"options={option_count} "
        f"rates={len(data.rates)} "
        f"samples={len(data.sample_requests)}"
    )

    first = data.requests[0]
    user_id = first["user_id"]
    profile = data.profiles_by_user.get(user_id)
    user_events = data.events_by_user.get(user_id, [])
    if profile is None:
        print(f"join failed {first['request_id']} {user_id}: missing profile")
        return 1
    if not user_events:
        print(f"join failed {first['request_id']} {user_id}: no events")
        return 1

    user_01 = data.profiles_by_user.get("user_01")
    max_inst = user_01.get("max_installment_months") if user_01 else "missing"
    print(
        f"join ok {first['request_id']} {user_id} events={len(user_events)} "
        f"user_01.max_installment_months={max_inst!r} fx_missing={data.fx_missing_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
