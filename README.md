# SFH IRS Transcript Analysis

Tooling to cross-reference IRS Wage & Income Transcripts against Delta CSV
exports, broker/custodian statements, and a counterparty matrix, following
the [SFH IRS Transcript Analysis Framework](docs/SFH-TAF-2026-001.md)
(SFH-TAF-2026-001).

## Layout

```
docs/SFH-TAF-2026-001.md   The framework document (Steps 1-5)
templates/                 Empty CSVs with the exact column headers the framework calls for
data/
  transcripts/*.csv        Wage & Income Transcript line items (one row per payer/form/year)
  delta/*.csv              Delta CSV exports (proceeds/cost basis per payer)
  broker_statements/*.csv  Which payers SFH has account statements from
  counterparty_matrix.csv  Known accounts, status, and dispute tier
scripts/cross_reference.py Runs Steps 2-4: cross-reference, gap analysis, summary table
outputs/                   Generated summary_table.csv and gap_analysis.md (git-ignored)
exhibits/                  Exhibit files, named SFH-EV-2026-XXX per the framework
```

## Usage

1. Copy each file in `templates/` into the matching `data/` subdirectory and
   fill it in from the actual IRS transcripts, Delta CSVs, and broker
   statements (or drop real exports directly into `data/transcripts/`,
   `data/delta/`, `data/broker_statements/`, using the same column headers).
2. Update `data/counterparty_matrix.csv` with any accounts not already
   pre-populated from the framework's "Known Expected Filers" list.
3. Run:

   ```
   python3 scripts/cross_reference.py
   ```

   This writes `outputs/summary_table.csv` (Step 4 of the framework) and
   `outputs/gap_analysis.md` (Step 3 gap analysis, grouped by Gap Type A-D,
   with the recommended action for each and a priority-flags section for
   NFS/Plynk, TD Ameritrade/Schwab, unknown filers, and IBKR/Coinbase gaps).
4. Entries classified as Gap Type D (a disputed account filed a 1099 for
   proceeds SFH never received) are auto-assigned the next `SFH-EV-2026-XXX`
   exhibit number — copy the supporting documents into `exhibits/` under
   that number.

Step 5 (filing status per year) and Step 1 (raw transcript intake) are
manual — use `templates/transcript_intake_template.csv` for intake and track
filing status separately, since it depends on a *Tax Return Transcript*
rather than the Wage & Income Transcript this tooling cross-references.
