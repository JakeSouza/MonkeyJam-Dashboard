# Yahoo Fantasy Football Dashboard

Auto-updating dashboard for a Yahoo fantasy football league, published via
GitHub Pages. The **same code** is used for both Yahoo leagues — only the
`LEAGUE_ID` secret differs between the two repos.

## One-time OAuth setup (Yahoo requires it)

1. Create a Yahoo Developer app at https://developer.yahoo.com/apps/ and grab
   the **Consumer Key** and **Consumer Secret**. If you haven't, apply for
   Fantasy Sports API access at https://sports.yahoo.com/developer/access/.
2. On your own machine (not CI), with `yfpy` installed, generate a token:
   ```bash
   pip install yfpy
   export YFPY_CONSUMER_KEY="..."; export YFPY_CONSUMER_SECRET="..."
   export LEAGUE_ID="123456"; export YFPY_BROWSER_AUTH=1
   python yahoo_dashboard.py      # a browser opens; click Allow
   ```
   This writes a token `.env` file into `.auth/`.
3. Add these **GitHub Secrets** to the repo:
   - `LEAGUE_ID` — numeric Yahoo league id (in your league URL)
   - `YFPY_CONSUMER_KEY`, `YFPY_CONSUMER_SECRET`
   - `YFPY_ENV_FILE` — paste the full contents of the `.auth/*.env` file
     produced in step 2 (this is what the workflow restores in CI)
   - optional `HISTORY_START_YEAR` (default 2018)
4. Enable **GitHub Pages** (Settings → Pages → main / root).
5. Run the **Update Dashboard** action once; it commits `index.html` and Pages
   serves it.

> Because Yahoo uses OAuth with a refresh token, you only do the browser
> handshake once locally; CI uses the cached token to renew automatically.

## Co-managers

Yahoo returns all managers (including co-managers) in each team's `managers`
list. Every one is pulled and joined with ` & `. Yahoo only exposes the
manager *nickname* (display name) — not first/last name — so that's what's
shown.