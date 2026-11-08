# Malaysia Trend Radar

Repo: https://github.com/booluckgmie/market-sentiment

A free, self-updating trend dashboard for Malaysian content creators. Every morning (07:00 Malaysia time)
GitHub fetches fresh data, rebuilds the page and hands it to Vercel. Optional daily Telegram brief.

**Data:** Google News RSS (Malaysian edition and worldwide), Wikipedia pageviews, Google Trends. No API keys.

## How the pieces fit

```
GitHub Actions (daily)  →  rebuilds `listener_live.html`  →  commits it back to this branch
                                                       →  Vercel sees the push and deploys it
                         →  notify.py  →  Telegram (optional)
```
Vercel does not run the Python script itself. It only hosts the finished page. The daily commit adds about 260 KB to the repo history, which is fine for years of use.
(Older steps below that mention a `vercel-site` branch no longer apply.)

## 1. Push the files to GitHub

Easiest, from a terminal with git: unzip the package and run `bash publish.sh`. It clones the repo, copies the files in
(replacing older versions), commits and pushes to the default branch. Use your normal git sign-in.
`REPO=git@github.com:booluckgmie/market-sentiment.git bash publish.sh` uses SSH.

No terminal? On github.com open the repo → **Add file → Upload files** and upload `refresh.py`, `notify.py`,
`requirements.txt`, `vercel.json`, `README.md`, `.gitignore`. Then **Add file → Create new file**, name it
`.github/workflows/daily.yml` (each `/` makes a folder), paste the contents of `daily.yml` and commit.

## 2. First run: GitHub Actions checklist

1. Repo → **Settings → Actions → General**: make sure Actions are allowed.
2. Repo → **Actions** tab → **Daily Trend Radar refresh** → **Run workflow**.
3. Watch the run. A healthy first run looks like this:

| Step | What you should see | Time |
|---|---|---|
| Install dependencies | finishes green | ~1 min |
| Build the page | `news: ~2600 to fetch`, then `fetched 200/…` counting up, `wikipedia: done`, `trends: done`, `origin: 10 trending-now items`, `wrote site/index.html (62 keywords, 21 days)` | 10 to 30 min the first time, a few minutes after that |
| Publish to the vercel-site branch | ends with `[new branch] vercel-site -> vercel-site` (later runs say `forced update`) | seconds |
| notify job | `Telegram not configured … Nothing sent.` until you add secrets, then `Telegram message sent.` | under 1 min |

4. Back on the repo's main page, a new branch **vercel-site** now exists with two files: `index.html` and `vercel.json`.

If something fails, see **Troubleshooting** at the bottom.

## 3. Deploy on Vercel

1. Go to vercel.com → **Add New → Project** → import `booluckgmie/market-sentiment` (allow the GitHub app when asked).
2. Set **Framework Preset** to **Other**. Leave Build Command and Output Directory empty. Tap **Deploy**.
   The first deployment of the default branch is skipped on purpose (the root `vercel.json` says `"ignoreCommand": "exit 0"`),
   so a status of **Canceled** is normal.
3. In the project open **Settings → Environments → Production → Branch Tracking** and set it to `vercel-site`. Save.
4. In GitHub, run the workflow once more (Actions → Run workflow). Vercel deploys within a minute.
5. Open your Vercel address, for example `https://market-sentiment.vercel.app`.
6. Optional: in GitHub open **Settings → Secrets and variables → Actions → Variables**, add `PAGE_URL` with that address
   so the Telegram message links to it.

From now on it refreshes by itself every day. Vercel's free plan has its own usage terms, so check them if you plan to earn from the page.

## 4. Daily Telegram brief (optional)

A picture of the top 3 picks, then a text brief with breakouts and the global-vs-local signals.

1. In Telegram open **@BotFather**, send `/newbot` and copy the **token**.
2. Message your new bot (for a channel or group, add the bot as an administrator).
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and find `"chat":{"id":…`. A public channel can use `@channelname`.
4. In GitHub: **Settings → Secrets and variables → Actions → New repository secret**: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
5. Optional **Variables**: `NOTIFY_LANG` = `en`, `ms` or `both`; `NOTIFY_SCOPE` = `both`, `my` or `gl`; `NOTIFY_IMAGE` = `0` for text only.
6. Run the workflow to test. Keep the token private: only paste it into GitHub Secrets.

Preview without sending: `python refresh.py --data-out brief/data.json` then `python notify.py --data brief/data.json --dry-run`.

## Customising

- **Keywords:** edit `KEYWORDS` at the top of `refresh.py`.
- **Worldwide search terms:** edit `GLOBAL_QUERY`. Give a topic its own Google News expression, or `None` to skip worldwide news for Malaysia-specific topics.
- **Heritage × trend pairings:** edit `PAIRS`.
- **Run time:** change the `cron` line in `daily.yml` (UTC, so Malaysia time minus 8 hours).
- **Faster, Malaysia only:** `python refresh.py --no-global`.
- The page has built-in EN / BM, Simple / Standard / Pro and Malaysia / Global / Both switches.

## Troubleshooting

| Problem | Fix |
|---|---|
| `trends batch … failed` or `429` in the log | Normal. Google Trends rate-limits; the script retries and gaps fill on the next run. |
| Push step says `Permission denied` or `403` | Repo **Settings → Actions → General → Workflow permissions** → choose **Read and write permissions**. |
| Vercel shows 404 | Production Branch is not `vercel-site` yet (step 3), or the workflow has not created that branch. |
| Vercel shows a failed build on the default branch | Check the root `vercel.json` exists. Or set **Settings → Git → Ignored Build Step** to `if [ "$VERCEL_GIT_COMMIT_REF" = "vercel-site" ]; then exit 1; else exit 0; fi`. |
| Page not updating | Open the Actions tab, check the latest run is green. GitHub pauses scheduled runs on public repos after 60 days with no repo activity; any commit or manual run restarts them. |
| Telegram does nothing | Both secrets must exist. The notify step prints why in its log. |

Signals are for content timing only, not financial advice.
