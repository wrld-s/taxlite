"""Vendor database management for TaxLite."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Optional


# WRLD Capital Holdings TIN — used to detect when OCR grabs the buyer's TIN
BUYER_TIN = "009-780-884"


@dataclass
class Vendor:
    name: str
    tin: str
    address: str
    category: str


@dataclass
class MatchResult:
    """Result of a vendor DB lookup with confidence scoring."""

    vendor: Optional[Vendor]
    confidence: str  # "high", "medium", "low", "new"
    notes: str  # Human-readable explanation


# ---------------------------------------------------------------------------
# TIN utilities
# ---------------------------------------------------------------------------

# Common OCR misreads for digits
_OCR_DIGIT_MAP = str.maketrans("OoIlBSZG", "00118526")


def normalize_tin(raw: str) -> str:
    """Normalize a TIN string: fix OCR errors, strip noise, format as digits-only."""
    if not raw:
        return ""
    # Fix common OCR letter→digit mistakes
    s = raw.translate(_OCR_DIGIT_MAP)
    # Keep only digits
    digits = re.sub(r"[^0-9]", "", s)
    return digits


def format_tin(digits: str) -> str:
    """Format a digit-only TIN back to XXX-XXX-XXX-XXXXX display format."""
    if len(digits) >= 12:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:9]}-{digits[9:]}"
    elif len(digits) >= 9:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:9]}"
    return digits


def is_valid_tin(digits: str) -> bool:
    """Check if a TIN has a valid digit count (9 base + optional branch code)."""
    return len(digits) >= 9 and len(digits) <= 14 and not all(c == "0" for c in digits)


def tin_distance(a: str, b: str) -> int:
    """Count digit differences between two TINs (Hamming-style on the shorter length).

    Returns a high number if lengths are wildly different.
    """
    if not a or not b:
        return 99
    # Compare up to the shorter length
    min_len = min(len(a), len(b))
    diff = sum(1 for i in range(min_len) if a[i] != b[i])
    # Penalise length mismatch beyond the 9 base digits
    diff += abs(len(a) - len(b)) if abs(len(a) - len(b)) > 3 else 0
    return diff


def is_buyer_tin(digits: str) -> bool:
    """Check if this TIN belongs to the buyer (WRLD Capital Holdings)."""
    buyer_digits = normalize_tin(BUYER_TIN)
    # Match on the 9-digit base (ignore branch code)
    return digits[:9] == buyer_digits[:9] if len(digits) >= 9 else False


# ---------------------------------------------------------------------------
# Vendor name utilities
# ---------------------------------------------------------------------------

def _normalize(name: str) -> str:
    """Normalize a vendor name for fuzzy matching."""
    s = name.lower().strip()
    s = re.sub(r"[.,;:'\"\-!@#$%^&*()]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Remove common suffixes
    for suffix in ["inc", "corp", "corporation", "incorporated", "co", "company", "llc"]:
        s = re.sub(rf"\b{suffix}\b\.?$", "", s).strip()
    return s


def _name_similarity(a: str, b: str) -> float:
    """Simple similarity score between two normalized names (0.0 to 1.0)."""
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    # Exact match
    if na == nb:
        return 1.0
    # Substring match (either direction)
    if na in nb or nb in na:
        return 0.85
    # Word overlap (Jaccard-ish)
    words_a = set(na.split())
    words_b = set(nb.split())
    if not words_a or not words_b:
        return 0.0
    overlap = len(words_a & words_b)
    union = len(words_a | words_b)
    return overlap / union if union else 0.0


# ---------------------------------------------------------------------------
# VendorDB class
# ---------------------------------------------------------------------------

class VendorDB:
    """Manages a JSON-backed vendor database with TIN-aware fuzzy matching."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.vendors: list[Vendor] = []
        self._name_index: dict[str, int] = {}  # normalized name -> index
        self._tin_index: dict[str, int] = {}  # normalized TIN digits -> index
        self._load()

    def _load(self):
        if not os.path.exists(self.db_path):
            return
        with open(self.db_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for item in data:
            v = Vendor(
                name=item["name"],
                tin=item.get("tin", ""),
                address=item.get("address", ""),
                category=item.get("category", ""),
            )
            self._name_index[_normalize(v.name)] = len(self.vendors)
            tin_digits = normalize_tin(v.tin)
            if tin_digits:
                self._tin_index[tin_digits] = len(self.vendors)
            self.vendors.append(v)

    def save(self):
        data = [
            {"name": v.name, "tin": v.tin, "address": v.address, "category": v.category}
            for v in self.vendors
        ]
        with open(self.db_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Legacy simple lookup (kept for backward compatibility with CLI)
    # ------------------------------------------------------------------
    def lookup(self, name: str) -> Optional[Vendor]:
        """Find a vendor by exact or fuzzy name match."""
        norm = _normalize(name)
        if norm in self._name_index:
            return self.vendors[self._name_index[norm]]
        for key, idx in self._name_index.items():
            if norm in key or key in norm:
                return self.vendors[idx]
        return None

    # ------------------------------------------------------------------
    # New: full confidence-scored matching pipeline
    # ------------------------------------------------------------------
    def lookup_by_tin(self, tin_digits: str) -> Optional[Vendor]:
        """Exact TIN match."""
        if tin_digits in self._tin_index:
            return self.vendors[self._tin_index[tin_digits]]
        return None

    def fuzzy_tin_matches(self, tin_digits: str, max_distance: int = 2) -> list[tuple[Vendor, int]]:
        """Find vendors whose TIN is within `max_distance` digit differences."""
        if not tin_digits:
            return []
        matches = []
        for db_tin, idx in self._tin_index.items():
            dist = tin_distance(tin_digits, db_tin)
            if dist <= max_distance and dist > 0:
                matches.append((self.vendors[idx], dist))
        matches.sort(key=lambda x: x[1])
        return matches

    def match_receipt(
        self,
        ocr_tin: str,
        ocr_vendor_name: str,
        ocr_brand_name: Optional[str] = None,
    ) -> MatchResult:
        """Full matching pipeline with confidence scoring.

        Steps:
        1. Normalize TIN, reject if it's the buyer's TIN
        2. Exact TIN match → cross-check name → high/medium confidence
        3. Fuzzy TIN match (1-2 digit OCR errors) → cross-check name
        4. Name-only match (TIN may be garbled) → use DB TIN
        5. No match → new vendor
        """
        tin_digits = normalize_tin(ocr_tin)
        names_to_try = [n for n in [ocr_vendor_name, ocr_brand_name] if n]

        # --- Step 1: Reject buyer TIN ---
        if tin_digits and is_buyer_tin(tin_digits):
            tin_digits = ""  # Discard; OCR picked up the buyer's TIN

        # --- Step 2: Exact TIN match ---
        if tin_digits:
            exact = self.lookup_by_tin(tin_digits)
            if exact:
                name_sim = max(_name_similarity(n, exact.name) for n in names_to_try) if names_to_try else 0.0
                if name_sim >= 0.5:
                    return MatchResult(
                        vendor=exact,
                        confidence="high",
                        notes=f"TIN exact match + name similarity {name_sim:.0%}",
                    )
                else:
                    return MatchResult(
                        vendor=exact,
                        confidence="medium",
                        notes=f"TIN exact match but name similarity low ({name_sim:.0%}). "
                              f"OCR: '{ocr_vendor_name}' vs DB: '{exact.name}'",
                    )

        # --- Step 3: Fuzzy TIN match (1-2 digit OCR errors) ---
        if tin_digits and is_valid_tin(tin_digits):
            fuzzy = self.fuzzy_tin_matches(tin_digits, max_distance=2)
            for vendor, dist in fuzzy:
                name_sim = max(_name_similarity(n, vendor.name) for n in names_to_try) if names_to_try else 0.0
                if name_sim >= 0.4:
                    return MatchResult(
                        vendor=vendor,
                        confidence="medium",
                        notes=f"TIN fuzzy match ({dist} digit diff) + name similarity {name_sim:.0%}",
                    )

        # --- Step 4: Name-only match ---
        best_vendor = None
        best_sim = 0.0
        for name in names_to_try:
            for key, idx in self._name_index.items():
                v = self.vendors[idx]
                sim = _name_similarity(name, v.name)
                if sim > best_sim:
                    best_sim = sim
                    best_vendor = v
        if best_vendor and best_sim >= 0.5:
            conf = "high" if best_sim >= 0.85 else "medium"
            return MatchResult(
                vendor=best_vendor,
                confidence=conf,
                notes=f"Name match ({best_sim:.0%}), using DB TIN '{best_vendor.tin}'",
            )

        # --- Step 5: No match → new vendor ---
        return MatchResult(
            vendor=None,
            confidence="new",
            notes="No match in vendor DB — new vendor",
        )

    def add(self, vendor: Vendor):
        """Add a new vendor to the database."""
        norm = _normalize(vendor.name)
        if norm not in self._name_index:
            self._name_index[norm] = len(self.vendors)
            tin_digits = normalize_tin(vendor.tin)
            if tin_digits:
                self._tin_index[tin_digits] = len(self.vendors)
            self.vendors.append(vendor)
            self.save()
