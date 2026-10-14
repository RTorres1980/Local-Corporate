import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import analysis  # noqa: E402
from generate_data import generate  # noqa: E402


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    return generate(seed=1, out_dir=tmp_path_factory.mktemp("data"))


def test_pnl_totals_tie(data):
    _, _, gl = data
    p = analysis.pnl(gl).set_index("Location")
    t = p.loc["Total"]
    assert t["Gross_Profit"] == pytest.approx(t["Revenue"] - t["COGS"], abs=0.05)
    assert t["Operating_Income"] == pytest.approx(t["Gross_Profit"] - t["Operating_Expenses"], abs=0.05)
    assert t["Revenue"] == pytest.approx(p.drop("Total")["Revenue"].sum(), abs=0.05)


def test_reconciliation_finds_only_the_planted_break(data):
    sales, _, gl = data
    r = analysis.reconcile(sales, gl)
    breaks = r[r["Status"] == "BREAK"]
    assert len(breaks) == 1
    row = breaks.iloc[0]
    assert row["Location"] == "Murray-Retail" and row["Revenue_Diff"] == -4850.0 and row["COGS_Diff"] == 0


def test_inventory_metrics(data):
    sales, inventory, _ = data
    m = analysis.inventory_metrics(sales, inventory).set_index("Product")
    # both columns are rounded to 2 decimals, so compare within 1%
    assert ((m["Days_On_Hand"] * m["Inventory_Turnover"] / 365) - 1).abs().max() < 0.01
    assert m.loc["Custom-Part-X", "Flag"].startswith("Slow-moving")
    assert m["Days_On_Hand"].idxmax() == "Custom-Part-X"


def test_forecast_is_last_year_times_growth(data):
    sales, _, _ = data
    fc = analysis.forecast(sales, growth=0.10)
    actual = fc[fc["Type"] == "Actual"]["Revenue"].values
    future = fc[fc["Type"] == "Forecast"]["Revenue"].values
    assert len(future) == 12
    assert future == pytest.approx(actual[-12:] * 1.10, rel=1e-6)
    assert fc[fc["Type"] == "Forecast"]["Month"].iloc[0] == "2026-01"


def test_sales_margin_columns(data):
    sales, _, _ = data
    assert (sales["Gross_Margin"] - (sales["Total_Revenue"] - sales["Total_Cost"])).abs().max() < 0.011
    margin = sales["Gross_Margin"].sum() / sales["Total_Revenue"].sum()
    assert 0.3 < margin < 0.6
