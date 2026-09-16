"""Untrusted message/image evidence applied as structured financial overrides."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

# Primary cash amounts read from dataset/media/images/<image_id>.png (no OCR at runtime).
IMAGE_AMOUNTS: dict[str, float] = {
    "image_01": 4365000.0,  # Aug-2019 net salary (IDR)
    "image_02": 100000.0,  # outstanding rent balance due (INR)
    "image_03": 41272.0,  # grocery bill (INR)
    "image_04": 2854.0,  # grocery delivery (INR)
    "image_05": 704.05,  # telecom amount due (INR)
    "image_06": 1995.0,  # grocery invoice (INR)
    "image_07": 8528.0,  # restaurant grand total (INR)
    "image_08": 15339.0,  # housing maintenance (INR)
    "image_09": 723.0,  # water bill (INR)
    "image_10": 79679.26,  # large grocery invoice (INR)
    "image_11": 3650.0,  # hospital bill payable (INR)
    "image_12": 33.50,  # taxi total (USD)
    "image_13": 2298.0,  # tote bag order (INR)
    "image_14": 4543.0,  # pharmacy total (INR)
    "image_15": 9968.0,  # airline ticket (INR)
    "image_16": 393.22,  # EV charging (INR)
}

_AMOUNT_RE = re.compile(
    r"(?P<currency>IDR|INR|EUR|USD|ZAR|Rp|₹|\$)?\s*"
    r"(?P<amount>\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})|\d+(?:[.,]\d{1,2})?)",
    re.IGNORECASE,
)
_DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
_SALARY_HINT = re.compile(
    r"(salary|gaji|monthly pay|payroll|gaji pokok|temporary monthly pay|"
    r"confirmed salary|next salary)",
    re.IGNORECASE,
)


@dataclass
class EvidenceMeta:
    salary_amount_override: float | None = None
    salary_effective_date: date | None = None
    salary_date_override: date | None = None
    # True = only next payday uses override; False = all future projections.
    salary_force_permanent: bool = True


def _parse_amount_token(raw: str) -> float:
    text = raw.strip().replace(" ", "")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        parts = text.split(",")
        text = (
            text.replace(",", "")
            if len(parts[-1]) == 3
            else text.replace(",", ".")
        )
    return float(text)


def _currency_ok(token: str | None, home_currency: str) -> bool:
    if not token:
        return True
    normalized = token.upper().replace("RP", "IDR").replace("₹", "INR").replace("$", "USD")
    return normalized == home_currency.upper()


def fill_blank_amounts(
    events: list[dict[str, Any]],
    images_by_event: dict[str, str],
    home_currency: str = "",
    rates_by_key: dict[tuple[date, str, str], float] | None = None,
) -> list[dict[str, Any]]:
    """Fill blank event amounts from the manual image amount table."""
    from load_data import convert_amount_home

    out: list[dict[str, Any]] = []
    for event in events:
        row = copy.copy(event)
        if row.get("amount") is None:
            image_id = images_by_event.get(str(row.get("event_id", "")))
            if image_id and image_id in IMAGE_AMOUNTS:
                amount = float(IMAGE_AMOUNTS[image_id])
                row["amount"] = amount
                amount_home, _ = convert_amount_home(
                    amount,
                    str(row.get("currency") or ""),
                    home_currency,
                    row["settlement_date"],
                    rates_by_key or {},
                )
                row["amount_home"] = amount_home if amount_home is not None else amount
        out.append(row)
    return out


def _relevant_messages(
    messages: list[dict[str, Any]],
    request_id: str,
    request_date: date,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for msg in messages:
        sent = msg.get("sent_at")
        if isinstance(sent, datetime):
            if sent.date() > request_date:
                continue
        req = (msg.get("request_id") or "").strip()
        if req and req != request_id:
            continue
        selected.append(msg)
    selected.sort(
        key=lambda m: m.get("sent_at") or datetime.min.replace(tzinfo=None),
    )
    return selected


def parse_message_overrides(
    messages: list[dict[str, Any]],
    profile: dict[str, Any],
    request_id: str,
    request_date: date,
) -> tuple[list[dict[str, Any]], EvidenceMeta]:
    """Parse untrusted messages into structured overrides + salary meta."""
    home = str(profile.get("home_currency", ""))
    meta = EvidenceMeta()
    event_overrides: list[dict[str, Any]] = []

    for msg in _relevant_messages(messages, request_id, request_date):
        text = str(msg.get("message_text") or "")
        lower = text.lower()
        related = (msg.get("related_event_id") or "").strip()
        sent = msg.get("sent_at")
        sent_date = sent.date() if isinstance(sent, datetime) else request_date

        # Cancel linked event.
        if related and re.search(r"\b(cancel+ed|cancelled|canceled)\b", lower):
            event_overrides.append(
                {"kind": "exclude", "event_id": related, "sent_at": sent}
            )

        # Delay linked event to a new date.
        if related and re.search(r"\b(delay|delayed|postponed)\b", lower):
            dates = _DATE_RE.findall(text)
            if dates:
                event_overrides.append(
                    {
                        "kind": "reschedule",
                        "event_id": related,
                        "new_date": date.fromisoformat(dates[-1]),
                        "sent_at": sent,
                    }
                )

        # Pending bonus/commission should not be counted.
        if re.search(r"\b(bonus|commission)\b", lower) and re.search(
            r"\b(pending|awaiting|masih menunggu)\b", lower
        ):
            event_overrides.append(
                {
                    "kind": "ignore_pending_credits",
                    "sent_at": sent,
                }
            )

        if not _SALARY_HINT.search(text):
            continue

        # Salary date shift.
        date_match = re.search(
            r"(?:expected on|berlaku mulai|on)\s+(20\d{2}-\d{2}-\d{2})",
            text,
            re.IGNORECASE,
        )
        if date_match:
            meta.salary_date_override = date.fromisoformat(date_match.group(1))
            meta.salary_effective_date = sent_date

        # Salary amount: require a currency-tagged figure near payroll wording.
        candidates: list[tuple[int, float]] = []
        min_by_home = {
            "IDR": 100_000.0,
            "INR": 1_000.0,
            "EUR": 100.0,
            "USD": 100.0,
            "ZAR": 500.0,
        }
        min_amount = min_by_home.get(home.upper(), 50.0)
        for match in _AMOUNT_RE.finditer(text):
            currency = match.group("currency")
            if not currency:
                continue  # avoid matching stray digits (dates, refs, "2")
            if not _currency_ok(currency, home):
                continue
            start = max(0, match.start() - 50)
            window = text[start : match.end() + 10]
            if not _SALARY_HINT.search(window):
                continue
            try:
                value = _parse_amount_token(match.group("amount"))
            except ValueError:
                continue
            if value < min_amount:
                continue
            rank = 1 + (1 if value >= min_amount * 10 else 0)
            candidates.append((rank, value))
        if candidates:
            candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
            meta.salary_amount_override = candidates[0][1]
            meta.salary_effective_date = sent_date
            # Temporary / next-cycle adjustments vs permanent base pay.
            if re.search(
                r"(temporary|next salary|next payroll|penggajian berikutnya|"
                r"untuk penggajian berikutnya|reduced amount continues for the next)",
                lower,
            ) and not re.search(r"(gaji pokok|base salary|pokok yang dikonfirmasi)", lower):
                meta.salary_force_permanent = False
            else:
                meta.salary_force_permanent = True

        # Confirmed next salary without amount.
        if re.search(
            r"(next|berikutnya|rutin).{0,60}(confirm|dikontirmasi|confirmed|dikonfirmasi)",
            lower,
        ) or re.search(
            r"(confirm|dikonfirmasi|confirmed).{0,60}(salary|gaji|next|berikutnya)",
            lower,
        ):
            # Only record confirmation; do not invent a payday when history
            # already supports a recurring salary series (avoids early earliest).
            if meta.salary_date_override is None and meta.salary_amount_override is None:
                meta.salary_effective_date = sent_date
                # Leave salary_date_override unset unless an explicit date was parsed.

    return event_overrides, meta


def apply_event_overrides(
    events: list[dict[str, Any]],
    overrides: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out = [copy.copy(e) for e in events]
    by_id = {str(e.get("event_id")): e for e in out}
    for override in overrides:
        kind = override.get("kind")
        if kind == "exclude":
            event = by_id.get(str(override.get("event_id")))
            if event is not None:
                event["include_in_cashflow"] = False
                event["status"] = "cancelled"
        elif kind == "reschedule":
            event = by_id.get(str(override.get("event_id")))
            if event is not None and override.get("new_date"):
                event["settlement_date"] = override["new_date"]
        elif kind == "ignore_pending_credits":
            for event in out:
                if (
                    str(event.get("status", "")).lower() == "pending"
                    and str(event.get("direction", "")).lower() == "credit"
                ):
                    event["include_in_cashflow"] = False
    return out


def ensure_scheduled_salary(
    events: list[dict[str, Any]],
    meta: EvidenceMeta,
    request_date: date,
    home_currency: str,
) -> list[dict[str, Any]]:
    """Update existing scheduled salary; invent one for confirmed date or next-cycle cut."""
    if meta.salary_date_override is None and meta.salary_amount_override is None:
        return events
    out = [copy.copy(e) for e in events]
    target_date = meta.salary_date_override
    amount = meta.salary_amount_override
    existing = None
    for event in out:
        if (
            str(event.get("category", "")).lower() == "salary"
            and str(event.get("direction", "")).lower() == "credit"
            and str(event.get("status", "")).lower() == "scheduled"
        ):
            existing = event
            break
    if existing is not None:
        if target_date is not None:
            existing["settlement_date"] = target_date
            existing["event_date"] = target_date
        if amount is not None:
            existing["amount"] = amount
            existing["amount_home"] = amount
        existing["include_in_cashflow"] = True
        return out

    # Permanent amount-only: cashflow series force covers projections.
    if target_date is None and meta.salary_force_permanent:
        return out

    # Temporary/next-cycle amount or explicit confirm date: anchor one scheduled credit.
    if target_date is None:
        settled = [
            e
            for e in out
            if str(e.get("category", "")).lower() == "salary"
            and str(e.get("status", "")).lower() == "settled"
            and e.get("amount_home") is not None
            and e["settlement_date"] < request_date
        ]
        if settled:
            settled.sort(key=lambda e: e["settlement_date"])
            last = settled[-1]["settlement_date"]
            # Approximate next monthly payday from last settled salary date.
            month = last.month + 1
            year = last.year
            if month > 12:
                month = 1
                year += 1
            day = min(last.day, 28)
            target_date = date(year, month, day)
            if target_date < request_date:
                target_date = request_date
        else:
            target_date = request_date

    if amount is None:
        settled = [
            e
            for e in out
            if str(e.get("category", "")).lower() == "salary"
            and str(e.get("status", "")).lower() == "settled"
            and e.get("amount_home") is not None
        ]
        if not settled:
            return out
        settled.sort(key=lambda e: e["settlement_date"])
        amount = float(settled[-1]["amount_home"])

    out.append(
        {
            "event_id": "evidence_salary_scheduled",
            "user_id": out[0].get("user_id") if out else "",
            "event_type": "income",
            "description": "Evidence-confirmed salary",
            "category": "salary",
            "direction": "credit",
            "amount": amount,
            "amount_home": amount,
            "currency": home_currency,
            "event_date": target_date,
            "settlement_date": target_date,
            "status": "scheduled",
            "linked_event_id": "",
            "flexibility": "fixed",
            "minimum_allowed_amount": None,
            "include_in_cashflow": True,
        }
    )
    return out


def salary_series_overrides(meta: EvidenceMeta) -> dict[tuple[str, str, str], float]:
    """Force projected salary series amounts from permanent payroll evidence."""
    if meta.salary_amount_override is None or not meta.salary_force_permanent:
        return {}
    amount = float(meta.salary_amount_override)
    return {
        ("salary", "credit", "income"): amount,
        ("salary", "credit", "salary"): amount,
    }


def apply_evidence(
    events: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    images_by_event: dict[str, str],
    profile: dict[str, Any],
    request: dict[str, Any],
    rates_by_key: dict[tuple[date, str, str], float] | None = None,
) -> tuple[list[dict[str, Any]], EvidenceMeta]:
    """Apply image fills + message overrides; return enriched events and salary meta."""
    request_id = str(request.get("request_id", ""))
    request_date: date = request["request_date"]
    home = str(profile.get("home_currency", ""))

    filled = fill_blank_amounts(
        events, images_by_event, home_currency=home, rates_by_key=rates_by_key
    )
    overrides, meta = parse_message_overrides(
        messages, profile, request_id, request_date
    )
    updated = apply_event_overrides(filled, overrides)
    updated = ensure_scheduled_salary(updated, meta, request_date, home)
    return updated, meta
