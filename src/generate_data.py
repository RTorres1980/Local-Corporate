"""Generate a year of ERP-style data for a retailer with a manufacturing hub.

Writes to data/:
  erp_sales_data.csv   daily units, price and cost by location and product
  inventory.csv        monthly ending inventory (units and cost) by product
  gl_entries.csv       monthly general ledger: revenue, COGS and operating expenses by location
"""
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parents[1] / "data"
LOCATIONS = ["SLC-Sugarhouse", "SLC-Downtown", "WVC-Manufacturing-Hub", "Murray-Retail"]
# product: (list price, unit cost as share of price, base daily units, seasonality amplitude)
PRODUCTS = {"Widget-A": (120, 0.45, 18, 0.25), "Widget-B": (180, 0.55, 12, 0.15),
            "Custom-Part-X": (420, 0.62, 4, 0.05), "Industrial-Sensor": (310, 0.50, 6, 0.10)}
OPEX = {"Rent": 9_000, "Payroll": 38_000, "Utilities": 2_500, "Marketing": 4_000}


def generate(seed=42, year=2025, out_dir=DATA):
    out_dir = Path(out_dir)
    rng = np.random.default_rng(seed)
    dates = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D")
    rows = []
    for d in dates:
        season = np.sin(2 * np.pi * (d.dayofyear - 80) / 365)  # peaks in summer
        weekend = 1.3 if d.dayofweek >= 5 else 1.0
        for loc in LOCATIONS:
            loc_scale = 1.6 if loc == "WVC-Manufacturing-Hub" else 1.0
            for prod, (price, cost_share, base, amp) in PRODUCTS.items():
                mu = base * loc_scale * (1 + amp * season) * (weekend if loc != "WVC-Manufacturing-Hub" else 1)
                units = int(rng.poisson(mu))
                if units == 0:
                    continue
                unit_price = round(price * rng.uniform(0.92, 1.0), 2)          # discounts off list
                unit_cost = round(price * cost_share * rng.uniform(0.97, 1.03), 2)
                rows.append([d, loc, prod, units, unit_price, unit_cost])
    sales = pd.DataFrame(rows, columns=["Date", "Location", "Product", "Units", "Price", "Unit_Cost"])
    sales["Total_Revenue"] = (sales["Units"] * sales["Price"]).round(2)
    sales["Total_Cost"] = (sales["Units"] * sales["Unit_Cost"]).round(2)
    sales["Gross_Margin"] = (sales["Total_Revenue"] - sales["Total_Cost"]).round(2)

    # Month-end inventory: roughly 3-8 weeks of cover, slower for the custom part.
    monthly_units = sales.groupby([sales["Date"].dt.to_period("M"), "Product"])["Units"].sum()
    inv = []
    for (month, prod), units in monthly_units.items():
        weeks = rng.uniform(8, 14) if prod == "Custom-Part-X" else rng.uniform(3, 6)
        on_hand = int(units / 4.33 * weeks)
        cost = PRODUCTS[prod][0] * PRODUCTS[prod][1]
        inv.append([month.to_timestamp("M").date(), prod, on_hand, round(on_hand * cost, 2)])
    inventory = pd.DataFrame(inv, columns=["Month_End", "Product", "Units_On_Hand", "Inventory_Cost"])

    # GL: revenue and COGS posted from sales; opex by location. One month has a posting error.
    m = sales.assign(Month=sales["Date"].dt.to_period("M").dt.to_timestamp("M").dt.date)
    gl = []
    for (month, loc), g in m.groupby(["Month", "Location"]):
        gl.append([month, loc, "4000", "Sales Revenue", 0.0, round(g["Total_Revenue"].sum(), 2)])
        gl.append([month, loc, "5000", "Cost of Goods Sold", round(g["Total_Cost"].sum(), 2), 0.0])
        for i, (name, amt) in enumerate(OPEX.items()):
            scale = 1.4 if loc == "WVC-Manufacturing-Hub" else 1.0
            gl.append([month, loc, str(6000 + 100 * i), name, round(amt * scale * rng.uniform(0.95, 1.05), 2), 0.0])
    gl = pd.DataFrame(gl, columns=["Month_End", "Location", "Account", "Account_Name", "Debit", "Credit"])
    err = gl.index[(gl["Account"] == "4000") & (gl["Location"] == "Murray-Retail")][5]
    gl.loc[err, "Credit"] = round(gl.loc[err, "Credit"] - 4_850.00, 2)     # revenue under-posted

    out_dir.mkdir(parents=True, exist_ok=True)
    sales.to_csv(out_dir / "erp_sales_data.csv", index=False)
    inventory.to_csv(out_dir / "inventory.csv", index=False)
    gl.to_csv(out_dir / "gl_entries.csv", index=False)
    return sales, inventory, gl


if __name__ == "__main__":
    s, i, g = generate()
    print(f"Wrote {len(s):,} sales rows, {len(i)} inventory snapshots and {len(g)} GL lines to {DATA}")
