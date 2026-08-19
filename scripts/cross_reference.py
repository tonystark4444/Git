#!/usr/bin/env python3
"""
SFH IRS Transcript Analysis Framework -- cross-reference engine.

Implements Steps 2-4 of docs/SFH-TAF-2026-001.md:
  - Check A: transcript vs Delta CSV data
  - Check B: transcript vs broker/custodian statements + counterparty matrix
  - Check C / Step 3: gap analysis (Gap Types A-D) and exhibit flagging
  - Step 4: summary table

Input (see templates/ for exact column headers):
  data/transcripts/*.csv       one row per Wage & Income Transcript line item
  data/delta/*.csv             Delta CSV exports (proceeds/cost basis by payer)
  data/broker_statements/*.csv account statement confirmation per payer
  data/counterparty_matrix.csv known accounts and dispute tier

Output:
  outputs/summary_table.csv
  outputs/gap_analysis.md
"""
import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AMOUNT_TOLERANCE = 1.00  # dollars; reconciled if abs(discrepancy) <= this

PRIORITY_PAYER_HINTS = {
    "plynk": "NFS/Plynk filed a 1099-B for SPAX proceeds -- direct dispute evidence",
    "nfs": "NFS/Plynk filed a 1099-B for SPAX proceeds -- direct dispute evidence",
    "td ameritrade": "TD Ameritrade filed a 1099-B for disputed account -- confirms liquidation",
    "schwab": "TD Ameritrade/Schwab filed a 1099-B for disputed account -- confirms liquidation",
    "wells fargo": "Wells Fargo filed a 1099 for a disputed account",
    "ibkr": "IBKR 1099 activity -- confirm vs. no-1099 case in Gap Type B",
    "interactive brokers": "IBKR 1099 activity -- confirm vs. no-1099 case in Gap Type B",
}


def normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (name or "").lower()).strip()


def tokenize(name: str):
    return set(re.findall(r"[a-z0-9]+", (name or "").lower()))


def names_match(a: str, b: str) -> bool:
    """Order-independent match, e.g. 'NFS/Plynk' vs 'Plynk/NFS', or 'IBKR' vs 'Interactive Brokers IBKR'."""
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return False
    return ta == tb or ta.issubset(tb) or tb.issubset(ta)


def read_csv_rows(path: Path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_many(dir_path: Path):
    rows = []
    if not dir_path.exists():
        return rows
    for csv_file in sorted(dir_path.glob("*.csv")):
        rows.extend(read_csv_rows(csv_file))
    return rows


def to_float(value):
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").replace("$", ""))
    except ValueError:
        return None


def check_a_delta(payer_name, box_amount, delta_rows):
    matches = [r for r in delta_rows if names_match(payer_name, r.get("payer_name", ""))]
    if not matches:
        return {"in_delta": "No", "reconciles": "N/A", "discrepancy": None}
    delta_total = 0.0
    any_amount = False
    for r in matches:
        amt = to_float(r.get("proceeds"))
        if amt is None:
            amt = to_float(r.get("gain_loss"))
        if amt is not None:
            delta_total += amt
            any_amount = True
    box_amt = to_float(box_amount)
    if not any_amount or box_amt is None:
        return {"in_delta": "Yes", "reconciles": "Unknown", "discrepancy": None}
    discrepancy = round(box_amt - delta_total, 2)
    reconciles = "Yes" if abs(discrepancy) <= AMOUNT_TOLERANCE else "No"
    return {"in_delta": "Yes", "reconciles": reconciles, "discrepancy": discrepancy}


def check_b_statements(payer_name, statement_rows, matrix_rows):
    stmt = next((r for r in statement_rows if names_match(payer_name, r.get("payer_name", ""))), None)
    matrix = next((r for r in matrix_rows if names_match(payer_name, r.get("payer_name", ""))), None)
    return {
        "has_statement": stmt is not None and (stmt.get("statement_provided") or "").strip().lower() == "yes",
        "account_status": (stmt.get("account_status") if stmt else (matrix.get("account_status") if matrix else "unknown")),
        "dispute_tier": ((matrix.get("dispute_tier") or "").strip().lower() == "yes") if matrix else False,
        "in_matrix": matrix is not None,
    }


def classify_gap(transcript_row, check_a, check_b):
    """Returns (gap_type, action, priority_note) per Step 3 of the framework."""
    payer = transcript_row.get("payer_name", "")
    priority_note = next(
        (note for hint, note in PRIORITY_PAYER_HINTS.items() if hint in normalize(payer)), None
    )

    # Gap Type D: disputed account filed a 1099 for proceeds SFH never received
    if check_b["dispute_tier"] and not check_b["has_statement"]:
        return "D", "Direct evidence -- assign exhibit number, route to attorney and FINRA filing", priority_note

    # Gap Type A: filer in transcript, not in SFH records at all
    if not check_b["in_matrix"] and not check_b["has_statement"]:
        return "A", "Formal records request; attorney demand if dispute", priority_note

    # Gap Type C: 1099 amount vs Delta CSV discrepancy
    if check_a["discrepancy"] is not None and abs(check_a["discrepancy"]) > AMOUNT_TOLERANCE:
        return (
            "C",
            "Flag for CPA; may indicate missing transactions, incorrect cost basis, or broker reporting error",
            priority_note,
        )

    return None, None, priority_note


def find_gap_type_b(matrix_rows, statement_rows, transcript_rows):
    """Known account / statement activity with no matching 1099 anywhere in the transcript."""
    known_payers = [r.get("payer_name", "") for r in matrix_rows]
    known_payers += [
        r.get("payer_name", "")
        for r in statement_rows
        if (r.get("statement_provided") or "").strip().lower() == "yes"
    ]

    seen, results = [], []
    for payer in known_payers:
        if not payer or any(names_match(payer, s) for s in seen):
            continue
        seen.append(payer)
        if not any(names_match(payer, t.get("payer_name", "")) for t in transcript_rows):
            results.append(payer)
    return results


def next_exhibit_number(counter):
    counter["n"] += 1
    return f"SFH-EV-2026-{counter['n']:03d}"


def run(data_dir: Path, output_dir: Path):
    transcript_rows = load_many(data_dir / "transcripts")
    delta_rows = load_many(data_dir / "delta")
    statement_rows = load_many(data_dir / "broker_statements")
    matrix_rows = read_csv_rows(data_dir / "counterparty_matrix.csv")

    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "summary_table.csv"
    gap_report_path = output_dir / "gap_analysis.md"

    exhibit_counter = {"n": 0}
    summary_rows = []
    gaps_by_type = defaultdict(list)
    priority_hits = []

    for row in transcript_rows:
        payer = row.get("payer_name", "")
        box_amount = row.get("box_amount")
        check_a = check_a_delta(payer, box_amount, delta_rows)
        check_b = check_b_statements(payer, statement_rows, matrix_rows)
        gap_type, action, priority_note = classify_gap(row, check_a, check_b)

        exhibit_no = ""
        priority = "Low"
        if gap_type == "D":
            exhibit_no = next_exhibit_number(exhibit_counter)
            priority = "High"
        elif gap_type in ("A", "C"):
            priority = "Medium"
        if priority_note:
            priority = "High"
            priority_hits.append((payer, priority_note))

        summary_rows.append({
            "tax_year": row.get("tax_year", ""),
            "payer": payer,
            "form": row.get("form_type", ""),
            "amount": box_amount,
            "delta_match": check_a["reconciles"],
            "statement_match": "Yes" if check_b["has_statement"] else "No",
            "gap_type": gap_type or "",
            "exhibit_number": exhibit_no,
            "priority": priority,
        })

        if gap_type:
            gaps_by_type[gap_type].append({
                "payer": payer,
                "tax_year": row.get("tax_year", ""),
                "amount": box_amount,
                "discrepancy": check_a["discrepancy"],
                "action": action,
                "exhibit_number": exhibit_no,
            })

    for payer in find_gap_type_b(matrix_rows, statement_rows, transcript_rows):
        gaps_by_type["B"].append({
            "payer": payer,
            "tax_year": "",
            "amount": "",
            "discrepancy": None,
            "action": (
                "Investigate -- broker may have failed to report (IRC §6045 violation) "
                "or account may have been improperly closed before reporting"
            ),
            "exhibit_number": "",
        })

    with summary_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "tax_year", "payer", "form", "amount", "delta_match",
            "statement_match", "gap_type", "exhibit_number", "priority",
        ])
        writer.writeheader()
        writer.writerows(summary_rows)

    gap_labels = {
        "A": "Gap Type A -- Filer in Transcript, NOT in SFH Records",
        "B": "Gap Type B -- Known Account, NO 1099 Filed",
        "C": "Gap Type C -- 1099 Amount vs. Delta CSV Discrepancy",
        "D": "Gap Type D -- Disputed Account Filed a 1099",
    }
    lines = ["# Gap Analysis", ""]
    for code in ["A", "B", "C", "D"]:
        entries = gaps_by_type.get(code, [])
        lines.append(f"## {gap_labels[code]} ({len(entries)})")
        if not entries:
            lines.append("_None found._")
        for e in entries:
            detail = f"- **{e['payer']}**"
            if e["tax_year"]:
                detail += f" ({e['tax_year']})"
            if e["amount"]:
                detail += f" -- amount: {e['amount']}"
            if e["discrepancy"] not in (None, ""):
                detail += f" -- discrepancy: {e['discrepancy']}"
            if e["exhibit_number"]:
                detail += f" -- Exhibit {e['exhibit_number']}"
            detail += f"\n  - Action: {e['action']}"
            lines.append(detail)
        lines.append("")

    lines.append("## Priority Flags Hit")
    if priority_hits:
        for payer, note in priority_hits:
            lines.append(f"- **{payer}**: {note}")
    else:
        lines.append("_None found._")
    lines.append("")

    gap_report_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"Wrote {summary_path} ({len(summary_rows)} rows)")
    print(f"Wrote {gap_report_path}")


def main():
    parser = argparse.ArgumentParser(description="SFH IRS Transcript Analysis Framework cross-reference engine")
    parser.add_argument(
        "--data-dir", default=str(ROOT / "data"),
        help="Directory containing transcripts/, delta/, broker_statements/, counterparty_matrix.csv",
    )
    parser.add_argument(
        "--output-dir", default=str(ROOT / "outputs"),
        help="Directory to write summary_table.csv and gap_analysis.md",
    )
    args = parser.parse_args()
    run(Path(args.data_dir), Path(args.output_dir))


if __name__ == "__main__":
    main()
