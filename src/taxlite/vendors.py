"""Vendor database management for TaxLite."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class Vendor:
    name: str
    tin: str
    address: str
    category: str


def _normalize(name: str) -> str:
    """Normalize a vendor name for fuzzy matching."""
    s = name.lower().strip()
    s = re.sub(r"[.,;:'\"\-!@#$%^&*()]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Remove common suffixes
    for suffix in ["inc", "corp", "corporation", "incorporated", "co", "company", "llc"]:
        s = re.sub(rf"\b{suffix}\b\.?$", "", s).strip()
    return s


class VendorDB:
    """Manages a JSON-backed vendor database with fuzzy matching."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.vendors: list[Vendor] = []
        self._index: dict[str, int] = {}  # normalized name -> index
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
            self._index[_normalize(v.name)] = len(self.vendors)
            self.vendors.append(v)

    def save(self):
        data = [
            {"name": v.name, "tin": v.tin, "address": v.address, "category": v.category}
            for v in self.vendors
        ]
        with open(self.db_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def lookup(self, name: str) -> Optional[Vendor]:
        """Find a vendor by exact or fuzzy name match."""
        norm = _normalize(name)
        # Exact normalized match
        if norm in self._index:
            return self.vendors[self._index[norm]]
        # Substring match (either direction)
        for key, idx in self._index.items():
            if norm in key or key in norm:
                return self.vendors[idx]
        return None

    def add(self, vendor: Vendor):
        """Add a new vendor to the database."""
        norm = _normalize(vendor.name)
        if norm not in self._index:
            self._index[norm] = len(self.vendors)
            self.vendors.append(vendor)
            self.save()
