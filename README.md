# S&P 500 screen

A private screener for every S&P 500 stock. It shows which stocks passed six fixed rules on
Jan 1, 2026, how that basket has done since against the S&P 500, which stocks pass today,
and a 2027 buzz column for today's top passers.

A GitHub Action rebuilds the data every weekday night and publishes the page to GitHub Pages.
No API key ever reaches the browser.

## The six rules

A stock passes only if all six hold, using data public on the screen date:

1. Revenue growth over the trailing 12 months is above 10% year over year.
2. EPS over the trailing 12 months is positive and up more than 10%.
3. Operating margin is above 10%.
4. Free cash flow (operating cash flow minus capex) is positive.
5. Net debt / EBITDA is below 3 (net cash passes).
6. The price is above its 200-day moving average.

Passers are ranked by revenue growth plus EPS growth.

Three extra signals are shown next to the rules but never change them: return versus the
S&P 500 over the prior 6 months, the growth trend (trailing 12-month revenue growth versus one
quarter earlier), and a cyclical flag (annual revenue fell 20% or more from an earlier peak in
the past 10 years). Each is computed for Jan 1 and today, so the Biggest gainers tab shows
whether it pointed at this year's winners.

## Data sources

- **Fundamentals:** SEC EDGAR company facts, free with no key. Only filings dated on or before
  the screen date are used.
- **Prices:** your FMP key (up to 240 calls a night). When the budget runs out or a symbol
  fails, it falls back to two keyless sources. Daily closes are cached between runs.
- **2027 buzz:** written on request in a Claude Code session from public news, analyst notes
  and investor discussion, and stored in `buzz.json`. Each nightly build publishes whatever
  is in that file; nothing refreshes it automatically.
- **Insiders:** open-market buys and sells over 90 days, from public SEC Form 4 filings.

## Setup

1. **Create the repo.** On GitHub, create a repo under `razvanm-tech`, for example
   `stock-screener`. Make it public: GitHub Pages on a private repo needs a paid plan, and
   the page URL is public either way. The page tells search engines not to index it.

2. **Upload the files.** Unzip this folder and push it:
   ```
   cd stock-screener
   git init && git add . && git commit -m "Screener"
   git branch -M main
   git remote add origin https://github.com/razvanm-tech/stock-screener.git
   git push -u origin main
   ```

3. **Add two secrets.** Go to Settings, then Secrets and variables, then Actions, and add:
   - `SEC_USER_AGENT`: your name and a real email, e.g. `Jane Doe jane.doe@gmail.com`, with
     no quotes. The SEC requires a contact in every request and blocks placeholder or no-reply
     addresses; `www.sec.gov`, where Form 4 filings live, is stricter than `data.sec.gov`.
   - `FMP_API_KEY`: your Financial Modeling Prep key. Optional: without it, prices come
     from the keyless fallbacks.

   Type keys only into GitHub's secret fields, never into a file or a chat.

4. **Turn on Pages.** In Settings, then Pages, set Source to **GitHub Actions**.

5. **Run it once.** In the Actions tab, open **Nightly build** and click **Run workflow**.
   The first run downloads about 10 years of prices and every company's filings, so it
   takes 20 to 40 minutes. Later runs are shorter.

6. **Open the page** at `https://razvanm-tech.github.io/stock-screener/` and share the link
   with your brother.

## Costs

- **GitHub:** free for a public repo.
- **SEC and price data:** free.
- **Buzz column:** no API costs; it is written in a Claude Code session.

## Settings (optional repo variables or env)

| Name | Default | Meaning |
|---|---|---|
| `SCREEN_START` | `2025-12-31` | Data cutoff for the backtest screen |
| `INSIDER_TOP_N` | `25` | How many top passers get an insider check |
| `INSIDER_MAX_AGE_DAYS` | `7` | How often insider data is refreshed |
| `FMP_DAILY_BUDGET` | `240` | FMP calls per run (free plan allows 250 a day) |

## Known limits

- The backtest uses today's S&P 500 members. Stocks removed during 2026 are missing, which
  flatters the results slightly.
- XBRL tags differ between companies. Banks and insurers often lack an operating income
  tag, so they show "no data" on the margin rule and don't pass.
- Returns are price only, in USD, with no dividends, fees or EUR/USD effect.
- The buzz column shows what was public when it was last written, with that date. It is
  not refreshed automatically.
- Charts use daily closes, so there is no intraday "today" view.

## Run locally

```
pip install -r requirements.txt
python -m unittest discover tests
SEC_USER_AGENT="Your Name you@example.com" python -m pipeline.run
cd site && python -m http.server 8000
```

For personal research only. Nothing here is investment advice.
