"""Test #2 Financial Trends calculation dengan mock quarterly data."""

from montrva.layer2.contracts import QuarterlyFundamental
from montrva.layer2.knowledge_helpers import compute_financial_trends

# Mock quarterly data untuk AAPL-like company (4 recent quarters)
# Most recent first
mock_quarterly = [
    QuarterlyFundamental(
        period='2024-Q1',
        revenue=120_000_000_000,
        gross_profit=48_000_000_000,
        operating_income=36_000_000_000,
        net_income=30_000_000_000,
        capital_expenditures=6_000_000_000,
        fiscal_date='2024-03-31'
    ),
    QuarterlyFundamental(
        period='2023-Q4',
        revenue=115_000_000_000,
        gross_profit=46_000_000_000,
        operating_income=34_500_000_000,
        net_income=28_750_000_000,
        capital_expenditures=5_750_000_000,
        fiscal_date='2023-12-31'
    ),
    QuarterlyFundamental(
        period='2023-Q3',
        revenue=110_000_000_000,
        gross_profit=44_000_000_000,
        operating_income=33_000_000_000,
        net_income=27_500_000_000,
        capital_expenditures=5_500_000_000,
        fiscal_date='2023-09-30'
    ),
    QuarterlyFundamental(
        period='2023-Q2',
        revenue=105_000_000_000,
        gross_profit=42_000_000_000,
        operating_income=31_500_000_000,
        net_income=26_250_000_000,
        capital_expenditures=5_250_000_000,
        fiscal_date='2023-06-30'
    ),
    # Prior year quarters for YoY comparison
    QuarterlyFundamental(
        period='2023-Q1',
        revenue=112_000_000_000,
        gross_profit=44_800_000_000,
        operating_income=33_600_000_000,
        net_income=28_000_000_000,
        capital_expenditures=5_600_000_000,
        fiscal_date='2023-03-31'
    ),
]

trends = compute_financial_trends(mock_quarterly)

print("Financial Trends Calculation (Mock Data):")
print("=" * 60)
for key, value in sorted(trends.items()):
    if value is not None:
        if 'margin' in key or 'yoy' in key or 'capex' in key:
            print(f"{key:30} {value:8.2f}%")
        else:
            print(f"{key:30} {value:8.2f}")

print()
print("Interpretation:")
print("-" * 60)
print(f"Revenue YoY (Q4): {trends.get('revenue_yoy_q4', 'N/A')}%  (Q1 2024 vs Q1 2023)")
print(f"Gross Margin Trend (Q4->Q1): {trends.get('gross_margin_q4', 'N/A')}% -> 40%")
print(f"  -> Margin stable or slightly improving")
print(f"Operating Margin Trend: {trends.get('operating_margin_q4', 'N/A')}% -> 30%")
print(f"  -> Operating leverage maintained")
print(f"Net Margin Trend: {trends.get('net_margin_q4', 'N/A')}% -> 25%")
print(f"  -> Net profitability stable")
print(f"CapEx as % Revenue (Q1): {trends.get('capex_pct_revenue_q4', 'N/A')}%")
print(f"  -> About 5% of revenue reinvested in capital")
