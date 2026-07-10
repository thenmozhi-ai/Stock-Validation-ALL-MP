# Multi-Marketplace Stock Validation

Streamlit app built from the spec: per-marketplace uploads (Product Master, SOH Report, Stock
Report) for Lazada, Shopee, TikTok, Zalora — SellerSKU matching, Status/Remark classification,
summary dashboard, filters, search, CSV/Excel export.

## ⚠️ Read this before using — assumptions made to resolve spec ambiguity

The original spec left two things underspecified. Here's exactly how they were resolved in
`validation_engine.py` (the only file that should ever encode validation rules):

1. **Where "Expected Stock" comes from.** The spec lists three input files (Product Master, SOH
   Report, Stock Report) but only ever compares "Expected Stock vs Marketplace Stock" — it never
   says which file Expected Stock comes from. This build treats the **SOH Report** as Expected
   Stock and the **Stock Report** as Marketplace Stock. Product Master is used only for the
   Overstock/Understock check.

2. **How `Status` and `Remark` relate.** The spec defines five Status values (MATCH, MISMATCH,
   SKU NOT FOUND, OVERSTOCK, UNDERSTOCK) and three Remark values (TRUE, IMPACT, UPDATE 0) but
   doesn't say how they combine into one row. This build treats them as two independent columns:
   - `Remark` = Expected Stock vs Marketplace Stock (TRUE / IMPACT / UPDATE 0)
   - `Status` = priority order: SKU NOT FOUND → OVERSTOCK/UNDERSTOCK (only if a Product Master
     file was uploaded) → MATCH/MISMATCH (Expected vs Marketplace)

If either assumption doesn't match what you actually meant, tell me the correct rule and I'll
change `validation_engine.py` only — `app.py` won't need to change.

## Column detection

Since exact export formats for "SOH Report" / "Stock Report" / "Product Master" weren't
provided, `validation_engine.py` uses **best-effort column detection**: it scans column names
for anything containing "sku", then "stock"/"quantity"/"expected stock", then
"name"/"title". It also tries header rows 0–3 in case there's a banner row above the real
header (common in marketplace exports).

This is the riskiest part of the whole app — untested against your real files. If detection
picks the wrong column, or your exports have quirks like Shopee's known `activePane` XML bug
or a fixed multi-row header, send me a real sample and I'll replace the generic detector with
a hard-coded reader for that source (same pattern used in the earlier
`stock_validation_project` build for Lazada/Shopee/TikTok/Zalora).

## Project structure

```
stock_validation_v2/
├── app.py                  # UI only — uploads, dashboard, filters, search, export
├── validation_engine.py     # ALL validation rules live here — the "don't change this" file
├── requirements.txt
└── README.md
```

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on GitHub + Streamlit Community Cloud

```bash
git init && git add . && git commit -m "Stock validation app"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

Then [share.streamlit.io](https://share.streamlit.io) → **New app** → select repo → main file
`app.py` → **Deploy**.

## Tested behaviour

Ran against synthetic data covering every branch (MATCH/TRUE, OVERSTOCK/IMPACT,
UNDERSTOCK/IMPACT, OVERSTOCK+UPDATE 0, SKU NOT FOUND) — all five Status values and all three
Remark values fire correctly. Not yet tested against real marketplace export files.
