# Token / model usage report

Final full-dataset run that produced repository-root `output.csv`.

## Summary

| Field | Value |
|---|---|
| Run mode | Deterministic rule-based (no LLM / vision / OCR APIs) |
| Model providers | none |
| Model names | none |
| Model calls | 0 |
| Input tokens | 0 |
| Output tokens | 0 |
| Total tokens | 0 |
| Requests scored | 250 |
| Average tokens per request | 0 |
| Estimated total cost | $0.00 |
| Estimated cost per request | $0.00 |

## Notes

- Decisions are produced by Python modules under `code/` (`load_data`, `evidence`, `cashflow`, `decide`) using only the participant-facing files in `dataset/`.
- Messages and images are applied via regex / rule parsers and a manual 16-image amount table. No remote model APIs were called during the scored run.
- Command: `python code/main.py` from the repository root.
