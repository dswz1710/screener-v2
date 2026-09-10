#!/usr/bin/env python3
"""
NSE Screener Backtest
======================
Tests whether the screener's ranking approach would actually have beaten
just holding the Nifty 50 index over a historical period.

IMPORTANT HONESTY NOTE
-----------------------
Free data sources (yfinance included) only expose CURRENT fundamentals
(P/E, P/B, ROE, D/E) -- not historical point-in-time fundamentals. So this
script runs TWO separate backtests:

  1. MOMENTUM BACKTEST (rigorous, no look-ahead bias)
     Uses only historical price data, which is genuinely available for
     every past date. At each monthly rebalance, ranks stocks purely on
     trailing 1-month momentum + distance from 52-week high -- exactly
     the momentum component of the live screener -- and only uses data
     that would have actually been available on that date.

  2. FULL-MODEL BACKTEST (approximate, HAS LOOK-AHEAD BIAS)
     Applies today's P/E / P/B / ROE snapshot to every historical
     rebalance date, because that's all free data gives us. This is
     clearly labeled and should be read as illustrative only -- it will
     look better than a real trader could have achieved, because it
     "knows" today's fundamentals in the past.

Read the momentum backtest as the trustworthy number. Read the full-model
backtest as "what if fundamentals had stayed roughly where they are now" --
not as proof the strategy works.

SETUP
-----
pip install yfinance pandas numpy matplotlib --break-system-packages
python3 backtest.py
"""

import sys
import os
import datetime
import logging

try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    print("Missing dependencies. Run: pip install yfinance pandas numpy matplotlib --break-system-packages")
    sys.exit(1)

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------
WATCHLIST = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "BHARTIARTL.NS", "SBIN.NS", "LT.NS", "ITC.NS", "HINDUNILVR.NS",
    "AXISBANK.NS", "KOTAKBANK.NS", "MARUTI.NS", "SUNPHARMA.NS", "TITAN.NS",
    "BAJFINANCE.NS", "ASIANPAINT.NS", "WIPRO.NS", "ULTRACEMCO.NS", "NTPC.NS",
]
BENCHMARK = "^NSEI"          # Nifty 50 index
BACKTEST_YEARS = 3
TOP_N = 5                    # how many stocks the strategy holds each month
RISK_FREE_RATE = 0.065       # approx Indian 10Y G-sec yield, for Sharpe ratio
TRANSACTION_COST_PCT = 0.001 # 0.1% per rebalance trade, rough estimate

try:
    _base_dir = os.path.dirname(os.path.abspath(__file__))
except NameError:
    # __file__ doesn't exist when code is pasted directly into a notebook
    # cell (Colab/Jupyter) rather than run as a .py file. Fall back to cwd.
    _base_dir = os.getcwd()

OUTPUT_DIR = os.path.join(_base_dir, "backtest_results")
os.makedirs(OUTPUT_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger(__name__)


def download_data():
    log.info(f"Downloading {BACKTEST_YEARS}y of price history for {len(WATCHLIST)} stocks + benchmark...")
    end = datetime.date.today()
    start = end - datetime.timedelta(days=365 * BACKTEST_YEARS + 60)  # +60 buffer for momentum lookback
    prices = yf.download(WATCHLIST, start=start, end=end)["Close"]
    bench = yf.download(BENCHMARK, start=start, end=end)["Close"]
    prices = prices.dropna(axis=1, how="all")  # drop tickers with no data
    return prices, bench


def monthly_rebalance_dates(prices: pd.DataFrame):
    """Last trading day of each month, skipping the first 2 months (need momentum lookback)."""
    monthly = prices.resample("ME").last()
    return monthly.index[2:]


def run_momentum_backtest(prices: pd.DataFrame):
    """
    Rigorous backtest: at each rebalance date, rank stocks using ONLY price
    data available up to (and including) that date. No fundamentals used,
    so no look-ahead bias is possible.
    """
    rebal_dates = monthly_rebalance_dates(prices)
    portfolio_returns = []
    holdings_log = []

    for i in range(len(rebal_dates) - 1):
        date = rebal_dates[i]
        next_date = rebal_dates[i + 1]

        window = prices.loc[:date].tail(252)  # trailing ~1y available as of `date`
        if window.shape[0] < 60:
            continue

        current_price = window.iloc[-1]
        month_ago_price = window.iloc[-22] if window.shape[0] >= 22 else window.iloc[0]
        year_high = window.max()

        momentum_1m = (current_price - month_ago_price) / month_ago_price
        pct_from_high = (current_price - year_high) / year_high

        score = momentum_1m.rank(pct=True) * 0.5 + pct_from_high.rank(pct=True) * 0.5
        top_picks = score.dropna().sort_values(ascending=False).head(TOP_N).index.tolist()

        if not top_picks:
            continue

        fwd_returns = (prices.loc[next_date, top_picks] / prices.loc[date, top_picks]) - 1
        month_return = fwd_returns.mean() - TRANSACTION_COST_PCT  # equal-weighted, minus rough costs
        portfolio_returns.append((next_date, month_return))
        holdings_log.append((date, top_picks, round(month_return * 100, 2)))

    return pd.DataFrame(portfolio_returns, columns=["date", "return"]).set_index("date"), holdings_log


def run_full_model_backtest(prices: pd.DataFrame):
    """
    Approximate backtest: adds CURRENT fundamental snapshot (P/E, ROE, D/E)
    to the ranking at every historical date. Flagged as look-ahead biased.
    """
    log.info("Fetching current fundamentals snapshot for full-model backtest (look-ahead biased)...")
    fundamentals = {}
    for ticker in prices.columns:
        try:
            info = yf.Ticker(ticker).info
            fundamentals[ticker] = {
                "pe": info.get("trailingPE", np.nan),
                "pb": info.get("priceToBook", np.nan),
                "roe": info.get("returnOnEquity", np.nan),
            }
        except Exception:
            fundamentals[ticker] = {"pe": np.nan, "pb": np.nan, "roe": np.nan}

    fund_df = pd.DataFrame(fundamentals).T
    val_score = (
        (1 - fund_df["pe"].rank(pct=True).fillna(0.5)) * 0.5
        + (1 - fund_df["pb"].rank(pct=True).fillna(0.5)) * 0.5
    )
    quality_score = fund_df["roe"].rank(pct=True).fillna(0.5)
    static_fundamental_score = val_score * 0.5 + quality_score * 0.5  # same weight every period (static)

    rebal_dates = monthly_rebalance_dates(prices)
    portfolio_returns = []

    for i in range(len(rebal_dates) - 1):
        date = rebal_dates[i]
        next_date = rebal_dates[i + 1]
        window = prices.loc[:date].tail(252)
        if window.shape[0] < 60:
            continue

        current_price = window.iloc[-1]
        month_ago_price = window.iloc[-22] if window.shape[0] >= 22 else window.iloc[0]
        year_high = window.max()
        momentum_score = ((current_price - month_ago_price) / month_ago_price).rank(pct=True) * 0.25 \
            + ((current_price - year_high) / year_high).rank(pct=True) * 0.25

        combined = momentum_score.add(static_fundamental_score * 0.5, fill_value=0)
        top_picks = combined.dropna().sort_values(ascending=False).head(TOP_N).index.tolist()
        if not top_picks:
            continue

        fwd_returns = (prices.loc[next_date, top_picks] / prices.loc[date, top_picks]) - 1
        month_return = fwd_returns.mean() - TRANSACTION_COST_PCT
        portfolio_returns.append((next_date, month_return))

    return pd.DataFrame(portfolio_returns, columns=["date", "return"]).set_index("date")


def benchmark_monthly_returns(bench: pd.Series, index_dates):
    monthly = bench.resample("ME").last()
    monthly = monthly.reindex(index_dates, method="nearest")
    return monthly.pct_change().dropna()


def performance_stats(returns: pd.Series, label: str) -> dict:
    if returns.empty:
        return {"label": label, "error": "no data"}
    cumulative = (1 + returns).cumprod()
    total_return = cumulative.iloc[-1] - 1
    n_months = len(returns)
    cagr = (1 + total_return) ** (12 / n_months) - 1 if n_months > 0 else np.nan
    vol_annual = returns.std() * np.sqrt(12)
    sharpe = (cagr - RISK_FREE_RATE) / vol_annual if vol_annual > 0 else np.nan
    running_max = cumulative.cummax()
    drawdown = (cumulative - running_max) / running_max
    max_dd = drawdown.min()
    return {
        "label": label,
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "annual_volatility_pct": round(vol_annual * 100, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "months_tested": n_months,
    }


def plot_comparison(curves: dict, path: str):
    plt.figure(figsize=(10, 6))
    for label, cum in curves.items():
        plt.plot(cum.index, cum.values, label=label, linewidth=2)
    plt.title(f"Strategy vs Nifty 50 — {BACKTEST_YEARS}Y Backtest")
    plt.ylabel("Growth of ₹1")
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    log.info(f"Chart saved to {path}")


def run():
    prices, bench = download_data()

    log.info("Running momentum-only backtest (rigorous, no look-ahead)...")
    mom_returns, holdings_log = run_momentum_backtest(prices)

    log.info("Running full-model backtest (approximate, look-ahead biased)...")
    full_returns = run_full_model_backtest(prices)

    bench_returns_mom = benchmark_monthly_returns(bench.iloc[:, 0] if isinstance(bench, pd.DataFrame) else bench, mom_returns.index)

    stats = [
        performance_stats(mom_returns["return"], "Momentum strategy (rigorous)"),
        performance_stats(full_returns["return"], "Full model (approx, look-ahead biased)"),
        performance_stats(bench_returns_mom, "Nifty 50 buy & hold (benchmark)"),
    ]

    stats_df = pd.DataFrame(stats)
    stats_path = os.path.join(OUTPUT_DIR, "backtest_summary.csv")
    stats_df.to_csv(stats_path, index=False)

    print("\n" + "=" * 78)
    print(f"BACKTEST RESULTS — {BACKTEST_YEARS} years, {TOP_N} stocks held, monthly rebalance")
    print("=" * 78)
    print(stats_df.to_string(index=False))
    print("=" * 78)
    print("Momentum strategy = trustworthy (no look-ahead bias).")
    print("Full model = illustrative only (uses TODAY's fundamentals on PAST dates).")
    print("=" * 78 + "\n")

    curves = {
        "Momentum strategy": (1 + mom_returns["return"]).cumprod(),
        "Full model (biased)": (1 + full_returns["return"]).cumprod(),
        "Nifty 50": (1 + bench_returns_mom).cumprod(),
    }
    plot_comparison(curves, os.path.join(OUTPUT_DIR, "backtest_chart.png"))

    holdings_path = os.path.join(OUTPUT_DIR, "monthly_holdings_log.csv")
    pd.DataFrame(holdings_log, columns=["date", "holdings", "month_return_pct"]).to_csv(holdings_path, index=False)
    log.info(f"Monthly holdings log saved to {holdings_path}")
    log.info(f"Summary stats saved to {stats_path}")


if __name__ == "__main__":
    run()
