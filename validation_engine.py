"""
validation_engine.py

Core validation logic for the Multi-Marketplace Stock Validation app.
THIS FILE IS THE SOURCE OF TRUTH FOR VALIDATION RULES — per the spec, once
finalized, only UI code (app.py) should change. Logic changes belong here
and only here, so a diff of this file always shows the full history of any
rule change.

------------------------------------------------------------------------
ASSUMPTIONS MADE TO RESOLVE AMBIGUITY IN THE SPEC (flag if these are wrong):
------------------------------------------------------------------------
1. "Expected Stock" comes from the Marketplace SOH Report (what the system
   expects to be live). "Marketplace Stock" comes from the Marketplace
   Stock Report (the live export from the platform). "Product Master" is a
   separate reference catalogue used only for the Overstock/Understock
   check, not for Expected Stock.
2. `Status` and `Remark` are two independent fields:
   - `Remark` = result of comparing Expected Stock vs Marketplace Stock
     (TRUE / IMPACT / UPDATE 0) — always computed when the SKU is found.
   - `Status` = a priority-ordered classification:
       1. SKU NOT FOUND  (SKU missing from the Stock Report)
       2. OVERSTOCK      (Marketplace Stock > Product Master qty, if a
                          Product Master file was supplied)
       3. UNDERSTOCK      (Marketplace Stock < Product Master qty)
       4. MATCH           (Expected Stock == Marketplace Stock, no Product
                          Master file supplied, or Marketplace Stock ==
                          Product Master qty)
       5. MISMATCH        (otherwise)
   If no Product Master file is uploaded for a marketplace, Status falls
   back to MATCH/MISMATCH based on Expected vs Marketplace Stock alone.
3. Column detection is flexible/best-effort (looks for column names
   containing "sku", "stock"/"quantity", "name"/"title") since exact
   per-marketplace export formats weren't provided for this spec. If your
   real files have known quirks (like Shopee's activePane bug, or a fixed
   header-skip pattern), tell me and I'll hard-code the reader instead of
   guessing — this is the single riskiest part of an auto-detect approach.
------------------------------------------------------------------------
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# GENERIC FILE READING (best-effort column detection)
# ---------------------------------------------------------------------------

SKU_HINTS = ("sellersku", "seller_sku", "seller sku", "sku")
QTY_HINTS = ("expected stock", "expected_stock", "soh", "quantity", "stock", "qty")
NAME_HINTS = ("product name", "product_name", "item title", "item name", "name", "title")


def _find_col(columns, hints):
    cols_lower = {c: str(c).strip().lower() for c in columns}
    # exact/substring match, longest hint first so "expected stock" wins over "stock"
    for hint in sorted(hints, key=len, reverse=True):
        for col, low in cols_lower.items():
            if hint in low:
                return col
    return None


def read_table(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """Reads csv/xlsx into a DataFrame, trying a few header rows if the
    first one doesn't look like real headers (common with marketplace
    exports that have banner/title rows before the real header)."""
    is_csv = filename.lower().endswith(".csv")

    def _try_header(h):
        buf = io.BytesIO(file_bytes)
        if is_csv:
            return pd.read_csv(buf, header=h, low_memory=False)
        return pd.read_excel(buf, header=h)

    for header_row in (0, 1, 2, 3):
        try:
            df = _try_header(header_row)
        except Exception:
            continue
        if _find_col(df.columns, SKU_HINTS) is not None:
            return df
    # fall back to header=0 even if no SKU column was detected, so caller
    # can surface a clear "couldn't find SKU column" error
    return _try_header(0)


def normalize_sku_qty_name(df: pd.DataFrame, source_label: str) -> dict:
    """Extracts SellerSKU / Quantity / Product Name from a loosely-shaped
    DataFrame. Returns a dict with the detected column names and a clean
    lookup DataFrame, or raises ValueError with a clear message."""
    sku_col = _find_col(df.columns, SKU_HINTS)
    qty_col = _find_col(df.columns, QTY_HINTS)
    name_col = _find_col(df.columns, NAME_HINTS)

    if sku_col is None:
        raise ValueError(f"Couldn't find a SellerSKU-like column in {source_label}. "
                          f"Columns seen: {list(df.columns)}")
    if qty_col is None:
        raise ValueError(f"Couldn't find a Quantity/Stock-like column in {source_label}. "
                          f"Columns seen: {list(df.columns)}")

    clean = df[[sku_col, qty_col] + ([name_col] if name_col else [])].copy()
    rename = {sku_col: "SellerSKU", qty_col: "Quantity"}
    if name_col:
        rename[name_col] = "Product Name"
    clean.rename(columns=rename, inplace=True)
    if "Product Name" not in clean.columns:
        clean["Product Name"] = None

    clean["SellerSKU"] = clean["SellerSKU"].astype(str).str.strip()
    clean["Quantity"] = pd.to_numeric(clean["Quantity"], errors="coerce").fillna(0).astype(int)
    clean = clean.dropna(subset=["SellerSKU"])
    clean = clean[clean["SellerSKU"] != "nan"]

    # collapse duplicates, keep first non-null Product Name
    agg = clean.groupby("SellerSKU").agg(
        Quantity=("Quantity", "sum"),
        **{"Product Name": ("Product Name", "first")},
    ).reset_index()

    return {
        "df": agg,
        "sku_col": sku_col,
        "qty_col": qty_col,
        "name_col": name_col,
        "lookup": agg.set_index("SellerSKU")["Quantity"].to_dict(),
        "name_lookup": agg.set_index("SellerSKU")["Product Name"].to_dict(),
    }


# ---------------------------------------------------------------------------
# VALIDATION LOGIC (the actual rules from the spec)
# ---------------------------------------------------------------------------

STATUS_VALUES = ("SKU NOT FOUND", "OVERSTOCK", "UNDERSTOCK", "MATCH", "MISMATCH")
REMARK_VALUES = ("TRUE", "IMPACT", "UPDATE 0", "—")


def compute_remark(expected: float, marketplace_stock) -> str:
    if pd.isna(marketplace_stock):
        return "—"
    exp = int(expected)
    mp = int(marketplace_stock)
    if exp == 0 and mp > 0:
        return "UPDATE 0"
    if exp != mp:
        return "IMPACT"
    return "TRUE"


def compute_status(expected: float, marketplace_stock, master_qty) -> str:
    if pd.isna(marketplace_stock):
        return "SKU NOT FOUND"
    mp = int(marketplace_stock)
    if master_qty is not None and not pd.isna(master_qty):
        master_qty = int(master_qty)
        if mp > master_qty:
            return "OVERSTOCK"
        if mp < master_qty:
            return "UNDERSTOCK"
        # mp == master_qty: fall through to expected-vs-marketplace check
    exp = int(expected)
    return "MATCH" if exp == mp else "MISMATCH"


def validate_marketplace(
    marketplace_name: str,
    soh_norm: dict,
    stock_norm: dict,
    master_norm: dict | None = None,
) -> pd.DataFrame:
    """
    soh_norm / stock_norm: output of normalize_sku_qty_name() for the SOH
        report and Stock report respectively (Expected Stock and
        Marketplace Stock sources).
    master_norm: optional, output of normalize_sku_qty_name() for the
        Product Master (used only for Overstock/Understock).

    Universe of SKUs = union of SOH report SKUs and Stock report SKUs
    (so both "expected but missing from marketplace" and "live on
    marketplace but not expected" surface as rows).
    """
    soh_df = soh_norm["df"].rename(columns={"Quantity": "Expected Stock"})
    stock_lookup = stock_norm["lookup"]
    name_lookup = {**stock_norm.get("name_lookup", {}), **soh_norm.get("name_lookup", {})}
    master_lookup = master_norm["lookup"] if master_norm else {}

    all_skus = set(soh_df["SellerSKU"]) | set(stock_lookup.keys())
    rows = []
    for sku in sorted(all_skus):
        expected = int(soh_df.set_index("SellerSKU")["Expected Stock"].get(sku, 0)) if sku in set(soh_df["SellerSKU"]) else 0
        mp_stock = stock_lookup.get(sku, np.nan)
        master_qty = master_lookup.get(sku, np.nan) if master_lookup else np.nan
        product_name = name_lookup.get(sku)

        remark = compute_remark(expected, mp_stock)
        status = compute_status(expected, mp_stock, master_qty if master_lookup else None)
        difference = (mp_stock - expected) if not pd.isna(mp_stock) else np.nan

        rows.append({
            "SellerSKU": sku,
            "Product Name": product_name,
            "Expected Stock": expected,
            "Marketplace Stock": mp_stock,
            "Difference": difference,
            "Status": status,
            "Remark": remark,
            "Marketplace": marketplace_name,
        })

    return pd.DataFrame(rows, columns=[
        "SellerSKU", "Product Name", "Expected Stock", "Marketplace Stock",
        "Difference", "Status", "Remark", "Marketplace",
    ])


def summarize(df: pd.DataFrame) -> dict:
    total = len(df)
    matched = int((df["Status"] == "MATCH").sum())
    mismatched = int((df["Status"] == "MISMATCH").sum())
    not_found = int((df["Status"] == "SKU NOT FOUND").sum())
    overstock = int((df["Status"] == "OVERSTOCK").sum())
    understock = int((df["Status"] == "UNDERSTOCK").sum())
    match_pct = round(matched / total * 100, 2) if total else 0.0
    return {
        "Total SKU": total,
        "Matched": matched,
        "Mismatched": mismatched,
        "SKU Not Found": not_found,
        "Overstock": overstock,
        "Understock": understock,
        "Match %": match_pct,
    }
