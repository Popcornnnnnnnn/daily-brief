# Evaluating Daily Brief (`eval.md`)

Run the invariant suite from the repository root (`daily-brief/`):

```sh
python3 -m unittest discover -s tests -v
python3 publisher/privacy_gate.py .
pnpm --dir site build
```

## Verification Checklist

1. **Workflow & Receipt Invariants (`[1.1]`–`[4.2]`):**
   - `tests/test_brief_workflow.py`: verifies lossless bounded partitions (`<= 25` items / `<= 22 KB`), family interleaving, SHA tamper failure, mixed-run detection, timestamp classification (`needs_original_date` for sitemaps), receipt validation, linked `correction_of`, and 20-example holdout evaluation gating.
   - `tests/test_brief_feedback.py`: verifies strict quote/locator requirements, retraction handling, SHA-guarded index merges, and non-contamination of legacy signals.
   - `tests/test_execution_audit.py`: verifies audit completeness invariants.
2. **Public Publication & Privacy Boundary (`[4.3]`):**
   - `tests/test_publisher.py`: verifies Notion block conversion, private section stripping, legacy control removal, S3 media localization, and fail-closed privacy gate enforcement.
   - `python3 publisher/privacy_gate.py .` exits `0` with zero detected leaks.
3. **Static Editorial Site Build:**
   - `pnpm --dir site build` exits `0`, emits HTML for `/`, `/archive/`, `/how-it-works/`, every `/issues/YYYY-MM-DD/` page, `/rss.xml`, `/feed.json`, and `/llms.txt`.
