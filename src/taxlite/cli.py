"""CLI entry point for TaxLite."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

import anthropic

from .excel import generate_excel
from .scanner import ReceiptData, find_receipt_files, scan_receipt
from .vendors import Vendor, VendorDB


def main():
    parser = argparse.ArgumentParser(
        prog="taxlite",
        description="Scan receipt images and generate a tax reimbursement Excel file.",
    )
    parser.add_argument("folder", type=Path, help="Folder containing receipt images/PDFs")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output Excel file path")
    parser.add_argument("--month", type=str, default=None, help="Month for the report (YYYY-MM)")
    parser.add_argument(
        "--vendor-db",
        type=Path,
        default=Path("vendors.json"),
        help="Path to vendor database JSON (default: vendors.json)",
    )
    args = parser.parse_args()

    # Validate folder
    if not args.folder.is_dir():
        print(f"Error: '{args.folder}' is not a directory.", file=sys.stderr)
        sys.exit(1)

    # Find receipt files
    files = find_receipt_files(args.folder)
    if not files:
        print(f"No receipt images found in '{args.folder}'.", file=sys.stderr)
        print("Supported formats: JPG, PNG, WEBP, GIF, PDF", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(files)} receipt(s) in '{args.folder}'")

    # Parse month
    month = None
    if args.month:
        try:
            month = datetime.strptime(args.month, "%Y-%m")
        except ValueError:
            print(f"Error: Invalid month format '{args.month}'. Use YYYY-MM.", file=sys.stderr)
            sys.exit(1)

    # Output path
    output = args.output
    if output is None:
        if month:
            output = Path(f"reimbursement_{month.strftime('%Y_%m')}.xlsx")
        else:
            output = Path(f"reimbursement_{datetime.now().strftime('%Y_%m_%d')}.xlsx")

    # Load .env file if present (check CWD and project root)
    for env_candidate in [Path.cwd() / ".env", Path(__file__).resolve().parent.parent.parent / ".env"]:
        if env_candidate.exists():
            with open(env_candidate) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, value = line.partition("=")
                        key, value = key.strip(), value.strip()
                        if value and not os.environ.get(key):
                            os.environ[key] = value
            break

    # Check API key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not found.", file=sys.stderr)
        print("Either set the env var or create a .env file in the project root.", file=sys.stderr)
        sys.exit(1)

    # Initialize
    client = anthropic.Anthropic(api_key=api_key)
    vendor_db = VendorDB(str(args.vendor_db))
    print(f"Loaded {len(vendor_db.vendors)} vendors from database")

    # Process receipts
    results: list[ReceiptData] = []
    errors: list[tuple[Path, str]] = []
    new_vendors: list[str] = []

    for i, filepath in enumerate(files, 1):
        print(f"\n[{i}/{len(files)}] Scanning {filepath.name}...", end=" ", flush=True)
        try:
            receipt = scan_receipt(client, filepath)
            print(f"OK - {receipt.vendor_name} | PHP {receipt.total_amount:,.2f}")

            # Cross-reference vendor DB
            match = vendor_db.lookup(receipt.vendor_name)
            if match:
                # Use DB values for TIN, address, and category
                if match.tin and not receipt.tin:
                    receipt.tin = match.tin
                elif match.tin:
                    receipt.tin = match.tin  # Prefer DB value for consistency
                if match.address:
                    receipt.address = match.address
                if match.category:
                    receipt.items_description = match.category
            else:
                # New vendor — add to DB if we have enough info
                new_vendors.append(receipt.vendor_name)
                if receipt.vendor_name and receipt.tin:
                    vendor_db.add(Vendor(
                        name=receipt.vendor_name,
                        tin=receipt.tin,
                        address=receipt.address,
                        category=receipt.items_description,
                    ))

            results.append(receipt)

        except Exception as e:
            print(f"FAILED - {e}")
            errors.append((filepath, str(e)))

    # Generate Excel
    if results:
        print(f"\nGenerating Excel: {output}")
        generate_excel(results, output, month)
        print(f"Saved {len(results)} receipt(s) to {output}")
    else:
        print("\nNo receipts were successfully processed.", file=sys.stderr)
        sys.exit(1)

    # Summary
    total_amount = sum(r.total_amount for r in results)
    total_vat = sum(r.vat_amount for r in results)
    print(f"\n{'='*50}")
    print(f"  Receipts processed: {len(results)}/{len(files)}")
    print(f"  Total amount:       PHP {total_amount:,.2f}")
    print(f"  Total VAT:          PHP {total_vat:,.2f}")
    if new_vendors:
        print(f"  New vendors added:  {len(new_vendors)}")
        for v in new_vendors:
            print(f"    - {v}")
    if errors:
        print(f"  Errors:             {len(errors)}")
        for path, err in errors:
            print(f"    - {path.name}: {err}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
