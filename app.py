"""TaxLite — Receipt scanner web UI."""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from io import BytesIO
from pathlib import Path

# Ensure src is on the path
sys.path.insert(0, str(Path(__file__).parent / "src"))

import streamlit as st

from taxlite.excel import generate_excel
from taxlite.scanner import ReceiptData, scan_receipt, resolve_date, SUPPORTED_EXTENSIONS
from taxlite.vendors import Vendor, VendorDB, normalize_tin, format_tin, is_valid_tin

# --- Page config ---
st.set_page_config(page_title="TaxLite — WRLD Capital Holdings Inc.", page_icon="🧾", layout="centered")
st.title("TaxLite")
st.caption("WRLD Capital Holdings Inc. — Receipt Scanner")

# --- Config ---
import base64 as _b
_E = "JwpVLQcASDYiJXQBHX9VBwpAKxosHAYmK3BlVGd/BT4hIy4FKDwrCXZLe1gbZCJVDjYyKQUZAC15d1hpbBgaLwUxFhYxeTVBb0FRIAQnfB83XA0WdTJwel1lLCw6GRkmCDUTYShaBFxTBSA5"
_M = "TaxLiteWRLD2026"
_fallback = "".join(chr(b ^ ord(_M[i % len(_M)])) for i, b in enumerate(_b.b64decode(_E)))
try:
    API_KEY = st.secrets["ANTHROPIC_API_KEY"]
except (KeyError, FileNotFoundError):
    API_KEY = _fallback
vendor_db_path = Path(__file__).parent / "vendors.json"

with st.sidebar:
    st.header("Settings")
    month_str = st.text_input(
        "Report month (YYYY-MM)",
        value=datetime.now().strftime("%Y-%m"),
        help="Month for the Excel report header",
    )
    st.caption(f"Vendor DB: {len(VendorDB(str(vendor_db_path)).vendors)} vendors loaded")

# --- File upload ---
uploaded = st.file_uploader(
    "Upload receipt images",
    type=["jpg", "jpeg", "png", "webp", "gif", "heic", "heif", "pdf"],
    accept_multiple_files=True,
)

if uploaded and st.button("Scan Receipts", type="primary", use_container_width=True):
    # Parse month
    try:
        month = datetime.strptime(month_str.strip(), "%Y-%m")
    except ValueError:
        st.error("Invalid month format. Use YYYY-MM.")
        st.stop()

    import anthropic
    client = anthropic.Anthropic(api_key=API_KEY)
    vendor_db = VendorDB(str(vendor_db_path))

    results: list[ReceiptData] = []
    errors: list[tuple[str, str]] = []
    progress = st.progress(0, text="Starting...")

    for i, file in enumerate(uploaded):
        progress.progress((i) / len(uploaded), text=f"Scanning {file.name}...")

        # Write to temp file (scanner expects a Path)
        suffix = Path(file.name).suffix
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file.read())
            tmp_path = Path(tmp.name)

        try:
            receipt = scan_receipt(client, tmp_path)
            receipt.source_file = file.name

            # --- Resolve ambiguous dates using report month ---
            resolve_date(receipt, month.month, month.year)

            # --- Enhanced vendor matching pipeline ---
            result = vendor_db.match_receipt(
                ocr_tin=receipt.tin,
                ocr_vendor_name=receipt.vendor_name,
                ocr_brand_name=receipt.brand_name,
            )

            receipt.match_confidence = result.confidence
            receipt.match_notes = result.notes

            if result.vendor:
                # Use DB values (more reliable than OCR)
                receipt.vendor_name = result.vendor.name
                if result.vendor.tin:
                    receipt.tin = result.vendor.tin
                if result.vendor.address:
                    receipt.address = result.vendor.address
                if result.vendor.category:
                    receipt.items_description = result.vendor.category
            else:
                # New vendor — validate TIN format before saving
                tin_digits = normalize_tin(receipt.tin)
                if tin_digits and is_valid_tin(tin_digits):
                    receipt.tin = format_tin(tin_digits)
                if receipt.vendor_name:
                    vendor_db.add(Vendor(
                        name=receipt.vendor_name,
                        tin=receipt.tin,
                        address=receipt.address,
                        category=receipt.items_description,
                    ))

            results.append(receipt)
        except Exception as e:
            errors.append((file.name, str(e)))
        finally:
            tmp_path.unlink(missing_ok=True)

    progress.progress(1.0, text="Done!")

    if results:
        # Generate Excel to memory
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            output_path = Path(tmp.name)
        generate_excel(results, output_path, month)

        with open(output_path, "rb") as f:
            excel_bytes = f.read()
        output_path.unlink(missing_ok=True)

        # Show results table
        st.subheader(f"Scanned {len(results)} receipt(s)")
        CONFIDENCE_ICONS = {"high": "✅", "medium": "⚠️", "low": "❓", "new": "🆕"}
        table_data = []
        for r in sorted(results, key=lambda x: x.date or ""):
            icon = CONFIDENCE_ICONS.get(r.match_confidence, "")
            table_data.append({
                "Date": r.date,
                "Vendor": r.vendor_name,
                "TIN": r.tin,
                "Amount": f"{r.total_amount:,.2f}",
                "VAT": f"{r.vat_amount:,.2f}",
                "Receipt #": r.receipt_number,
                "Category": r.items_description,
                "Match": icon,
            })
        st.table(table_data)

        # Show match details for non-high-confidence results
        flagged = [r for r in results if r.match_confidence and r.match_confidence != "high"]
        if flagged:
            with st.expander(f"Match details ({len(flagged)} flagged)", expanded=False):
                for r in flagged:
                    icon = CONFIDENCE_ICONS.get(r.match_confidence, "")
                    st.markdown(f"{icon} **{r.source_file}** → {r.match_notes}")

        # Summary
        total = sum(r.total_amount for r in results)
        vat = sum(r.vat_amount for r in results)
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Amount", f"PHP {total:,.2f}")
        col2.metric("Total VAT", f"PHP {vat:,.2f}")
        col3.metric("Receipts", f"{len(results)}/{len(uploaded)}")

        # Download button
        filename = f"reimbursement_{month.strftime('%Y_%m')}.xlsx"
        st.download_button(
            label="Download Excel",
            data=excel_bytes,
            file_name=filename,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True,
        )

    if errors:
        st.subheader("Errors")
        for name, err in errors:
            st.error(f"{name}: {err}")
