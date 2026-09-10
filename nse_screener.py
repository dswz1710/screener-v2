#!/usr/bin/env python3
"""
NSE Daily Stock Screener
=========================
Pulls live price + fundamental data for a watchlist of NSE stocks,
scores each one on valuation + momentum ratios, and writes a ranked
shortlist to CSV + a plain-text log every time it runs.

This is a DECISION-SUPPORT tool, not a buy/sell signal generator.
It surfaces the numbers and a transparent score -- you make the call.

SETUP
-----
1. pip install yfinance pandas numpy feedparser
2. Edit WATCHLIST below (add/remove NSE tickers, ".NS" suffix required)
3. Run manually to test:  python3 nse_screener.py
4. Automate with cron (see bottom of this file for the crontab line)
5. Optional alerts: set environment variables before running --
   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  (create a bot via @BotFather on Telegram)
   SCREENER_EMAIL_ADDRESS, SCREENER_EMAIL_APP_PASSWORD  (Gmail app password, not your login password)
   See README.md for step-by-step Telegram bot setup.

OUTPUT
------
- results/screener_YYYY-MM-DD.csv   -> full ranked table
- results/screener_YYYY-MM-DD.log   -> human-readable top-10 summary
"""

import sys
import os
import datetime
import logging

import json
import re
import urllib.request
import urllib.parse

try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
    import feedparser
except ImportError:
    print("Missing dependencies. Run: pip install yfinance pandas numpy feedparser --break-system-packages")
    sys.exit(1)

# ----------------------------------------------------------------------
# CONFIG - edit this section for your own use
# ----------------------------------------------------------------------

# NSE tickers need a ".NS" suffix for Yahoo Finance. Add/remove as you like.
WATCHLIST = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "BHARTIARTL.NS", "SBIN.NS", "LT.NS", "ITC.NS", "HINDUNILVR.NS",
    "AXISBANK.NS", "KOTAKBANK.NS", "MARUTI.NS", "SUNPHARMA.NS", "TITAN.NS",
    "BAJFINANCE.NS", "ASIANPAINT.NS", "WIPRO.NS", "ULTRACEMCO.NS", "NTPC.NS",
]

# Upcoming/recent IPOs to track manually (Yahoo Finance has no IPO calendar API).
# Update this list yourself from NSE/BSE/SEBI filings or Chittorgarh/Moneycontrol.
IPO_WATCHLIST = [
    # {"name": "Example Co", "expected_date": "2026-09-15", "price_band": "180-190", "notes": "SME/Mainboard"},
]

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
TOP_N = 10

# Free public RSS feeds - no API key needed. Add/remove sources as you like.
NEWS_FEEDS = [
    "https://www.moneycontrol.com/rss/marketreports.xml",
    "https://www.moneycontrol.com/rss/results.xml",
    "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
]

# Simple keyword-based sentiment (no ML dependency, transparent and tunable).
POSITIVE_WORDS = {"surge", "rally", "gain", "gains", "profit", "beats", "upgrade",
                   "growth", "record", "high", "jump", "rise", "rises", "bullish",
                   "outperform", "strong", "buy"}
NEGATIVE_WORDS = {"fall", "falls", "drop", "decline", "loss", "miss", "downgrade",
                   "crash", "plunge", "slump", "weak", "bearish", "underperform",
                   "sell", "cut", "concern", "probe", "fraud"}

# Scoring weights (must sum to 1.0) -- tune to taste
WEIGHTS = {
    "valuation": 0.35,   # lower P/E, P/B relative to peers = better
    "quality": 0.25,     # higher ROE, lower D/E = better
    "momentum": 0.25,    # price vs 50/200 DMA, recent trend = better
    "dividend": 0.15,    # dividend yield
}

# ----------------------------------------------------------------------
# ALERTS - sends a message when a stock's score crosses ALERT_SCORE_THRESHOLD
# ----------------------------------------------------------------------
# Credentials are read from environment variables, NEVER hardcoded here.
# Locally: export them in your shell before running the script.
# On GitHub Actions: add them as repo Secrets (Settings -> Secrets -> Actions)
# and pass them into the workflow's `env:` block (see .github/workflows/screener.yml).
ALERTS_ENABLED = True
ALERT_SCORE_THRESHOLD = 75.0   # alert when a stock's score >= this
ALERT_TOP_N_ONLY = True        # only alert for stocks that are in the top TOP_N

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

EMAIL_ADDRESS = os.environ.get("SCREENER_EMAIL_ADDRESS")       # sender gmail address
EMAIL_APP_PASSWORD = os.environ.get("SCREENER_EMAIL_APP_PASSWORD")  # gmail app password, not your login password
EMAIL_TO = os.environ.get("SCREENER_EMAIL_TO", EMAIL_ADDRESS)  # recipient, defaults to yourself

# ----------------------------------------------------------------------

os.makedirs(OUTPUT_DIR, exist_ok=True)
today = datetime.date.today().isoformat()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(OUTPUT_DIR, f"screener_{today}.log")),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def fetch_stock_data(ticker: str) -> dict:
    """Pull fundamentals + price data for one ticker. Returns None on failure."""
    try:
        t = yf.Ticker(ticker)
        info = t.info
        hist = t.history(period="1y")

        if hist.empty:
            log.warning(f"No price history for {ticker}, skipping.")
            return None

        current_price = hist["Close"].iloc[-1]
        dma50 = hist["Close"].tail(50).mean()
        dma200 = hist["Close"].mean() if len(hist) >= 200 else hist["Close"].mean()
        year_high = hist["High"].max()
        year_low = hist["Low"].min()

        # 1-month price momentum
        month_ago_price = hist["Close"].iloc[-22] if len(hist) >= 22 else hist["Close"].iloc[0]
        momentum_1m = (current_price - month_ago_price) / month_ago_price * 100

        return {
            "ticker": ticker,
            "name": info.get("shortName", ticker),
            "sector": info.get("sector", "N/A"),
            "price": round(current_price, 2),
            "pe_ratio": info.get("trailingPE"),
            "pb_ratio": info.get("priceToBook"),
            "roe": info.get("returnOnEquity"),
            "debt_to_equity": info.get("debtToEquity"),
            "dividend_yield": info.get("dividendYield"),
            "dma50": round(dma50, 2),
            "dma200": round(dma200, 2),
            "year_high": round(year_high, 2),
            "year_low": round(year_low, 2),
            "momentum_1m_pct": round(momentum_1m, 2),
            "pct_from_52w_high": round((current_price - year_high) / year_high * 100, 2),
        }
    except Exception as e:
        log.warning(f"Failed to fetch {ticker}: {e}")
        return None


def normalize(series: pd.Series, invert: bool = False) -> pd.Series:
    """Min-max normalize a series to 0-1. invert=True means lower raw value = higher score."""
    s = series.astype(float)
    if s.max() == s.min():
        return pd.Series([0.5] * len(s), index=s.index)
    norm = (s - s.min()) / (s.max() - s.min())
    return 1 - norm if invert else norm


def score_stocks(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the ratio-based scoring model. Adds a 'score' column, 0-100."""
    df = df.copy()

    # Fill missing fundamental data with column median so one bad ticker doesn't skew results
    for col in ["pe_ratio", "pb_ratio", "roe", "debt_to_equity", "dividend_yield"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
        df[col] = df[col].fillna(df[col].median())

    valuation_score = (
        normalize(df["pe_ratio"], invert=True) * 0.5
        + normalize(df["pb_ratio"], invert=True) * 0.5
    )
    quality_score = (
        normalize(df["roe"], invert=False) * 0.6
        + normalize(df["debt_to_equity"], invert=True) * 0.4
    )
    momentum_score = (
        normalize(df["momentum_1m_pct"], invert=False) * 0.5
        + normalize(df["pct_from_52w_high"], invert=False) * 0.5
    )
    dividend_score = normalize(df["dividend_yield"], invert=False)

    df["score"] = (
        valuation_score * WEIGHTS["valuation"]
        + quality_score * WEIGHTS["quality"]
        + momentum_score * WEIGHTS["momentum"]
        + dividend_score * WEIGHTS["dividend"]
    ) * 100
    df["score"] = df["score"].round(2)

    return df.sort_values("score", ascending=False)


def fetch_news_sentiment() -> dict:
    """
    Pull recent market headlines from RSS feeds and score sentiment per
    headline via keyword matching. Returns {company_name_lower_fragment: score}
    aggregated, plus an 'overall' market mood score.
    Falls back to empty dict if feeds are unreachable (e.g. no internet).
    """
    headlines = []
    for url in NEWS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:20]:
                headlines.append(entry.get("title", ""))
        except Exception as e:
            log.warning(f"Could not fetch feed {url}: {e}")

    if not headlines:
        log.warning("No news headlines fetched - sentiment will be neutral (0) for all stocks.")
        return {"_overall": 0.0, "_headline_count": 0}

    def score_text(text: str) -> int:
        words = set(re.findall(r"[a-z]+", text.lower()))
        return len(words & POSITIVE_WORDS) - len(words & NEGATIVE_WORDS)

    overall = sum(score_text(h) for h in headlines) / len(headlines)

    per_stock = {"_overall": round(overall, 3), "_headline_count": len(headlines)}
    return per_stock, headlines


def attach_sentiment(df: pd.DataFrame, headlines: list) -> pd.DataFrame:
    """Match company names against headlines for a per-stock sentiment nudge."""
    df = df.copy()
    scores = []
    for _, row in df.iterrows():
        name_fragment = str(row["name"]).split()[0].lower()  # e.g. "Reliance" from "Reliance Industries"
        related = [h for h in headlines if name_fragment in h.lower()]
        if related:
            words = set(re.findall(r"[a-z]+", " ".join(related).lower()))
            s = len(words & POSITIVE_WORDS) - len(words & NEGATIVE_WORDS)
        else:
            s = 0
        scores.append(s)
    df["news_sentiment"] = scores
    df["news_mentions"] = [len([h for h in headlines if str(n).split()[0].lower() in h.lower()])
                            for n in df["name"]]
    return df


def export_json(df: pd.DataFrame, overall_sentiment: float, headline_count: int):
    """Write results in the shape the webpage dashboard expects.
    Written to BOTH results/data.json (archival) and docs/data.json
    (what the live GitHub Pages site actually fetches, since Pages
    only serves files inside the /docs folder)."""
    payload = {
        "generated_at": datetime.datetime.now().isoformat(),
        "market": "NSE",
        "overall_news_sentiment": overall_sentiment,
        "headline_count": headline_count,
        "stocks": json.loads(df.to_json(orient="records")),
        "ipo_watchlist": IPO_WATCHLIST,
    }
    json_path = os.path.join(OUTPUT_DIR, "data.json")
    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    log.info(f"Dashboard data written to {json_path}")

    docs_dir = os.path.join(_base_dir, "docs")
    if os.path.isdir(docs_dir):
        docs_json_path = os.path.join(docs_dir, "data.json")
        with open(docs_json_path, "w") as f:
            json.dump(payload, f, indent=2, default=str)
        log.info(f"Live site data written to {docs_json_path}")


def send_telegram_alert(message: str) -> bool:
    """Send a message via Telegram bot. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown",
        }).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        log.warning(f"Telegram alert failed: {e}")
        return False


def send_email_alert(subject: str, body: str) -> bool:
    """Send an email alert via Gmail SMTP. Returns True on success."""
    if not EMAIL_ADDRESS or not EMAIL_APP_PASSWORD or not EMAIL_TO:
        return False
    try:
        import smtplib
        from email.mime.text import MIMEText

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = EMAIL_TO

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        log.warning(f"Email alert failed: {e}")
        return False


def send_alerts(ranked: pd.DataFrame):
    """Check ranked stocks against the alert threshold and notify configured channels."""
    if not ALERTS_ENABLED:
        return

    candidates = ranked.head(TOP_N) if ALERT_TOP_N_ONLY else ranked
    triggered = candidates[candidates["score"] >= ALERT_SCORE_THRESHOLD]

    if triggered.empty:
        log.info(f"No stocks crossed the alert threshold ({ALERT_SCORE_THRESHOLD}) today.")
        return

    lines = [f"NSE Screener Alert — {today}", f"{len(triggered)} stock(s) scored >= {ALERT_SCORE_THRESHOLD}:", ""]
    for _, row in triggered.iterrows():
        lines.append(
            f"• {row['name']} ({row['ticker']}) — Score {row['score']:.1f} | "
            f"P/E {row['pe_ratio']:.1f} | 1M mom {row['momentum_1m_pct']:.1f}% | "
            f"News sentiment {row['news_sentiment']:+d}"
        )
    lines.append("\nNot investment advice — verify independently before acting.")
    message = "\n".join(lines)

    sent_telegram = send_telegram_alert(message)
    sent_email = send_email_alert(f"NSE Screener: {len(triggered)} stock(s) above threshold", message)

    if not sent_telegram and not TELEGRAM_BOT_TOKEN:
        log.info("Telegram not configured (set TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID to enable).")
    if not sent_email and not EMAIL_ADDRESS:
        log.info("Email not configured (set SCREENER_EMAIL_ADDRESS / SCREENER_EMAIL_APP_PASSWORD to enable).")
    if sent_telegram:
        log.info("Telegram alert sent.")
    if sent_email:
        log.info("Email alert sent.")


def run():
    log.info(f"Starting NSE screener run for {today} on {len(WATCHLIST)} stocks")
    rows = [d for d in (fetch_stock_data(tk) for tk in WATCHLIST) if d is not None]

    if not rows:
        log.error("No data fetched for any ticker. Check your internet connection / ticker list.")
        return

    df = pd.DataFrame(rows)
    ranked = score_stocks(df)

    sentiment_result = fetch_news_sentiment()
    if isinstance(sentiment_result, tuple):
        overall_meta, headlines = sentiment_result
    else:
        overall_meta, headlines = sentiment_result, []
    ranked = attach_sentiment(ranked, headlines)

    csv_path = os.path.join(OUTPUT_DIR, f"screener_{today}.csv")
    ranked.to_csv(csv_path, index=False)
    log.info(f"Full results saved to {csv_path}")

    export_json(ranked, overall_meta.get("_overall", 0.0), overall_meta.get("_headline_count", 0))

    send_alerts(ranked)

    log.info(f"\n{'='*70}\nTOP {TOP_N} BY SCORE - {today}\n{'='*70}")
    for _, row in ranked.head(TOP_N).iterrows():
        log.info(
            f"{row['name']:<20} ({row['ticker']:<15}) | Score: {row['score']:>6.2f} | "
            f"P/E: {row['pe_ratio']:>6.1f} | P/B: {row['pb_ratio']:>5.2f} | "
            f"ROE: {row['roe'] if pd.notna(row['roe']) else 'N/A'} | "
            f"1M mom: {row['momentum_1m_pct']:>6.2f}% | "
            f"From 52wH: {row['pct_from_52w_high']:>6.2f}%"
        )

    if IPO_WATCHLIST:
        log.info(f"\n{'='*70}\nIPO WATCHLIST (manually maintained)\n{'='*70}")
        for ipo in IPO_WATCHLIST:
            log.info(f"{ipo['name']} | Expected: {ipo['expected_date']} | Band: {ipo.get('price_band','N/A')}")

    log.info("\nDisclaimer: Educational/research tool only, not investment advice. "
              "Verify all data independently before trading.")


if __name__ == "__main__":
    run()

# ----------------------------------------------------------------------
# AUTOMATION - run automatically every trading day at 9:15 AM IST
# ----------------------------------------------------------------------
# On Linux/Mac, add this line via `crontab -e`:
#
#   15 9 * * 1-5 /usr/bin/python3 /path/to/nse_screener.py >> /path/to/results/cron.log 2>&1
#
# Breakdown: minute=15, hour=9, every day-of-month, every month, Mon-Fri only.
#
# On Windows, use Task Scheduler:
#   Trigger: Daily, 9:15 AM, repeat Mon-Fri
#   Action:  python.exe C:\path\to\nse_screener.py
# ----------------------------------------------------------------------
