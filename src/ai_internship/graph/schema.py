"""Authoritative P0 labels and relationship directions."""

from __future__ import annotations

import hashlib

EDGE_TYPE_MAP = {
    ("Vendor", "Product"): {"Makes"},
    ("HealthSystem", "Product"): {"Adopts"},
    ("Product", "Product"): {"Integrates"},
    ("Product", "Category"): {"Addresses"},
    ("Vendor", "Category"): {"Addresses"},
}


def deterministic_id(*values: str) -> str:
    return hashlib.sha256("\0".join(values).encode()).hexdigest()
