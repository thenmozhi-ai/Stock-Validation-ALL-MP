"""
app.py — Multi-Marketplace Stock Validation

UI only. All validation rules live in validation_engine.py — per the
project's own rule ("do not change validation logic, only UI"), this file
should only ever touch layout/styling/interaction, never the math.
"""

import io

import pandas as pd
import streamlit as st

import validation_engine as ve

MARKETPLACES = ["Lazada", "Shopee", "TikTok", "Zalora"]

st.set_page_config(page_title="Stock Validation", page_icon="📦", layout="wide")

# ---------------------------------------------------------------------------
# STYLING
# ---------------------------------------------------------------------------
st.markdown("""
<style>
.metric-card {
    background: var(--background-color, rgba(127,127,127,0.06));
    border: 1px solid rgba(127,127,127,0.25);
    border-radius: 12px;
    padding: 16px 18px;
    text-align: center;
}
.metric-card .label { font-size: 0.8rem; opacity: 0.7; margin-bottom: 4px; }
.metric-card .value { font-size: 1.6rem; font-weight: 700; }
.status-MATCH { background:#C6EFCE; color:#375623; padding:2px 8px; border-radius:6px; font-weight:600; }
.status-MISMATCH { background:#FFC7CE; color:#9C0006; padding:2px 8px; border-radius:6px; font-weight:600; }
.status-SKUNOTFOUND { background:#D9D9D9; color:#595959; padding:2px 8px; border-radius:6px; font-weight:600; }
.status-OVERSTOCK { background:#FFE699; color:#7F6000; padding:2px 8px; border-radius:6px; font-weight:600; }
.status-UNDERSTOCK { background:#B4C7E7; color:#1F3864; padding:2px 8px; border-radius:6px; font-weight:600; }
</style>
""", unsafe_allow_html=True)

st.title("📦 Multi-Marketplace Stock Validation")
st.caption("Lazada · Shopee · TikTok · Zalora — upload Product Master, SOH Report, and Stock Report per marketplace.")

if "results" not in st.session_state:
    st.session_state["results"] = {}  # {marketplace: DataFrame}

# ---------------------------------------------------------------------------
# UPLOAD SECTIONS — one card per marketplace
# ---------------------------------------------------------------------------
uploads = {}  # {marketplace: {"master":..., "soh":..., "stock":...}}

tabs = st.tabs([f"🏬 {m}" for m in MARKETPLACES])
for tab, marketplace in zip(tabs, MARKETPLACES):
    with tab:
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("**Product Master**")
            master_file = st.file_uploader(
                "Product Master", type=["csv", "xlsx", "xls"],
                key=f"{marketplace}_master", label_visibility="collapsed",
            )
        with c2:
            st.markdown("**SOH Report** (Expected Stock)")
            soh_file = st.file_uploader(
                "SOH Report", type=["csv", "xlsx", "xls"],
                key=f"{marketplace}_soh", label_visibility="collapsed",
            )
        with c3:
            st.markdown("**Marketplace Stock Report**")
            stock_file = st.file_uploader(
                "Stock Report", type=["csv", "xlsx", "xls"],
                key=f"{marketplace}_stock", label_visibility="collapsed",
            )
        uploads[marketplace] = {"master": master_file, "soh": soh_file, "stock": stock_file}

        ready = soh_file is not None and stock_file is not None
        if soh_file and not stock_file:
            st.info("SOH Report uploaded — also add the Stock Report to validate this marketplace.")
        elif stock_file and not soh_file:
            st.info("Stock Report uploaded — also add the SOH Report to validate this marketplace.")
        if master_file is None and (soh_file or stock_file):
            st.caption("No Product Master uploaded — Overstock/Understock checks will be skipped for this marketplace; Status will fall back to MATCH/MISMATCH.")

st.divider()

run = st.button("🚀 Run Validation", type="primary", use_container_width=True)

# ---------------------------------------------------------------------------
# PROCESSING
# ---------------------------------------------------------------------------
if run:
    progress = st.progress(0.0, text="Starting validation...")
    active = [m for m in MARKETPLACES if uploads[m]["soh"] and uploads[m]["stock"]]

    if not active:
        st.warning("No marketplace has both a SOH Report and a Stock Report uploaded yet.")
    else:
        results = {}
        errors = []
        for i, marketplace in enumerate(active):
            progress.progress((i) / len(active), text=f"Validating {marketplace}...")
            files = uploads[marketplace]
            try:
                soh_df = ve.read_table(files["soh"].getvalue(), files["soh"].name)
                stock_df = ve.read_table(files["stock"].getvalue(), files["stock"].name)
                soh_norm = ve.normalize_sku_qty_name(soh_df, f"{marketplace} SOH Report")
                stock_norm = ve.normalize_sku_qty_name(stock_df, f"{marketplace} Stock Report")

                master_norm = None
                if files["master"] is not None:
                    master_df = ve.read_table(files["master"].getvalue(), files["master"].name)
                    master_norm = ve.normalize_sku_qty_name(master_df, f"{marketplace} Product Master")

                out = ve.validate_marketplace(marketplace, soh_norm, stock_norm, master_norm)
                results[marketplace] = out
            except ValueError as e:
                errors.append(f"{marketplace}: {e}")
            progress.progress((i + 1) / len(active), text=f"{marketplace} done")

        progress.empty()
        st.session_state["results"] = results
        if errors:
            for e in errors:
                st.error(e)
        if results:
            st.success(f"Validated {len(results)} marketplace(s).")

# ---------------------------------------------------------------------------
# DASHBOARD + TABLE
# ---------------------------------------------------------------------------
results = st.session_state["results"]

if results:
    combined = pd.concat(results.values(), ignore_index=True)
    summary = ve.summarize(combined)

    st.subheader("📊 Summary")
    cards = st.columns(7)
    card_defs = [
        ("Total SKU", summary["Total SKU"]),
        ("Matched", summary["Matched"]),
        ("Mismatched", summary["Mismatched"]),
        ("SKU Not Found", summary["SKU Not Found"]),
        ("Overstock", summary["Overstock"]),
        ("Understock", summary["Understock"]),
        ("Match %", f"{summary['Match %']}%"),
    ]
    for col, (label, value) in zip(cards, card_defs):
        with col:
            st.markdown(f"""<div class="metric-card"><div class="label">{label}</div><div class="value">{value}</div></div>""", unsafe_allow_html=True)

    st.write("")

    # ---- filters + search ----
    fcol1, fcol2, fcol3 = st.columns([2, 2, 3])
    with fcol1:
        status_filter = st.selectbox(
            "Filter by status",
            ["All", "MATCH", "MISMATCH", "SKU NOT FOUND", "OVERSTOCK", "UNDERSTOCK"],
        )
    with fcol2:
        marketplace_filter = st.selectbox("Filter by marketplace", ["All"] + list(results.keys()))
    with fcol3:
        search_term = st.text_input("🔍 Search by SellerSKU or Product Name", "")

    filtered = combined.copy()
    if status_filter != "All":
        filtered = filtered[filtered["Status"] == status_filter]
    if marketplace_filter != "All":
        filtered = filtered[filtered["Marketplace"] == marketplace_filter]
    if search_term.strip():
        term = search_term.strip().lower()
        filtered = filtered[
            filtered["SellerSKU"].astype(str).str.lower().str.contains(term)
            | filtered["Product Name"].astype(str).str.lower().str.contains(term)
        ]

    st.subheader(f"📋 Results ({len(filtered)} of {len(combined)} rows)")
    st.dataframe(filtered, use_container_width=True, height=460)

    # ---- export ----
    st.subheader("⬇️ Export")
    ecol1, ecol2 = st.columns(2)
    with ecol1:
        csv_bytes = filtered.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download filtered results as CSV",
            data=csv_bytes,
            file_name="stock_validation_results.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with ecol2:
        excel_buf = io.BytesIO()
        with pd.ExcelWriter(excel_buf, engine="openpyxl") as writer:
            combined.to_excel(writer, index=False, sheet_name="All Results")
            for marketplace, df in results.items():
                df.to_excel(writer, index=False, sheet_name=marketplace[:31])
        st.download_button(
            "Download full workbook as Excel (.xlsx)",
            data=excel_buf.getvalue(),
            file_name="Stock_Validation_Results.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
else:
    st.info("Upload files above and click **Run Validation** to see results here.")
