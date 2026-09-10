# NSE Screener — Full Setup

Three files work together:
1. `nse_screener.py` — pulls ratios + news sentiment, ranks stocks, writes `results/data.json`
2. `.github_workflows_screener.yml` — runs step 1 automatically every trading day at 9:15 AM IST
3. `dashboard/index.html` — the webpage, reads `results/data.json` and displays it live

## Setup (15 minutes, all free)

1. **Create a GitHub repo** (e.g. `nse-screener`), public.
2. Add these files to it:
   - `nse_screener.py` at the repo root
   - `.github_workflows_screener.yml` → rename/move to `.github/workflows/screener.yml`
   - `dashboard/index.html` as-is
3. **Turn on GitHub Pages**: repo Settings → Pages → Source: `main` branch, folder `/dashboard`.
   Your site will be live at `https://<your-username>.github.io/nse-screener/`.
4. **Turn on Actions**: repo Settings → Actions → General → allow workflows to run and to push commits
   (needed since the workflow commits `results/data.json` back to the repo).
5. Push. The workflow will now run automatically every weekday at 9:15 AM IST, updating
   `results/data.json`, and your live webpage will pick it up within 5 minutes (auto-refresh built in).
6. To test immediately instead of waiting: go to the repo's **Actions** tab → "Daily NSE Screener" →
   **Run workflow** button.

## Alerts (Telegram + email)

The screener can now ping you directly when a stock's score crosses a threshold
(default: 75/100, only among the top 10). Both channels are optional and independent
— set up one, both, or neither.

### Telegram (recommended — instant, free, no app passwords)
1. In Telegram, message **@BotFather** → `/newbot` → follow the prompts → it gives you a **bot token**.
2. Message your new bot anything (e.g. "hi") so it can message you back.
3. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser — find `"chat":{"id":...}` in
   the response, that's your **chat ID**.
4. Set both as environment variables (locally) or repo Secrets (GitHub Actions):
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

### Email (Gmail)
1. Turn on 2-Step Verification on your Google account, then create an **App Password**
   at myaccount.google.com/apppasswords (search "app passwords" if the link moves).
2. Set as environment variables / repo Secrets: `SCREENER_EMAIL_ADDRESS` (your Gmail),
   `SCREENER_EMAIL_APP_PASSWORD` (the 16-char app password, NOT your normal password),
   and optionally `SCREENER_EMAIL_TO` if you want alerts sent somewhere other than yourself.

### For GitHub Actions
Repo → Settings → Secrets and variables → Actions → **New repository secret** for each
variable above. The workflow file already passes them through — nothing else to edit.

### Tuning
Edit `ALERT_SCORE_THRESHOLD` and `ALERT_TOP_N_ONLY` in `nse_screener.py` to control
sensitivity. Higher threshold = fewer, higher-conviction alerts.

## Extending it

- **Add stocks**: edit `WATCHLIST` in `nse_screener.py`.
- **Track IPOs**: add entries to `IPO_WATCHLIST` in the same file — update manually from NSE/SEBI
  filings, Chittorgarh, or Moneycontrol (no free reliable IPO API exists).
- **Change scoring weights**: edit the `WEIGHTS` dict — e.g. weight momentum higher if you trade
  shorter-term, weight valuation higher if you invest longer-term.
- **Alerts**: already built in — see the "Alerts" section above to hook up Telegram or email.

## Reality check

- This is a rules-based screener, not a prediction engine. No tool can reliably predict short-term
  price moves — markets price in known information almost instantly, which is exactly why "beat the
  market with public ratios alone" rarely works consistently over time.
- News sentiment here is keyword-based (fast, transparent, free) — not true NLP sentiment. Good for
  a directional signal, not gospel.
- Treat the ranked list as a **shortlist to research further**, not a buy list.
