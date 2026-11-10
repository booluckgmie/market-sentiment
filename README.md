# Malaysia Trend Radar

Repo: https://github.com/booluckgmie/market-sentiment

A free, self-updating trend dashboard for Malaysian content creators. Every morning (from 06:17 Malaysia time)
GitHub fetches fresh data, rebuilds the page, commits it back to the repo and Vercel deploys it.
An optional daily brief goes to Telegram.

**Reliability:** GitHub often delays or drops scheduled runs that start exactly on the hour, so the workflow runs at
06:17, 08:47 and 12:17 Malaysia time. A "gate" job lets only the first one do the work each Malaysia day, so you never get
two briefs. If the build fails, the Telegram bot sends a ⚠️ message with a link to the failed run.

**Data:** Google News RSS (Malaysian edition and worldwide), Wikipedia pageviews, Google Trends. No API keys.
All times shown are Malaysia time (MYT, UTC+8).

## How the pieces fit

```
GitHub Actions (daily 07:00 MYT)
   ├─ refresh.py  →  index.html  →  committed to the default branch  →  Vercel deploys it
   └─ notify.py   →  Telegram picture + message (optional)
```

| File | What it does |
|---|---|
| `refresh.py` | Fetches news, Wikipedia and Trends, scores every topic and writes the self-contained `index.html`. |
| `topics.py` | Plain-English description of every topic, in English and Bahasa Malaysia. Used by the dashboard and Telegram. |
| `notify.py` | Builds the Telegram picture and message from the same data. |
| `index.html` | The dashboard. Rebuilt daily by the workflow, so do not edit it by hand. |
| `vercel.json` | Tells Vercel this is a plain static site (no framework, no build). |
| `.github/workflows/daily.yml` | The daily job: build, commit the page, send the brief. |
| `publish.sh` | Optional helper to copy the files into a fresh clone and push. |

Vercel does not run the Python script. It only hosts the finished page. Each daily commit adds about 260 KB to the
repo history, which is fine for years of use.

## What the dashboard shows

Every topic card has its name, a status (Exploding, Heating up, Steady, Cooling down, Too quiet to call),
a **one-line plain-English description of the topic**, a trend line, mood, heat bar and an opportunity rating.
Tap a card for the full sheet: description, chart, forecast, content angles, latest headlines and share / copy buttons.

Three detail levels (switch at the top):

| Level | Adds |
|---|---|
| **Simple** | Name, status, description, one-line summary, heat bar, stars and a content hook. |
| **Standard** | Score chips on each card (Malaysia and worldwide story counts, z-score, tone, opportunity score) and a hero line with the window, scope and number of exploding topics. |
| **Pro** | A technical panel on each card (velocity, confidence, Google Trends and Wikipedia z-scores, 90-day change, search spike) and a data-health line in the hero. |

Other switches: **EN / BM** language and **Malaysia / Global / Both** news scope. The Culture tab pairs imported
aesthetics with local heritage lanes, and **Data & rules** explains every number.

## 1. Push the files to GitHub

From a terminal with git, run `bash publish.sh`. It clones the repo, copies the files in (replacing older versions),
commits and pushes to the default branch. Use your normal git sign-in.
`REPO=git@github.com:booluckgmie/market-sentiment.git bash publish.sh` uses SSH.

No terminal? On github.com open the repo → **Add file → Upload files** and upload `refresh.py`, `topics.py`,
`notify.py`, `requirements.txt`, `vercel.json`, `README.md`, `.gitignore`. Then **Add file → Create new file**, name it
`.github/workflows/daily.yml` (each `/` makes a folder), paste the contents of `daily.yml` and commit.

## 2. First run: GitHub Actions checklist

1. Repo → **Settings → Actions → General**: allow Actions, and under **Workflow permissions** choose **Read and write permissions**.
2. Repo → **Actions** tab → **Daily Trend Radar refresh** → **Run workflow**.
3. Watch the run. A healthy first run looks like this:

| Step | What you should see | Time |
|---|---|---|
| Install dependencies | finishes green | ~1 min |
| Build the page | `news: ~2600 to fetch`, then `fetched 200/…` counting up, `wikipedia: done`, `trends: done`, `origin: 10 trending-now items`, `wrote index.html (62 keywords, 21 days)` | 10 to 30 min the first time, a few minutes after that |
| Commit the refreshed page | a `Daily refresh <date>` commit by `github-actions[bot]` (or `no changes`) | seconds |
| notify job | `Telegram not configured … Nothing sent.` until you add secrets, then `Telegram message sent.` | under 1 min |

**Scheduled runs only fire from the repo's default branch.** Check **Settings → Branches** that the branch holding
`daily.yml` is the default one, otherwise only manual runs work.

## 3. Deploy on Vercel

1. Go to vercel.com → **Add New → Project** → import `booluckgmie/market-sentiment` (allow the GitHub app when asked).
2. Set **Framework Preset** to **Other**. Leave Build Command, Install Command and Output Directory empty. Deploy.
   The root `vercel.json` already sets `"framework": null`, so no build runs and `index.html` is served at `/`.
3. Production Branch is the repo's default branch, which is where the workflow commits the page. Each daily commit redeploys the site.
4. Open your Vercel address, for example `https://market-sentiment.vercel.app`.
5. Optional: in GitHub open **Settings → Secrets and variables → Actions → Variables**, add `PAGE_URL` with that address
   so the Telegram message links to it.
6. New Vercel projects have **Vercel Authentication** on, which asks visitors to sign in. To make the page public, turn it off in
   **Project → Settings → Deployment Protection**.

Vercel's free plan has its own usage terms, so check them if you plan to earn from the page.

## 4. Daily Telegram brief (optional)

Each morning you get two messages:

1. **A picture** of the top 3 picks (name, status, trend line, stories a day, opportunity and a plain-English description of each topic).
2. **A short text** that does not repeat the picture: a greeting with the Malaysia time, then for each pick the newest headline
   (linked), a coverage multiple against its usual pace, the mood of the coverage and a ✍️ content angle that rotates daily.
   It ends with teasers (what is exploding, what is big abroad but quiet in Malaysia, what is only buzzing here) and a link to the dashboard.

Set it up:

1. In Telegram open **@BotFather**, send `/newbot` and copy the **token**.
2. Open your new bot and tap **Start**, then send it any message (for a channel or group, add the bot as an administrator and post there).
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and find `"chat":{"id":…`. A public channel can use `@channelname`.
4. In GitHub: **Settings → Secrets and variables → Actions → New repository secret**: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
5. Optional **Variables**: `PAGE_URL`; `NOTIFY_LANG` = `en`, `ms` or `both`; `NOTIFY_SCOPE` = `both`, `my` or `gl`; `NOTIFY_IMAGE` = `0` for text only.
6. Run the workflow to test.

Keep the token private: only paste it into GitHub Secrets, never into chats or commits. If it leaks, send `/revoke` to @BotFather and update the secret.

Preview without sending: `python refresh.py --data-out brief/data.json` then `python notify.py --data brief/data.json --dry-run`
(also saves the picture as `brief/preview_en.png`).

## Customising

- **Keywords:** edit `KEYWORDS` at the top of `refresh.py`, and add a description for each new one in `topics.py`.
  A topic without a description falls back to its category.
- **Worldwide search terms:** edit `GLOBAL_QUERY`. Give a topic its own Google News expression, or `None` to skip worldwide news for Malaysia-specific topics.
- **Heritage × trend pairings:** edit `PAIRS`.
- **Telegram content angles:** edit `ANGLES` in `notify.py` (one set of lines per category, English and Bahasa Malaysia).
- **Run time:** change the `cron` lines in `daily.yml` (UTC, so Malaysia time minus 8 hours). Keep odd minutes, not `:00`.
- **Faster, Malaysia only:** `python refresh.py --no-global`.
- **Run locally:** `pip install -r requirements.txt`, then `python refresh.py` writes `index.html`.

## Troubleshooting

| Problem | Fix |
|---|---|
| `trends batch … failed` or `429` in the log | Normal. Google Trends rate-limits; the script retries and gaps fill on the next run. |
| Commit step says `Permission denied` or `403` | Repo **Settings → Actions → General → Workflow permissions** → choose **Read and write permissions**. |
| Vercel build is Canceled | The project has an Ignored Build Step set. Clear it in **Settings → Git → Ignored Build Step**. |
| Vercel shows a failed Python build | Set **Framework Preset** to **Other** and clear the build overrides. The root `vercel.json` also forces no framework. |
| Vercel asks for a login | Turn off **Vercel Authentication** in **Settings → Deployment Protection** to make the page public. |
| Page not updating | Open the Actions tab, check the latest run is green. GitHub pauses scheduled runs on public repos after 60 days with no repo activity; any commit or manual run restarts them. |
| Scheduled run never starts | The workflow file must be on the default branch (**Settings → Branches**). GitHub can also delay runs by hours; the backup schedules cover that. Run it by hand from the Actions tab any time. |
| Telegram does nothing | Both secrets must exist. The notify step prints why in its log. If the chat id is empty, message the bot first. |
| NLTK cannot download the VADER lexicon | Run `pip install vaderSentiment` once, then copy its `vader_lexicon.txt` into `nltk_data/sentiment/vader_lexicon/`. (GitHub runners download it fine.) |

Signals are for content timing only, not financial advice.
