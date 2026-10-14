"""Financial intelligence for the retail and manufacturing business.

  P&L by location and month, with gross and operating margin
  Subledger-to-GL reconciliation (sales system vs posted revenue and COGS)
  Inventory turnover, days on hand and GMROI by product
  12-month revenue forecast (last year's monthly pattern x a stated growth assumption)

Writes output/financial_intelligence.xlsx (one sheet per view, ready to import into Power BI or Excel)
and output/*.png charts.

Usage: python src/analysis.py
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA, OUT = ROOT / "data", ROOT / "output"
RECON_TOLERANCE = 1.00
GROWTH_ASSUMPTION = 0.04   # annual revenue growth used in the forecast


def load():
    sales = pd.read_csv(DATA / "erp_sales_data.csv", parse_dates=["Date"])
    inventory = pd.read_csv(DATA / "inventory.csv", parse_dates=["Month_End"])
    gl = pd.read_csv(DATA / "gl_entries.csv", parse_dates=["Month_End"], dtype={"Account": str})
    return sales, inventory, gl


def pnl(gl):
    """Income statement by location from the GL."""
    gl = gl.assign(Net=gl["Credit"] - gl["Debit"])
    rev = gl[gl["Account"] == "4000"].groupby("Location")["Net"].sum()
    cogs = -gl[gl["Account"] == "5000"].groupby("Location")["Net"].sum()
    opex = -gl[gl["Account"].str.startswith("6")].groupby("Location")["Net"].sum()
    p = pd.DataFrame({"Revenue": rev, "COGS": cogs})
    p["Gross_Profit"] = p["Revenue"] - p["COGS"]
    p["Gross_Margin_%"] = 100 * p["Gross_Profit"] / p["Revenue"]
    p["Operating_Expenses"] = opex
    p["Operating_Income"] = p["Gross_Profit"] - p["Operating_Expenses"]
    p["Operating_Margin_%"] = 100 * p["Operating_Income"] / p["Revenue"]
    p.loc["Total"] = p.sum()
    for col in ("Gross_Margin_%", "Operating_Margin_%"):
        num = "Gross_Profit" if col.startswith("Gross") else "Operating_Income"
        p.loc["Total", col] = 100 * p.loc["Total", num] / p.loc["Total", "Revenue"]
    return p.round(2).reset_index().rename(columns={"index": "Location"})


def monthly_pnl(gl):
    g = gl.assign(Net=gl["Credit"] - gl["Debit"], Month=gl["Month_End"].dt.to_period("M").astype(str))
    rev = g[g["Account"] == "4000"].groupby("Month")["Net"].sum()
    gp = rev + g[g["Account"] == "5000"].groupby("Month")["Net"].sum()
    oi = gp + g[g["Account"].str.startswith("6")].groupby("Month")["Net"].sum()
    return pd.DataFrame({"Revenue": rev, "Gross_Profit": gp, "Operating_Income": oi,
                         "Gross_Margin_%": 100 * gp / rev}).round(2).reset_index()


def reconcile(sales, gl):
    """Sales subledger vs GL postings, by month, location and account."""
    s = sales.assign(Month_End=sales["Date"].dt.to_period("M").dt.to_timestamp("M"))
    sub = s.groupby(["Month_End", "Location"]).agg(Revenue=("Total_Revenue", "sum"), COGS=("Total_Cost", "sum"))
    g = gl.set_index(["Month_End", "Location"])
    posted = pd.DataFrame({
        "Revenue": g[g["Account"] == "4000"]["Credit"].groupby(level=[0, 1]).sum(),
        "COGS": g[g["Account"] == "5000"]["Debit"].groupby(level=[0, 1]).sum(),
    })
    r = sub.join(posted, lsuffix="_Subledger", rsuffix="_GL")
    r["Revenue_Diff"] = (r["Revenue_GL"] - r["Revenue_Subledger"]).round(2)
    r["COGS_Diff"] = (r["COGS_GL"] - r["COGS_Subledger"]).round(2)
    r["Status"] = np.where((r[["Revenue_Diff", "COGS_Diff"]].abs() > RECON_TOLERANCE).any(axis=1),
                           "BREAK", "OK")
    return r.round(2).reset_index()


def inventory_metrics(sales, inventory):
    """Annual turnover, days on hand and GMROI per product."""
    by = sales.groupby("Product").agg(Revenue=("Total_Revenue", "sum"), COGS=("Total_Cost", "sum"),
                                      Gross_Margin=("Gross_Margin", "sum"), Units_Sold=("Units", "sum"))
    avg_inv = inventory.groupby("Product")["Inventory_Cost"].mean().rename("Avg_Inventory_Cost")
    m = by.join(avg_inv)
    m["Inventory_Turnover"] = m["COGS"] / m["Avg_Inventory_Cost"]
    m["Days_On_Hand"] = 365 / m["Inventory_Turnover"]
    m["GMROI"] = m["Gross_Margin"] / m["Avg_Inventory_Cost"]     # gross margin $ per $ of inventory
    m["Gross_Margin_%"] = 100 * m["Gross_Margin"] / m["Revenue"]
    m["Flag"] = np.where(m["Days_On_Hand"] > 45, "Slow-moving: review stocking level", "")
    return m.round(2).sort_values("GMROI", ascending=False).reset_index()


def forecast(sales, months=12, growth=GROWTH_ASSUMPTION):
    """Next-12-month revenue: the same month last year, grown by an explicit annual growth assumption.

    With a single year of history a fitted trend mistakes seasonality for growth (the year ends in
    the slow winter months), so a seasonal-naive forecast with a stated growth rate is more reliable.
    Swap in a trend-and-seasonality model once two or more years of history are available.
    """
    monthly = sales.groupby(sales["Date"].dt.to_period("M"))["Total_Revenue"].sum()
    last_year = monthly.iloc[-12:]
    periods = pd.period_range(monthly.index[-1] + 1, periods=months, freq="M")
    fc = last_year.values[np.arange(months) % 12] * (1 + growth)
    hist = pd.DataFrame({"Month": monthly.index.astype(str), "Revenue": monthly.values.round(2), "Type": "Actual"})
    fut = pd.DataFrame({"Month": periods.astype(str), "Revenue": fc.round(2), "Type": "Forecast"})
    return pd.concat([hist, fut], ignore_index=True)


def charts(monthly, fc, inv):
    OUT.mkdir(exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 4))
    a, f = fc[fc["Type"] == "Actual"], fc[fc["Type"] == "Forecast"]
    ax.plot(a["Month"], a["Revenue"] / 1e3, marker="o", label="Actual")
    ax.plot(f["Month"], f["Revenue"] / 1e3, marker="o", linestyle="--", label="Forecast")
    ax.set_ylabel("Revenue ($K)")
    ax.set_title("Monthly revenue: 2025 actuals and 12-month forecast")
    ax.tick_params(axis="x", rotation=60, labelsize=8)
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "revenue_forecast.png", dpi=130)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 3.5))
    ax.barh(inv["Product"], inv["GMROI"], color="#4C78A8")
    ax.set_xlabel("GMROI (gross margin $ per $ of average inventory)")
    ax.set_title("Inventory return by product")
    fig.tight_layout()
    fig.savefig(OUT / "gmroi_by_product.png", dpi=130)
    plt.close(fig)


def main():
    sales, inventory, gl = load()
    views = {
        "PnL_by_Location": pnl(gl),
        "PnL_by_Month": monthly_pnl(gl),
        "GL_Reconciliation": reconcile(sales, gl),
        "Inventory_Metrics": inventory_metrics(sales, inventory),
        "Revenue_Forecast": forecast(sales),
    }
    OUT.mkdir(exist_ok=True)
    with pd.ExcelWriter(OUT / "financial_intelligence.xlsx", engine="openpyxl") as w:
        for name, df in views.items():
            df.to_excel(w, sheet_name=name, index=False)
    charts(views["PnL_by_Month"], views["Revenue_Forecast"], views["Inventory_Metrics"])

    pd.set_option("display.width", 160)
    print(views["PnL_by_Location"].to_string(index=False), "\n")
    breaks = views["GL_Reconciliation"].query("Status == 'BREAK'")
    print(f"GL reconciliation: {len(breaks)} break(s)")
    if len(breaks):
        print(breaks[["Month_End", "Location", "Revenue_Diff", "COGS_Diff"]].to_string(index=False))
    print()
    print(views["Inventory_Metrics"][["Product", "Inventory_Turnover", "Days_On_Hand", "GMROI", "Flag"]]
          .to_string(index=False))
    fc = views["Revenue_Forecast"]
    print(f"\nNext-12-month revenue forecast: ${fc.loc[fc['Type'] == 'Forecast', 'Revenue'].sum():,.0f}"
          f" (assumes {GROWTH_ASSUMPTION:.0%} growth)")
    print(f"Outputs written to {OUT}")
    return views


if __name__ == "__main__":
    main()
