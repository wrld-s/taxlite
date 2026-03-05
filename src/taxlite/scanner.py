"""Receipt scanning via Claude Vision API."""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import anthropic

SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
SUPPORTED_HEIC_EXTENSIONS = {".heic", ".heif"}
SUPPORTED_PDF_EXTENSIONS = {".pdf"}
SUPPORTED_EXTENSIONS = SUPPORTED_IMAGE_EXTENSIONS | SUPPORTED_HEIC_EXTENSIONS | SUPPORTED_PDF_EXTENSIONS

MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

EXTRACTION_PROMPT = """\
You are an expert at reading Philippine BIR-registered Official Receipts (OR) and Sales Invoices (SI).

Analyze this receipt image and extract the following fields. Return ONLY a JSON object with these exact keys:

{
  "date": "YYYY-MM-DD",
  "date_raw": "The date exactly as printed on the receipt",
  "vendor_name": "The legal entity / registered business name",
  "brand_name": "The trade or brand name (if different from vendor_name)",
  "tin": "TIN in format XXX-XXX-XXX-XXXXX (taxpayer identification number of the vendor)",
  "total_amount": 0.00,
  "vat_amount": 0.00,
  "receipt_number": "The OR/SI/Invoice number",
  "address": "Municipality/City, Province",
  "items_description": "Category from: Meals, Grocery, Medicine, Gasoline, Home Improvement"
}

Important rules:
- "date" must be the transaction date in YYYY-MM-DD format. Try your best to interpret the date, but if the month/day are ambiguous (both ≤ 12), make your best guess
- "date_raw" must be the date EXACTLY as printed on the receipt, character for character (e.g., "03/05/2026", "MAR 05, 2026", "05-03-26"). Do not reformat this
- "vendor_name" PRIORITY ORDER — use the FIRST one you find:
  1. The entity after "Operated by", "A subsidiary of", "A franchise of", "Managed by", "A licensee of"
  2. The BIR-registered business name (usually the legal entity at the top, often ending in Inc., Corp., Co., etc.)
  3. The trade/brand name ONLY if no legal entity name is visible
- "brand_name" is the trade/brand name shown on the receipt (e.g., "Starbucks", "Jollibee"). If the brand name is the same as vendor_name, set to null
- "tin" is the VENDOR's TIN (the seller, NOT the buyer, NOT the POS provider). It is usually printed near the vendor's business name at the top of the receipt. Format: XXX-XXX-XXX-XXXXX. IMPORTANT: receipts often show MULTIPLE TINs — you must pick the VENDOR's:
  • VENDOR TIN: near the business name at the top of the receipt. THIS is the one you want.
  • BUYER TIN: appears near "Sold to", "Customer TIN", "Buyer's TIN". IGNORE this.
  • POS/CRM PROVIDER TIN: appears near "POS Provider", "POS by", "CRM Provider", "Accredited", "Software by", "System Provider", usually at the bottom of the receipt. IGNORE this.
- "total_amount" is the TOTAL amount paid (the final amount including VAT). Use the number after "TOTAL" or "AMOUNT DUE"
- "vat_amount" is the 12% VAT amount. Look for "VAT 12%", "VAT AMT", "VAT Amount", or "Output Tax". If the receipt shows "VATable Sales" instead, compute: vat_amount = vatable_sales * 0.12. If the receipt is VAT-exempt or non-VAT, set to 0.00
- "receipt_number" is the OR number, SI number, or Invoice number. Look for "OR No.", "SI No.", "Invoice No.", or similar
- "address" must be SHORT: just "Municipality/City, Province" (e.g., "Balanga City, Bataan", "Subic, Zambales"). Do NOT include street addresses, barangay, or unit numbers
- "items_description" must be exactly ONE of these categories: "Meals", "Grocery", "Medicine", "Gasoline", "Home Improvement". Pick the best fit based on what was purchased

If a field cannot be read or is not present, use null for strings and 0.0 for numbers.
Return ONLY the JSON object, no other text."""


@dataclass
class ReceiptData:
    date: Optional[str]
    vendor_name: str
    tin: str
    total_amount: float
    vat_amount: float
    receipt_number: str
    address: str
    items_description: str
    source_file: str
    date_raw: Optional[str] = None
    brand_name: Optional[str] = None
    match_confidence: Optional[str] = None  # "high", "medium", "low", "new"
    match_notes: Optional[str] = None  # Explanation of how the match was resolved


MAX_IMAGE_BYTES = 4_800_000  # Stay under Claude's 5MB limit


def _compress_to_jpeg(img, max_bytes: int = MAX_IMAGE_BYTES) -> bytes:
    """Compress a PIL Image to JPEG, resizing if needed to stay under max_bytes."""
    import io

    # Try at current size with decreasing quality
    for quality in [85, 70, 55]:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=quality)
        if buf.tell() <= max_bytes:
            return buf.getvalue()

    # Still too large — resize down
    width, height = img.size
    for scale in [0.75, 0.5, 0.35]:
        resized = img.resize((int(width * scale), int(height * scale)))
        buf = io.BytesIO()
        resized.convert("RGB").save(buf, format="JPEG", quality=70)
        if buf.tell() <= max_bytes:
            return buf.getvalue()

    # Last resort
    buf = io.BytesIO()
    img.resize((int(width * 0.25), int(height * 0.25))).convert("RGB").save(buf, format="JPEG", quality=60)
    return buf.getvalue()


def _image_to_base64(path: Path) -> tuple[str, str]:
    """Read an image file and return (base64_data, media_type). Resizes if too large."""
    import io

    from PIL import Image

    if path.suffix.lower() in SUPPORTED_HEIC_EXTENSIONS:
        from pillow_heif import register_heif_opener
        register_heif_opener()

    # Check if the raw file is small enough and in a native format
    if path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
        raw_size = path.stat().st_size
        if raw_size <= MAX_IMAGE_BYTES:
            media_type = MEDIA_TYPES.get(path.suffix.lower(), "image/jpeg")
            with open(path, "rb") as f:
                data = base64.standard_b64encode(f.read()).decode("utf-8")
            return data, media_type

    # For HEIC or oversized images, convert/compress to JPEG
    img = Image.open(path)
    jpeg_bytes = _compress_to_jpeg(img)
    b64 = base64.standard_b64encode(jpeg_bytes).decode("utf-8")
    return b64, "image/jpeg"


def _pdf_to_images(path: Path) -> list[tuple[str, str]]:
    """Convert PDF pages to base64 images."""
    import fitz  # PyMuPDF

    doc = fitz.open(str(path))
    images = []
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        img_data = pix.tobytes("png")
        b64 = base64.standard_b64encode(img_data).decode("utf-8")
        images.append((b64, "image/png"))
    doc.close()
    return images


def _parse_response(text: str) -> dict:
    """Extract JSON from Claude's response."""
    # Try to find JSON in the response
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group())
    return json.loads(text)


def scan_receipt(client: anthropic.Anthropic, image_path: Path) -> ReceiptData:
    """Scan a single receipt image and extract structured data."""
    content = []

    if image_path.suffix.lower() in SUPPORTED_PDF_EXTENSIONS:
        images = _pdf_to_images(image_path)
        for b64_data, media_type in images:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64_data},
            })
    else:
        b64_data, media_type = _image_to_base64(image_path)
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64_data},
        })

    content.append({"type": "text", "text": EXTRACTION_PROMPT})

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": content}],
    )

    raw = response.content[0].text
    data = _parse_response(raw)

    return ReceiptData(
        date=data.get("date"),
        vendor_name=data.get("vendor_name", ""),
        tin=data.get("tin", ""),
        total_amount=float(data.get("total_amount", 0) or 0),
        vat_amount=float(data.get("vat_amount", 0) or 0),
        receipt_number=str(data.get("receipt_number", "")),
        address=data.get("address", ""),
        items_description=data.get("items_description", ""),
        source_file=str(image_path.name),
        date_raw=data.get("date_raw"),
        brand_name=data.get("brand_name"),
    )


def resolve_date(receipt: ReceiptData, report_month: int, report_year: int) -> None:
    """Resolve ambiguous dates using the report month as a tiebreaker.

    If Claude returned a date whose month doesn't match the report month,
    and the raw date string is ambiguous (both parts ≤ 12), try swapping
    month and day to see if the other interpretation matches.
    """
    if not receipt.date or not receipt.date_raw:
        return

    try:
        from datetime import datetime
        parsed = datetime.strptime(receipt.date, "%Y-%m-%d")
    except ValueError:
        return

    # Already matches the report month — no action needed
    if parsed.month == report_month and parsed.year == report_year:
        return

    # Check if the date is ambiguous (day ≤ 12, so month/day could be swapped)
    if parsed.day > 12:
        return  # Unambiguous — day can't be a month

    # Try swapping month and day
    try:
        from datetime import datetime as dt
        swapped = parsed.replace(month=parsed.day, day=parsed.month)
        if swapped.month == report_month and swapped.year == report_year:
            receipt.date = swapped.strftime("%Y-%m-%d")
    except ValueError:
        pass  # Invalid date after swap (e.g., day 31 as month)


def find_receipt_files(folder: Path) -> list[Path]:
    """Find all supported image/PDF files in a folder."""
    files = []
    for f in sorted(folder.iterdir()):
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS:
            files.append(f)
    return files
