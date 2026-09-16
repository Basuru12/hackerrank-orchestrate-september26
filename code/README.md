# Buy or Wait — solution

Deterministic Python agent for the HackerRank Orchestrate **Buy or Wait?** challenge.

## Requirements

- Python 3.10+ (tested with 3.14)
- Standard library only (no third-party packages, no API keys)

## Run

From the repository root:

```bash
# Score all 250 evaluation requests → repository-root output.csv
python code/main.py

# Optional: compare against sample_requests.csv
python code/main.py --samples
```

## Layout

```text
code/
  main.py              Entry point
  load_data.py         CSV loaders, FX, indexes
  evidence.py          Messages/images structured overrides
  cashflow.py          90-day forecast, safe amount, earliest date
  decide.py            Payment candidates, ranking, spending changes
  evaluation/
    usage_report.md    Token/cost report for the full run
  README.md
```

## Output

`python code/main.py` writes `output.csv` at the repository root with one row per
`dataset/requests.csv` request and these columns (exact order):

```text
request_id,amount_safe_to_pay,affordability_status,recommended_payment_method,
payment_plan,earliest_date_for_full_payment,spending_changes_needed,decision_explanation
```

## Approach (short)

1. Reconstruct cash position from profiles, events, and fixed FX rates.
2. Apply message/image evidence as structured financial overrides (not as rule overrides).
3. Forecast ~90 days; compute `amount_safe_to_pay` and earliest full-payment date.
4. Rank eligible plans: full / partial / installments / wait / not recommended, with optional spending changes on flexible categories only.
