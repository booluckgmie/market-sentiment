#!/usr/bin/env python3
"""
refresh.py — one-command refresh for the MY Trend Listener page.

    pip install feedparser requests nltk trendspy
    python refresh.py                  # writes index.html (21 days)
    python refresh.py --days 30 --out docs/index.html
    python refresh.py --no-trends      # skip Google Trends (faster, avoids rate limits)

What it does
  1. Google News RSS (Malaysia edition): headline count per keyword per day
  2. Wikipedia pageviews + Google Trends (geo MY) as extra demand signals
  3. VADER sentiment on the real headlines
  4. Embeds everything into a self-contained HTML dashboard

Results are cached in cache.json, so re-runs only fetch new days.
Edit KEYWORDS below to track your own topics. Content-timing signal only.

Schedule daily (cron):   0 7 * * *  cd /path/to/folder && python refresh.py
"""
import argparse, datetime as dt, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import feedparser, requests

# ---------------- EDIT ME ----------------
KEYWORDS = [  # (keyword, domain, pipeline)  N=news/ops, A=local culture, B=inbound aesthetic
    ("Melaka polls", "Politics", "N"), ("Budget 2027", "Politics", "N"), ("Najib", "Politics", "N"),
    ("MCMC", "Politics", "N"), ("rural projects", "Politics", "N"), ("Online Safety Act", "Politics", "N"),
    ("subsidy", "Politics", "N"), ("SST", "Politics", "N"),
    ("OPR", "Economy", "N"), ("Bursa", "Economy", "N"), ("ringgit", "Economy", "N"), ("Yinson", "Economy", "N"),
    ("ViTrox", "Economy", "N"), ("palm oil", "Economy", "N"), ("EPF", "Economy", "N"), ("cost of living", "Economy", "N"),
    ("hyperscale data centre", "Tech & Infra", "N"), ("data centre", "Tech & Infra", "N"), ("AI", "Tech & Infra", "N"),
    ("Google Malaysia", "Tech & Infra", "N"), ("Mutiara Line", "Tech & Infra", "N"), ("ECRL", "Tech & Infra", "N"),
    ("Petronas", "Energy", "N"), ("Petron", "Energy", "N"),
    ("scam", "Safety & Health", "N"), ("mule account", "Safety & Health", "N"), ("vape", "Safety & Health", "N"),
    ("banjir", "Safety & Health", "N"), ("dengue", "Safety & Health", "N"), ("haze", "Safety & Health", "N"),
    ("Daiso", "Society", "N"),
    # Cyber security (cyber attacks, breaches, online fraud). Malaysian outlets write "cyberattack" as one word.
    ("cyberattack", "Cyber Security", "N"), ("data breach", "Cyber Security", "N"), ("cybersecurity", "Cyber Security", "N"),
    ("hackers", "Cyber Security", "N"), ("cybercrime", "Cyber Security", "N"), ("online fraud", "Cyber Security", "N"),
    ("ransomware", "Cyber Security", "N"), ("phishing", "Cyber Security", "N"), ("malware", "Cyber Security", "N"),
    ("deepfake", "Cyber Security", "N"), ("data leak", "Cyber Security", "N"), ("NACSA", "Cyber Security", "N"),
    ("Bank Rakyat", "Cyber Security", "N"),
    # Pipeline A — local culture supply (spec section 3A / 6)
    ("songket", "Culture (local)", "A"), ("batik", "Culture (local)", "A"), ("kebaya", "Culture (local)", "A"),
    ("tenun", "Culture (local)", "A"), ("wayang kulit", "Culture (local)", "A"), ("wau", "Culture (local)", "A"),
    ("Kraftangan", "Culture (local)", "A"), ("Ilham Gallery", "Culture (local)", "A"),
    # Pipeline B — inbound aesthetic / fandom demand (spec section 3B / 6)
    ("quiet luxury", "Inbound", "B"), ("old money", "Inbound", "B"), ("coquette", "Inbound", "B"),
    ("streetwear", "Inbound", "B"), ("Y2K", "Inbound", "B"), ("mob wife", "Inbound", "B"),
    ("K-pop", "Inbound", "B"), ("Premier League", "Inbound", "B"), ("F1", "Inbound", "B"), ("Thai BL", "Inbound", "B"),
]
# Worldwide search term per keyword (used when news scope includes Global).
#   not listed -> searches the keyword itself, e.g. "hackers"
#   a string   -> your own Google News search expression (supports OR, quotes and -exclusions)
#   None       -> Malaysia-specific term, so it is skipped for worldwide news
GLOBAL_QUERY = {
    "Bank Rakyat": '"Bank Rakyat" -Indonesia -BRI -Indonesian',   # drops Bank Rakyat Indonesia
    "OPR": '"interest rate decision" central bank',               # global proxy for Bank Negara's OPR
    "Bursa": '"Bursa Malaysia"',                                   # Bursa is also a Turkish city
    "data centre": '"data center" OR "data centre"',               # US spelling
    "hyperscale data centre": '"hyperscale data center" OR "hyperscale data centre"',
    "banjir": 'floods "Southeast Asia"',                           # Malay word for flood
    "subsidy": '"fuel subsidy" OR "energy subsidy"',
    "SST": None, "EPF": None, "MCMC": None, "NACSA": None, "Melaka polls": None, "Budget 2027": None,
    "rural projects": None, "ECRL": None, "Mutiara Line": None, "Petron": None, "Google Malaysia": None,
    "wau": None, "tenun": None, "Kraftangan": None, "Ilham Gallery": None,
}
# editorial pairs for the Localization Gap: (inbound aesthetic, local heritage lane, product format). EDIT FREELY.
PAIRS = [("streetwear", "songket", "songket-streetwear"), ("old money", "batik", "batik-tailoring"),
         ("quiet luxury", "tenun", "tenun-accessories"), ("coquette", "kebaya", "kebaya-contemporary")]
# origin dictionaries (spec section 6) - keep growing these
ORIGIN_DICT = {
    "TH": ["youmaniac","mrfanboy","plslove","forcebook","taynewsan","perth","chawarin","geminifourth","bifw","siam paragon","thai bl","bangkok","thailand","thai"],
    "KR": ["nct","jisung","mamamoo","snsd","k-pop","kpop","bts","blackpink","seoul","kospi","korea","korean"],
    "JP": ["naruto","anime","tokyo","nikkei","japan"],
    "GB": ["arsenal","chelsea","liverpool","manchester","tottenham","newcastle","aston villa","west ham","premier league","epl","verstappen","hamilton","norris","piastri","leclerc","ferrari","mclaren","red bull","mercedes","f1","grand prix","england","london"],
    "ID": ["rupiah","persija","jakarta","indonesia"],
    "US": ["nba","suns","magic","warriors","heat","lakers","celtics","nfl","nasdaq","trump","wall street","intel","nvidia","elon musk","apple","tesla"],
    "MY": ["ron95","padu","sst","belanjawan","najib","rafizi","melaka","anthony loke","fahmi fadzil","hannah yeoh","zahid","dewan rakyat","metmalaysia","solat","jumaat","banjir","pdrm","scam","mule account","bursa","jdt","zii jia","songket","batik","kebaya","tenun","wau","wayang kulit","ilham gallery","kraftangan","malaysia","kuala lumpur","petronas","tnb","anwar","umno","bernama"],
}
MALAY_WORDS = set("yang dan di untuk dengan kerana tidak ini itu ke dari akan pada sudah kena boleh mana apa siapa kenapa bila berapa harga terkini hari lagi semua negeri".split())

WIKI = {  # optional keyword -> English Wikipedia page
    "Najib": "Najib_Razak", "Yinson": "Yinson", "ringgit": "Malaysian_ringgit", "Bursa": "Bursa_Malaysia",
    "palm oil": "Palm_oil", "Petronas": "Petronas", "AI": "Artificial_intelligence",
    "dengue": "Dengue_fever", "haze": "Haze", "ECRL": "East_Coast_Rail_Link", "Daiso": "Daiso",
    "EPF": "Employees_Provident_Fund_(Malaysia)", "ransomware": "Ransomware", "phishing": "Phishing", "malware": "Malware", "deepfake": "Deepfake", "cybersecurity": "Computer_security",
    "MCMC": "Malaysian_Communications_and_Multimedia_Commission",
    "songket": "Songket", "batik": "Batik", "kebaya": "Kebaya", "wayang kulit": "Wayang_kulit",
    "K-pop": "K-pop", "Premier League": "Premier_League", "F1": "Formula_One", "streetwear": "Streetwear",
    "Y2K": "Y2K_aesthetic", "quiet luxury": "Quiet_luxury", "coquette": "Coquette_(aesthetic)",
}
# -----------------------------------------

CACHE = "cache.json"
UA = {"User-Agent": "my-listener/1.0 (personal research)"}

def get_sia():
    from nltk.sentiment.vader import SentimentIntensityAnalyzer
    try:
        return SentimentIntensityAnalyzer()
    except LookupError:
        import nltk; nltk.download("vader_lexicon", quiet=True)
        return SentimentIntensityAnalyzer()

def fetch_day(kw, d, glob=False, gq=None):
    """Google News RSS for one keyword and one day. glob=False -> Malaysia edition; glob=True -> worldwide (US English edition)."""
    q = (gq or f'"{kw}"') if glob else f'"{kw}" Malaysia'
    loc = "hl=en-US&gl=US&ceid=US:en" if glob else "hl=en-MY&gl=MY&ceid=MY:en"
    u = ("https://news.google.com/rss/search?q=" + quote(q) +
         f"+after:{d}+before:{d + dt.timedelta(days=1)}&{loc}")
    for _ in range(3):
        try:
            f = feedparser.parse(u)
            if f.entries or getattr(f, "status", 0) == 200:
                return [[e.title, e.link] for e in f.entries]
        except Exception:
            pass
        time.sleep(1.5)
    return None

def wiki_views(page, days):
    u = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
         f"{page}/daily/{days[0]:%Y%m%d}/{days[-1]:%Y%m%d}")
    try:
        r = requests.get(u, headers=UA, timeout=20).json()
        m = {i["timestamp"][:8]: i["views"] for i in r["items"]}
        return [m.get(f"{d:%Y%m%d}", 0) for d in days]
    except Exception:
        return None


def tag_origin(title):
    """Spec section 5: (1) dictionary match, (2) script/language detection, (3) otherwise None. Never guess."""
    t = title.lower()
    for country, words in ORIGIN_DICT.items():
        for w in words:
            if re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", t):
                return country
    if re.search(r"[\u0e00-\u0e7f]", title): return "TH"
    if re.search(r"[\uac00-\ud7af]", title): return "KR"
    if re.search(r"[\u3040-\u30ff]", title): return "JP"
    if re.search(r"[\u4e00-\u9fff]", title): return "CN"
    if len(set(re.findall(r"[a-z]+", t)) & MALAY_WORDS) >= 2: return "MY"
    return None

def trending_now():
    try:
        f = feedparser.parse("https://trends.google.com/trending/rss?geo=MY", request_headers=UA)
        items = [{"t": e.title, "o": tag_origin(e.title), "v": getattr(e, "ht_approx_traffic", "")} for e in f.entries]
        return {"date": dt.date.today().isoformat(), "items": items}
    except Exception:
        return {"date": dt.date.today().isoformat(), "items": []}

TEMPLATE = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Malaysia Trend Radar</title>
<style>
:root{--bg:#f4f6fb;--card:#fff;--ink:#141a26;--mute:#6b7488;--line:#e6e9f2;--acc:#4f46e5;--acc2:#06b6d4;
--up:#16a34a;--down:#ef4444;--warn:#f59e0b;--chip:#eef1f8;--hot:#ff5a3c;--hero1:#4f46e5;--hero2:#06b6d4;--shadow:0 6px 20px #1a237e14}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#0b0f17;--card:#141a26;--ink:#eef1f8;--mute:#9aa4b8;--line:#232b3b;--acc:#818cf8;--acc2:#22d3ee;--up:#4ade80;--down:#f87171;--warn:#fbbf24;--chip:#1c2434;--hot:#ff7a5c;--hero1:#3730a3;--hero2:#0e7490;--shadow:0 6px 20px #0006}}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
body[data-lv=simple] .std,body[data-lv=simple] .pro{display:none!important}
body[data-lv=standard] .pro{display:none!important}
body:not([data-lv=simple]) .sm{display:none!important}
.top{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:14px 16px 6px}
.brand{font-weight:800;font-size:18px;letter-spacing:-.01em}
.brand i{font-style:normal;background:linear-gradient(90deg,var(--hero1),var(--hero2));-webkit-background-clip:text;background-clip:text;color:transparent}
.lv{display:flex;background:var(--chip);border-radius:99px;padding:3px}
.lv button{border:0;background:none;color:var(--mute);font-weight:700;font-size:12px;padding:6px 11px;border-radius:99px}
.lv button.on{background:var(--card);color:var(--ink);box-shadow:0 1px 4px #0002}
.lvhint{padding:0 16px 8px;font-size:12px;color:var(--mute);display:flex;justify-content:space-between;align-items:center;gap:8px}
.lg{display:inline-flex;align-items:center;gap:4px;font-size:11.5px;white-space:nowrap}
.lg button{border:0;background:var(--chip);color:var(--mute);font-weight:800;font-size:11px;padding:4px 9px;border-radius:99px}
.lg button.on{background:var(--ink);color:var(--bg)}
main{padding:0 14px 110px;max-width:720px;margin:0 auto}
.nav{position:fixed;left:0;right:0;bottom:0;display:flex;background:var(--card);border-top:1px solid var(--line);z-index:20;padding:6px 6px calc(6px + env(safe-area-inset-bottom,0px))}
.nav button{flex:1;border:0;background:none;color:var(--mute);font-size:11px;font-weight:700;display:flex;flex-direction:column;align-items:center;gap:1px;padding:4px 0}
.nav button b{font-size:20px;line-height:1.1;filter:grayscale(1);opacity:.7;transition:.2s}
.nav button.on{color:var(--acc)}.nav button.on b{filter:none;opacity:1;transform:translateY(-2px) scale(1.12)}
.hero{position:relative;overflow:hidden;border-radius:22px;padding:18px 18px 16px;color:#fff;background:linear-gradient(135deg,var(--hero1),var(--hero2));box-shadow:var(--shadow);margin-bottom:12px}
.hero:before,.hero:after{content:"";position:absolute;border-radius:50%;background:#ffffff1f}
.hero:before{width:170px;height:170px;right:-50px;top:-60px}.hero:after{width:110px;height:110px;right:50px;bottom:-60px}
.hero h2{margin:0 0 4px;font-size:21px;letter-spacing:-.01em;position:relative}
.hero p{margin:0;font-size:13.5px;opacity:.95;position:relative}
.live{display:inline-flex;align-items:center;gap:6px;font-size:11px;font-weight:700;background:#ffffff26;border-radius:99px;padding:3px 10px;margin-bottom:8px;position:relative}
.dot{width:8px;height:8px;border-radius:50%;background:#4ade80;box-shadow:0 0 0 0 #4ade80aa;animation:pulse 1.8s infinite}
@keyframes pulse{70%{box-shadow:0 0 0 8px #4ade8000}100%{box-shadow:0 0 0 0 #4ade8000}}
.stats3{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:14px;position:relative}
.stats3 div{background:#ffffff22;border-radius:14px;padding:9px 6px;text-align:center}
.stats3 b{display:block;font-size:22px;line-height:1.1}.stats3 span{font-size:10.5px;opacity:.92}
.ticker{overflow:hidden;white-space:nowrap;border-radius:12px;background:var(--card);border:1px solid var(--line);margin-bottom:14px;padding:8px 0}
.ticker div{display:inline-block;animation:tick 70s linear infinite;padding-left:100%}
.ticker:active div{animation-play-state:paused}
.ticker a{color:var(--ink);text-decoration:none;font-size:12.5px;margin-right:28px}
.ticker a span{color:var(--mute)}
@keyframes tick{to{transform:translateX(-100%)}}
h3.sec{display:flex;align-items:center;gap:8px;font-size:15px;margin:18px 2px 8px;letter-spacing:-.01em}
h3.sec small{font-weight:500;color:var(--mute);font-size:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:13px;margin-bottom:10px;box-shadow:var(--shadow);transition:transform .15s}
.card[data-k]:active{transform:scale(.985)}
.row{display:flex;align-items:center;gap:11px}
.grow{flex:1;min-width:0}
.ic{width:42px;height:42px;border-radius:14px;display:grid;place-items:center;font-size:21px;background:var(--chip);flex:none}
.kw{font-weight:800;font-size:16.5px;letter-spacing:-.01em}
.sub{font-size:12.5px;color:var(--mute);margin-top:1px}
.tag{font-size:11.5px;color:var(--mute)}
.pill{display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:800;padding:2px 9px;border-radius:99px;white-space:nowrap;vertical-align:middle}
.BREAKOUT{background:#ff5a3c22;color:var(--hot)}.GROWTH{background:#16a34a22;color:var(--up)}
.BASELINE{background:var(--chip);color:var(--mute)}.FADING{background:#06b6d422;color:var(--acc2)}
.SPARSE{background:var(--chip);color:var(--mute)}
.BREAKOUT.pulseit{animation:glow 2s infinite}
@keyframes glow{50%{box-shadow:0 0 0 5px #ff5a3c22}}
.heat{height:7px;border-radius:4px;margin-top:10px;position:relative;background:linear-gradient(90deg,#38bdf8,#a3e635,#fbbf24,#ff5a3c)}
.heat i{position:absolute;top:-4px;width:15px;height:15px;border-radius:50%;background:var(--card);border:3px solid var(--ink);transform:translateX(-50%)}
.heatl{display:flex;justify-content:space-between;font-size:10px;color:var(--mute);margin-top:3px}
.opp{display:inline-flex;gap:5px;align-items:center;font-size:12px;font-weight:700;margin-top:9px}
.stars{color:var(--warn);letter-spacing:1px}
.draw{stroke-dasharray:1;stroke-dashoffset:1;animation:draw 1.1s .15s ease-out forwards}
@keyframes draw{to{stroke-dashoffset:0}}
.up{opacity:0;animation:fade .5s ease-out forwards;animation-delay:calc(var(--i,0)*70ms)}
@keyframes fade{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.pick{position:relative;overflow:hidden}
.pick .rk{position:absolute;right:12px;top:8px;font-size:40px;font-weight:900;opacity:.08}
.idea{background:var(--chip);border-radius:12px;padding:8px 11px;font-size:13px;margin-top:10px}
.scroll{display:flex;gap:8px;overflow-x:auto;padding:2px 2px 8px;scrollbar-width:none}
.scroll::-webkit-scrollbar{display:none}
.hot{flex:none;display:flex;align-items:center;gap:7px;background:var(--card);border:1px solid var(--line);border-radius:99px;padding:7px 13px;font-weight:700;font-size:13.5px;box-shadow:var(--shadow)}
.mood{height:14px;border-radius:7px;display:flex;overflow:hidden;margin:8px 0}
.mood i{display:block;transition:width .8s}
.chips{display:flex;gap:6px;overflow-x:auto;padding:2px 2px 10px;scrollbar-width:none}
.chip{flex:none;white-space:nowrap;padding:7px 13px;border-radius:99px;border:0;background:var(--card);border:1px solid var(--line);color:var(--ink);font-size:13px;font-weight:600}
.chip.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.ctl{display:flex;gap:8px;margin-bottom:10px}
.ctl input,.ctl select{flex:1;min-width:0;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:12px;padding:10px 12px;font-size:14px}
.ctl select{flex:none}
details{background:var(--card);border:1px solid var(--line);border-radius:16px;margin-bottom:10px;overflow:hidden}
summary{cursor:pointer;padding:13px 14px;font-weight:700;list-style:none;display:flex;gap:9px;align-items:center}
summary::-webkit-details-marker{display:none}
summary:after{content:"＋";margin-left:auto;color:var(--mute)}details[open] summary:after{content:"－"}
details .in{padding:0 14px 14px}
.formula{background:var(--chip);border-radius:10px;padding:9px 10px;font-family:ui-monospace,monospace;font-size:12px;margin:8px 0}
dl{margin:6px 0}dt{font-weight:700;margin-top:8px}dd{margin:0;color:var(--mute);font-size:13px}
.bar{height:8px;background:var(--chip);border-radius:4px;overflow:hidden;margin-top:6px}
.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--acc),var(--acc2));border-radius:4px}
.line{display:flex;align-items:center;gap:8px;padding:6px 0;font-size:13.5px}
.line .grow{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.og{display:inline-block;min-width:28px;text-align:center;font-size:10.5px;font-weight:800;border-radius:6px;padding:1px 4px;background:var(--chip)}
.og.MY{background:#16a34a22;color:var(--up)}.og.NULL{color:var(--mute);border:1px dashed var(--mute);background:none}
.note{border-left:3px solid var(--warn);background:var(--chip);border-radius:0 10px 10px 0;padding:7px 10px;font-size:12.5px;margin:8px 0}
pre{background:var(--chip);border-radius:10px;padding:10px;font-size:11px;overflow:auto;max-height:300px;margin:8px 0}
.btn{border:0;background:var(--acc);color:#fff;border-radius:12px;padding:10px 15px;font-weight:700;font-size:13px}
.sheet{position:fixed;left:0;right:0;bottom:0;max-height:88vh;overflow:auto;background:var(--card);border-radius:24px 24px 0 0;padding:8px 16px 32px;box-shadow:0 -10px 40px #0007;transform:translateY(105%);transition:transform .3s cubic-bezier(.2,.9,.3,1);z-index:30;max-width:720px;margin:0 auto}
.sheet.open{transform:none}
.grab{width:42px;height:5px;border-radius:3px;background:var(--line);margin:4px auto 10px}
.x{float:right;border:0;background:var(--chip);color:var(--ink);border-radius:99px;width:32px;height:32px;font-size:15px}
.bk{position:fixed;inset:0;background:#0006;opacity:0;pointer-events:none;transition:.25s;z-index:25}.bk.on{opacity:1;pointer-events:auto}
.stats4{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin:12px 0}
.stats4 div{background:var(--chip);border-radius:12px;padding:8px 4px;text-align:center}
.stats4 b{display:block;font-size:16px}.stats4 span{font-size:10.5px;color:var(--mute)}
.hl{display:block;padding:9px 0;border-top:1px solid var(--line);color:var(--ink);text-decoration:none;font-size:13.5px}
.hl small{display:block;color:var(--mute);font-size:11.5px}
h4{font-size:12px;margin:16px 0 4px;color:var(--mute);text-transform:uppercase;letter-spacing:.05em}
svg{display:block;width:100%}
p{margin:6px 0}
.foot{text-align:center;color:var(--mute);font-size:11.5px;margin:20px 0 0}
.shr{position:fixed;inset:0;background:#000c;display:none;z-index:40;align-items:center;justify-content:center;padding:14px}
.shr.on{display:flex}
.shin{background:var(--card);border-radius:22px;padding:12px;max-width:430px;width:100%;max-height:96vh;overflow:auto;text-align:center}
.shin img{width:auto;max-width:100%;max-height:64vh;border-radius:14px;display:block;margin:0 auto 12px;box-shadow:var(--shadow)}
.shb{display:flex;gap:8px;justify-content:center;flex-wrap:wrap}
.btn.alt{background:var(--chip);color:var(--ink);text-decoration:none;display:inline-block}
.btn.wide{width:100%;margin-top:2px;margin-bottom:6px;padding:12px;font-size:14px;background:linear-gradient(90deg,var(--hero1),var(--hero2))}
.toast{position:fixed;left:50%;bottom:96px;transform:translate(-50%,20px);background:var(--ink);color:var(--bg);padding:10px 16px;border-radius:99px;font-size:13px;font-weight:700;opacity:0;pointer-events:none;transition:.25s;z-index:60;white-space:nowrap}
.toast.on{opacity:1;transform:translate(-50%,0)}
.cap{width:100%;min-height:150px;margin:0 0 10px;border:1px solid var(--line);background:var(--chip);color:var(--ink);border-radius:12px;padding:10px;font:13px/1.45 system-ui,sans-serif;resize:vertical}
.btnrow{display:flex;gap:8px}.btnrow .btn{flex:1}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}.up{opacity:1}.draw{stroke-dashoffset:0}}
</style>
</head>
<body data-lv="simple">
<div class="top">
  <div class="brand">🇲🇾 Trend <i>Radar</i></div>
  <div class="lv" id="lv">
    <button data-lv="simple" class="on" data-i="lv.simple">Simple</button><button data-lv="standard" data-i="lv.standard">Standard</button><button data-lv="pro" data-i="lv.pro">Pro</button>
  </div>
</div>
<div class="lvhint"><span id="lvhint"></span><span class="lg" id="lg"><span data-i="lang.label">Language</span> <button data-lang="en" class="on">EN</button><button data-lang="ms">BM</button></span></div>
<div class="lvhint" id="scrow"><span data-i="scope.label">News scope</span><span class="lg" id="sc"><button data-sc="my">🇲🇾 <span data-i="scope.my">Malaysia</span></button><button data-sc="gl">🌐 <span data-i="scope.gl">Global</span></button><button data-sc="both" class="on"><span data-i="scope.both">Both</span></button></span></div>
<main id="main"></main>
<div class="toast" id="toast"></div>
<div class="shr" id="shr"></div>
<div class="bk" id="bk"></div>
<div class="sheet" id="sheet"></div>
<nav class="nav" id="nav">
  <button data-t="today" class="on"><b>🏠</b><span data-i="nav.today">Today</span></button>
  <button data-t="explore"><b>🔎</b><span data-i="nav.explore">Explore</span></button>
  <button data-t="culture"><b>🎨</b><span data-i="nav.culture">Culture</span></button>
  <button data-t="data"><b>📚</b><span data-i="nav.data">Data &amp; rules</span></button>
</nav>

<script>
const DATA=__DATA__;
const KW=DATA.kw,DAYS=DATA.days,N=DAYS.length,FULL_DAYS=45;
const $=s=>document.querySelector(s);
const sum=a=>a.reduce((x,y)=>x+y,0),mean=a=>sum(a)/a.length;
const sdev=a=>{const m=mean(a);return Math.sqrt(a.reduce((t,v)=>t+(v-m)**2,0)/(a.length-1))||1};
const zscore=s=>{const b=s.slice(-15,-1);return(s[s.length-1]-mean(b))/sdev(b)};
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt=n=>(n>0?'+':'')+n.toFixed(1);
const dlabel=d=>new Date(d+'T00:00').toLocaleDateString(lang==='ms'?'ms-MY':'en-MY',{day:'numeric',month:'short'});

// ---------- metrics (same rules as the build spec) ----------
// each keyword carries Malaysia (MY) and worldwide (GL) series; "scope" decides which one drives the page
KW.forEach(o=>{o.sMY=o.s;o.sGL=o.gs||o.s.map(()=>0);o.hMY=o.h;o.hGL=o.gh||[];o.sentMY=o.sent;o.sentGL=o.gsent==null?o.sent:o.gsent;
 o.zMY=zscore(o.sMY);o.zGL=zscore(o.sGL);o.avgMY=mean(o.sMY);o.avgGL=mean(o.sGL);o.totalMY=sum(o.sMY);o.totalGL=sum(o.sGL);
 o.lastMY=o.sMY[N-1];o.lastGL=o.sGL[N-1];o.zt=o.t?zscore(o.t):null;o.zw=o.w?zscore(o.w):null;
 o.sig=null;if(o.gs){ // lead indicators: where the world and Malaysia disagree
  if(o.avgGL>=5&&o.zGL>1.5&&o.zMY<.5)o.sig='gl';
  else if(o.avgMY>=5&&o.zMY>1.5&&o.zGL<.5)o.sig='my';
  else if(o.avgMY>=5&&o.zMY>1&&o.zGL>1)o.sig='both'}});
const HAS_GL=KW.some(o=>o.gs);
let scope=HAS_GL?'both':'my',tot=[],tmax=1,rmax=1;
function applyScope(){
 KW.forEach(o=>{
  o.s=scope==='my'?o.sMY:scope==='gl'?o.sGL:o.sMY.map((v,i)=>v+o.sGL[i]);
  o.h=scope==='my'?o.hMY:scope==='gl'?o.hGL:[...o.hMY.slice(0,2),...o.hGL.slice(0,2)];
  o.sent=scope==='my'?o.sentMY:scope==='gl'?o.sentGL:(o.sentMY*o.totalMY+o.sentGL*o.totalGL)/Math.max(o.totalMY+o.totalGL,1)});
 tot=KW.map(o=>sum(o.s));tmax=Math.max(...tot,1);
 KW.forEach((o,i)=>{
  o.total=tot[i];o.avg=mean(o.s);o.last=o.s[N-1];o.vel=o.s[N-1]-o.s[N-2];o.last7=sum(o.s.slice(-7));o.z=zscore(o.s);
  o.sparse=o.avg<5||N<15;o.conf=o.sparse?0:(o.avg>20&&N>=FULL_DAYS?1:.5);
  o.status=o.sparse?'SPARSE':(o.z>2&&o.vel>0)?'BREAKOUT':(o.z>.5&&o.vel>=0)?'GROWTH':(o.z<-1&&o.vel<0)?'FADING':'BASELINE';
  const p7=mean(o.s.slice(-7)),p14=mean(o.s.slice(-21,-7))||.1,r=p7/p14;
  o.dir=r>1.15?'▲':r<.85?'▼':'▬';o.supply=o.total/tmax;
  o.raw=o.sparse?null:clamp(o.z,0,3)/3*(1-o.supply)*(.25+Math.abs(o.sent))});
 rmax=Math.max(...KW.filter(o=>o.raw!=null).map(o=>o.raw),0)||1;
 KW.forEach(o=>o.gap=o.raw==null?null:Math.round(o.raw/rmax*100))}
applyScope();
const byK=k=>KW.find(o=>o.k===k);
const PAIRS=DATA.pairs.map(([ins,loc,label])=>{const a=byK(ins),b=byK(loc),s30=b?Math.round(b.totalMY*30/N):0,g=a&&a.tg!=null?a.tg:null;
 return{aesthetic:label,inbound:ins,local:loc,inbound_growth_pct:g,local_supply_30d:s30,gap:g==null?null:+(Math.max(g,0)/Math.max(s30,1)).toFixed(2)}});

// ---------- plain-language helpers ----------
const EM={'Cyber Security':'🔐','Politics':'🏛️','Economy':'💰','Tech & Infra':'🛰️','Energy':'⛽','Safety & Health':'🛡️','Society':'🛍️','Culture (local)':'🧵','Inbound':'🌏'};
const STEN={BREAKOUT:['Exploding','🔥'],GROWTH:['Heating up','📈'],BASELINE:['Steady','〰️'],FADING:['Cooling down','🧊'],SPARSE:['Too quiet to call','🤫']};
const ST=new Proxy({},{get:(_,k)=>lang==='ms'?STM[k]:STEN[k]});
const pill=o=>`<span class="pill ${o.status} ${o.status==='BREAKOUT'?'pulseit':''}">${ST[o.status][1]} ${ST[o.status][0]}</span>`;
const mood=v=>v<-.15?'😟':v>.15?'😊':'😐';
const dirW=o=>lang==='ms'?(o.dir==='▲'?'meningkat':o.dir==='▼'?'menurun':'stabil'):(o.dir==='▲'?'rising':o.dir==='▼'?'falling':'steady');
const talk=o=>lang==='ms'?(o.avg<1?'kurang daripada 1 berita sehari':`kira-kira ${Math.round(o.avg)} berita sehari`):(o.avg<1?'under 1 story a day':`about ${Math.round(o.avg)} stor${Math.round(o.avg)===1?'y':'ies'} a day`);
let lang='en',curK=null;
const STM={BREAKOUT:['Meletup','🔥'],GROWTH:['Semakin naik','📈'],BASELINE:['Stabil','〰️'],FADING:['Semakin reda','🧊'],SPARSE:['Terlalu senyap','🤫']};
const stX=o=>lang==='ms'?STM[o.status]:ST[o.status];
const dirX=o=>lang==='ms'?(o.dir==='▲'?'meningkat':o.dir==='▼'?'menurun':'stabil'):dirW(o);
const talkX=o=>{const n=Math.round(o.avg);return lang==='ms'?(o.avg<1?'kurang daripada 1 berita sehari':`kira-kira ${n} berita sehari`):talk(o)};
const opp=o=>o.gap==null?null:o.gap>=60?{t:'High',s:'★★★'}:o.gap>=30?{t:'Medium',s:'★★☆'}:{t:'Low',s:'★☆☆'};
const heatPos=o=>clamp((o.z+1.5)/4.5,0,1)*100;
// ---------- interface language (EN / BM) ----------
const I={
en:{
'scope.label':`News scope`,'scope.my':`Malaysia`,'scope.gl':`Global`,'scope.both':`Both`,
'toast.scope.my':`Showing Malaysian news`,'toast.scope.gl':`Showing worldwide news`,'toast.scope.both':`Malaysia + worldwide news`,
'sig.gl':`Big abroad, quiet here`,'sig.my':`Malaysia-only buzz`,'sig.both':`Rising here and abroad`,
'sig.gl.d':`Worldwide coverage is surging but Malaysian outlets have not caught up. Being first to explain it locally is your opening.`,
'sig.my.d':`This is a Malaysia-driven story. Global audiences are not talking about it, so lean into local detail.`,
'sig.both.d':`Coverage is rising both here and abroad. Expect competition, so bring a fresh local angle.`,
't.glob':`🌐 Going global, not yet local`,'t.glob.s':`be first to explain it here`,
't.myonly':`🇲🇾 Malaysia-only buzz`,'t.myonly.s':`local stories the world hasn’t noticed`,
's.vs':`🇲🇾 vs 🌐 coverage`,'s.gq':`Worldwide search used`,'s.nogl':`Malaysia-specific topic, so no worldwide search.`,
'g.scope':`News scope`,'g.scope.d':`Malaysia counts the Malaysian edition of Google News only. Global counts worldwide coverage. Both adds them together, so global stories can dominate busy topics.`,
'nav.today':`Today`,'nav.explore':`Explore`,'nav.culture':`Culture`,'nav.data':`Data & rules`,
'lv.simple':`Simple`,'lv.standard':`Standard`,'lv.pro':`Pro`,'lang.label':`Language`,
'hint.simple':`Plain-language view. Tap any card for ideas.`,'hint.standard':`Adds scores and numbers.`,'hint.pro':`Adds confidence, signals and technical detail.`,
'toast.lang':`Language: English`,'all':`All`,'up':`up`,'down':`down`,
'c.notranked':`not ranked`,'c.tone':`tone`,'c.oppscore':`opportunity score`,'c.vel':`velocity`,'c.conf':`confidence`,
'conf.0':`suppressed`,'conf.1':`full`,'conf.5':`indicative`,'c.spike':`search spike`,'c.quiet':`quiet`,'c.hot':`unusually hot`,
'c.lowcomp':` · low competition + rising`,'c.mood':`mood`,
'opp.high':`High opportunity`,'opp.mid':`Medium opportunity`,'opp.low':`Low opportunity`,
't.live':`Live · updated {gen}`,'t.hero':`What Malaysia is talking about`,
't.herop':`{n} topics are heating up. Biggest mover: <b>{a}</b>. Easiest win for a post: <b>{b}</b>.`,
't.stat1':`topics watched`,'t.stat2':`stories read`,'t.stat3':`heating up`,
't.picks':`Post about this today`,'t.picks.s':`low competition, rising interest`,
't.shareimg':`📤 Share image`,'t.copycap':`📋 Copy caption`,'t.hotnow':`🔥 Hot right now`,
't.mood':`🌡️ Mood of Malaysia`,'t.mood.s':`tone of the headlines`,
't.worried':`{n} worried`,'t.neutral':`{n} neutral`,'t.upbeat':`{n} upbeat`,
't.mw':`Most worried about: {a} · Most upbeat: {b}`,
't.early':`🌱 Early signals`,'t.early.s':`small but climbing`,'t.thisweek':`▲ {n} this week`,
't.foot':`Content-timing signals from public headlines, not financial advice.<br>Want the numbers? Switch to Standard or Pro at the top.`,
's.hot':`🔥 Hottest`,'s.opp':`🎯 Best opportunity`,'s.talk':`📰 Most talked about`,'s.mood':`😟 Most worried`,
'e.title':`🔎 Explore topics`,'e.search':`Search a topic…`,'e.nomatch':`Nothing matches. Try another filter.`,
'e.quiet':`🤫 Too quiet to rank`,
'e.quietp':`Fewer than 5 stories a day. We don’t score these, but a ▲ means it is climbing from a low base.`,
'e.perday':`{v}/day`,'e.rising':`▲ rising`,
'cu.title':`🎨 Local culture × imported trends`,
'cu.intro':`Foreign styles arrive fast. Malaysian heritage coverage is thin. Where those two meet is your content gap.`,
'cu.match':`🧩 Idea matchmaker`,'cu.idea':`Idea: <b>{a}</b>`,'cu.nodataS':`Not enough data`,
'cu.nodata':`Not enough search data for “{a}” yet.`,
'cu.growth':`Searches for “{a}” are <b>{dir} {pct}%</b> over 3 months, while only <b>{n}</b> local stories covered {b} in 30 days.`,
'cu.gapline':`gap score {g} = growth {gr} ÷ local supply {s}`,
'cu.note':`Growth comes from Google searches in Malaysia, not Pinterest. Pairings are editorial suggestions.`,
'cu.waves':`🌏 Imported waves`,'cu.waves.s':`3-month search change`,
'w.none':`No data`,'w.surge':`Surging 🚀`,'w.rise':`Rising 📈`,'w.flat':`Flat 〰️`,'w.fall':`Falling 🧊`,
'cu.craft':`🧵 Local craft coverage`,'cu.craft.s':`fewer stories = open space`,'cu.last':`Stories in the last {n} days.`,
'd.title':`📚 Data & rules`,'d.sub':`Everything technical lives here. You don’t need any of it to use the radar.`,
'd.health':`🩺 Data health`,'d.hist':`History collected: {n} of {m} days needed for full confidence`,
'd.src':`News {a} topics · Wikipedia {b} · Google Trends {c}{f} · Too quiet to rank {d}`,'d.batchfail':` ({n} batch failed)`,
'd.fc':`Forecasts are a simple trend line. Prophet stays locked until 45+ days and 20+ stories a day.`,
'd.read':`📖 How to read the numbers`,
'g.stories':`Stories a day`,'g.stories.d':`Headlines Google News Malaysia lists for the topic each day.`,
'g.z':`Z-score`,'g.z.d':`How unusual yesterday was versus the last 14 days. Above +2 is a spike.`,
'g.mood':`Mood / tone`,'g.mood.d':`Average sentiment of recent headlines, from −1 (worried) to +1 (upbeat). Rough on news text.`,
'g.opp':`Opportunity`,'g.opp.d':`High when interest is rising, few outlets cover it, and emotions run strong.`,
'g.quiet':`Too quiet to call`,'g.quiet.d':`Under 5 stories a day, so any score would be noise.`,
'g.conf':`Indicative vs full`,'g.conf.d':`Full confidence needs 45+ days of history and 20+ stories a day.`,
'd.formulas':`📐 Formulas & status rules`,
'd.f1':`Exploding: z&gt;2 and rising · Heating up: z&gt;0.5, not falling · Cooling: z&lt;−1 and falling`,
'd.f2':`opportunity = norm(z) × (1 − supply) × (0.25 + |tone|)`,
'd.fp':`norm(z) is z clipped to 0–3 then ÷3. Supply is headline share versus the busiest topic. The 0.25 tone floor is my addition because headline sentiment sits near zero.`,
'd.f3':`gap = inbound growth 90d ÷ max(local supply 30d, 1)`,
'd.origin':`🌍 Where do trends come from?`,'d.rules':`⚖️ Rules we follow`,
'd.rulesp':`1. No scraping of Facebook, Instagram or TikTok. 2. No automated WIPO brand-database queries. 3. Reddit free tier is non-commercial. 4. A single day is never presented as a trend. 5. Google Trends and Pinterest scores are relative to the batch they were pulled in. 6. Thin topics are suppressed instead of reporting noisy scores.`,
'd.sources':`🔌 Sources & what is not connected`,
'd.sourcesp':`Connected: Google News RSS (Malaysia edition and worldwide), Wikipedia pageviews, Google Trends (MY), Google Trends trending-now, VADER sentiment. Not connected yet: Pinterest, Xiaohongshu, Bluesky, Reddit, MyIPO trademarks.`,
'd.openq':`Open questions: does Pinterest Trends offer Malaysia in your account? Xiaohongshu proxy cost? Telegram bot as the daily-brief channel?`,
'd.brief':`🧪 Daily brief (JSON)`,'d.copyjson':`Copy JSON`,'d.copied':`Copied`,'d.selected':`Selected: press copy`,
'o.nodata':`No data on this run.`,
'o.tagged':`{a} of {b} trending searches tagged by dictionary and script rules. Untagged items are excluded, never guessed.`,
'o.note':`<b>One day is not a trend.</b> This is a single snapshot ({d}). Wait for 30+ days of your own data before claiming a pattern.`,
's.why':`{why} Coverage: {talk}; yesterday {n} ({dir}).`,'s.last':`last {n} days`,'s.tooearly':`too early to predict`,
's.up':`likely to keep rising`,'s.cool':`likely to cool off`,'s.level':`likely to stay level`,
's.yest':`yesterday`,'s.z':`z-score`,'s.tone':`tone`,'s.opp':`opportunity`,
's.how':`💡 How to post it`,'s.bestfmt':`Best format`,'s.signals':`Signals`,'s.gt':`Google Trends`,'s.wiki':`Wikipedia views`,
's.tg':`Trends 90d change`,'s.nodata':`{nm}: no data`,'s.pipe':`Pipeline {p} · confidence {c} · velocity {v}{lock}`,
's.lock':` · outlook locked (thin series)`,'s.latest':`📰 Latest stories`,'s.nostories':`No stories in this window.`,
'sh.a1':`Sorry, could not draw the image on this device.`,'sh.a2':`Could not create the image.`,'sh.alt':`Share image preview`,'sh.cap':`Caption`,
'sh.share':`📤 Share`,'sh.copy':`📋 Copy caption`,'sh.save':`⬇ Save image`,'sh.close':`Close`,
'sh.tip':`Story size (1080×1920). Edit the caption before copying. If Share doesn’t open, press and hold the image to save it.`,
'toast.copied':`Caption copied ✓`,'toast.blocked':`Copy blocked: open Share and copy from the box`
},
ms:{
'scope.label':`Skop berita`,'scope.my':`Malaysia`,'scope.gl':`Global`,'scope.both':`Kedua-duanya`,
'toast.scope.my':`Memaparkan berita Malaysia`,'toast.scope.gl':`Memaparkan berita seluruh dunia`,'toast.scope.both':`Berita Malaysia + seluruh dunia`,
'sig.gl':`Besar di luar negara, senyap di sini`,'sig.my':`Hangat di Malaysia sahaja`,'sig.both':`Meningkat di sini dan di luar negara`,
'sig.gl.d':`Liputan global sedang melonjak tetapi media Malaysia belum mengikutinya. Menjadi yang pertama menerangkannya di sini ialah peluang anda.`,
'sig.my.d':`Ini cerita yang didorong Malaysia. Khalayak global tidak membincangkannya, jadi tumpukan pada butiran tempatan.`,
'sig.both.d':`Liputan meningkat di sini dan di luar negara. Jangkakan persaingan, jadi bawa sudut tempatan yang segar.`,
't.glob':`🌐 Mendunia, belum tempatan`,'t.glob.s':`jadilah yang pertama menerangkannya di sini`,
't.myonly':`🇲🇾 Hangat di Malaysia sahaja`,'t.myonly.s':`cerita tempatan yang belum disedari dunia`,
's.vs':`Liputan 🇲🇾 lawan 🌐`,'s.gq':`Carian global yang digunakan`,'s.nogl':`Topik khusus Malaysia, jadi tiada carian global.`,
'g.scope':`Skop berita`,'g.scope.d':`Malaysia hanya mengira edisi Malaysia Google News. Global mengira liputan seluruh dunia. Kedua-duanya menambah kedua-duanya, jadi cerita global boleh mendominasi topik sibuk.`,
'nav.today':`Hari ini`,'nav.explore':`Terokai`,'nav.culture':`Budaya`,'nav.data':`Data & peraturan`,
'lv.simple':`Mudah`,'lv.standard':`Standard`,'lv.pro':`Pro`,'lang.label':`Bahasa`,
'hint.simple':`Paparan bahasa mudah. Ketik mana-mana kad untuk idea.`,'hint.standard':`Tambah skor dan nombor.`,'hint.pro':`Tambah tahap keyakinan, isyarat dan butiran teknikal.`,
'toast.lang':`Bahasa: Bahasa Malaysia`,'all':`Semua`,'up':`naik`,'down':`turun`,
'c.notranked':`tidak diberi ranking`,'c.tone':`nada`,'c.oppscore':`skor peluang`,'c.vel':`halaju`,'c.conf':`keyakinan`,
'conf.0':`dikecualikan`,'conf.1':`penuh`,'conf.5':`petunjuk`,'c.spike':`lonjakan carian`,'c.quiet':`senyap`,'c.hot':`luar biasa panas`,
'c.lowcomp':` · saingan rendah + meningkat`,'c.mood':`emosi`,
'opp.high':`Peluang tinggi`,'opp.mid':`Peluang sederhana`,'opp.low':`Peluang rendah`,
't.live':`Langsung · dikemas kini {gen}`,'t.hero':`Apa yang rakyat Malaysia bualkan`,
't.herop':`{n} topik semakin hangat. Pergerakan terbesar: <b>{a}</b>. Paling mudah untuk disiarkan: <b>{b}</b>.`,
't.stat1':`topik dipantau`,'t.stat2':`berita dibaca`,'t.stat3':`semakin hangat`,
't.picks':`Siarkan tentang ini hari ini`,'t.picks.s':`saingan rendah, minat meningkat`,
't.shareimg':`📤 Kongsi imej`,'t.copycap':`📋 Salin kapsyen`,'t.hotnow':`🔥 Sedang hangat`,
't.mood':`🌡️ Emosi Malaysia`,'t.mood.s':`nada tajuk berita`,
't.worried':`{n} bimbang`,'t.neutral':`{n} neutral`,'t.upbeat':`{n} positif`,
't.mw':`Paling bimbang tentang: {a} · Paling positif: {b}`,
't.early':`🌱 Isyarat awal`,'t.early.s':`kecil tetapi meningkat`,'t.thisweek':`▲ {n} minggu ini`,
't.foot':`Isyarat masa kandungan daripada tajuk berita awam, bukan nasihat kewangan.<br>Mahu angka? Tukar ke Standard atau Pro di atas.`,
's.hot':`🔥 Paling hangat`,'s.opp':`🎯 Peluang terbaik`,'s.talk':`📰 Paling dibualkan`,'s.mood':`😟 Paling bimbang`,
'e.title':`🔎 Terokai topik`,'e.search':`Cari topik…`,'e.nomatch':`Tiada padanan. Cuba penapis lain.`,
'e.quiet':`🤫 Terlalu senyap untuk diranking`,
'e.quietp':`Kurang 5 berita sehari. Kami tidak menilai ini, tetapi ▲ bermaksud ia naik dari paras rendah.`,
'e.perday':`{v}/hari`,'e.rising':`▲ meningkat`,
'cu.title':`🎨 Budaya tempatan × tren luar`,
'cu.intro':`Gaya luar tiba dengan pantas. Liputan warisan Malaysia masih nipis. Di situlah jurang kandungan anda.`,
'cu.match':`🧩 Pemadan idea`,'cu.idea':`Idea: <b>{a}</b>`,'cu.nodataS':`Data tidak mencukupi`,
'cu.nodata':`Data carian untuk “{a}” belum mencukupi.`,
'cu.growth':`Carian untuk “{a}” <b>{dir} {pct}%</b> dalam 3 bulan, sedangkan hanya <b>{n}</b> berita tempatan melaporkan {b} dalam 30 hari.`,
'cu.gapline':`skor jurang {g} = pertumbuhan {gr} ÷ bekalan tempatan {s}`,
'cu.note':`Pertumbuhan datang daripada carian Google di Malaysia, bukan Pinterest. Pasangan idea ialah cadangan editorial.`,
'cu.waves':`🌏 Gelombang luar`,'cu.waves.s':`perubahan carian 3 bulan`,
'w.none':`Tiada data`,'w.surge':`Melonjak 🚀`,'w.rise':`Meningkat 📈`,'w.flat':`Mendatar 〰️`,'w.fall':`Menurun 🧊`,
'cu.craft':`🧵 Liputan kraf tempatan`,'cu.craft.s':`kurang berita = ruang terbuka`,'cu.last':`Berita dalam {n} hari lepas.`,
'd.title':`📚 Data & peraturan`,'d.sub':`Semua perkara teknikal ada di sini. Anda tidak perlu semuanya untuk menggunakan radar ini.`,
'd.health':`🩺 Kesihatan data`,'d.hist':`Sejarah dikumpul: {n} daripada {m} hari diperlukan untuk keyakinan penuh`,
'd.src':`Berita {a} topik · Wikipedia {b} · Google Trends {c}{f} · Terlalu senyap untuk diranking {d}`,'d.batchfail':` ({n} kelompok gagal)`,
'd.fc':`Ramalan hanyalah garis trend ringkas. Prophet dikunci sehingga 45+ hari dan 20+ berita sehari.`,
'd.read':`📖 Cara membaca angka`,
'g.stories':`Berita sehari`,'g.stories.d':`Tajuk berita yang disenaraikan Google News Malaysia untuk topik itu setiap hari.`,
'g.z':`Skor-Z`,'g.z.d':`Betapa luar biasanya semalam berbanding 14 hari lepas. Melebihi +2 ialah lonjakan.`,
'g.mood':`Emosi / nada`,'g.mood.d':`Purata sentimen tajuk terkini, dari −1 (bimbang) hingga +1 (positif). Kurang tepat untuk teks berita.`,
'g.opp':`Peluang`,'g.opp.d':`Tinggi apabila minat meningkat, sedikit media melaporkannya dan emosi kuat.`,
'g.quiet':`Terlalu senyap`,'g.quiet.d':`Kurang 5 berita sehari, jadi sebarang skor hanyalah bunyi bising.`,
'g.conf':`Petunjuk lawan penuh`,'g.conf.d':`Keyakinan penuh memerlukan 45+ hari sejarah dan 20+ berita sehari.`,
'd.formulas':`📐 Formula & peraturan status`,
'd.f1':`Meletup: z&gt;2 dan meningkat · Semakin naik: z&gt;0.5, tidak menurun · Semakin reda: z&lt;−1 dan menurun`,
'd.f2':`peluang = norm(z) × (1 − bekalan) × (0.25 + |nada|)`,
'd.fp':`norm(z) ialah z dihadkan 0–3 kemudian ÷3. Bekalan ialah bahagian tajuk berita berbanding topik paling sibuk. Lantai nada 0.25 ialah tambahan saya kerana sentimen tajuk berita hampir sifar.`,
'd.f3':`jurang = pertumbuhan luar 90h ÷ maks(bekalan tempatan 30h, 1)`,
'd.origin':`🌍 Dari mana datangnya tren?`,'d.rules':`⚖️ Peraturan yang kami ikut`,
'd.rulesp':`1. Tiada scraping Facebook, Instagram atau TikTok. 2. Tiada pertanyaan automatik pangkalan data jenama WIPO. 3. Peringkat percuma Reddit adalah bukan komersial. 4. Satu hari tidak pernah dipersembahkan sebagai tren. 5. Skor Google Trends dan Pinterest adalah relatif kepada kelompok ia ditarik. 6. Topik nipis ditindas dan bukan melaporkan skor yang bising.`,
'd.sources':`🔌 Sumber & apa yang belum disambung`,
'd.sourcesp':`Disambung: Google News RSS (edisi Malaysia dan seluruh dunia), paparan halaman Wikipedia, Google Trends (MY), Google Trends sedang trending, sentimen VADER. Belum disambung: Pinterest, Xiaohongshu, Bluesky, Reddit, tanda dagangan MyIPO.`,
'd.openq':`Soalan terbuka: adakah Pinterest Trends menawarkan Malaysia dalam akaun anda? Kos proksi Xiaohongshu? Bot Telegram sebagai saluran ringkasan harian?`,
'd.brief':`🧪 Ringkasan harian (JSON)`,'d.copyjson':`Salin JSON`,'d.copied':`Disalin`,'d.selected':`Dipilih: tekan salin`,
'o.nodata':`Tiada data untuk larian ini.`,
'o.tagged':`{a} daripada {b} carian trending ditanda mengikut peraturan kamus dan skrip. Item tanpa tanda dikecualikan, tidak pernah diteka.`,
'o.note':`<b>Satu hari bukan tren.</b> Ini hanyalah satu syot ({d}). Tunggu 30+ hari data anda sendiri sebelum mendakwa sesuatu corak.`,
's.why':`{why} Liputan: {talk}; semalam {n} ({dir}).`,'s.last':`{n} hari lepas`,'s.tooearly':`terlalu awal untuk diramal`,
's.up':`berkemungkinan terus naik`,'s.cool':`berkemungkinan reda`,'s.level':`berkemungkinan kekal mendatar`,
's.yest':`semalam`,'s.z':`skor-Z`,'s.tone':`nada`,'s.opp':`peluang`,
's.how':`💡 Cara menyiarkannya`,'s.bestfmt':`Format terbaik`,'s.signals':`Isyarat`,'s.gt':`Google Trends`,'s.wiki':`Paparan Wikipedia`,
's.tg':`Perubahan Trends 90h`,'s.nodata':`{nm}: tiada data`,'s.pipe':`Saluran {p} · keyakinan {c} · halaju {v}{lock}`,
's.lock':` · ramalan dikunci (siri nipis)`,'s.latest':`📰 Berita terkini`,'s.nostories':`Tiada berita dalam tempoh ini.`,
'sh.a1':`Maaf, imej tidak dapat dilukis pada peranti ini.`,'sh.a2':`Imej tidak dapat dicipta.`,'sh.alt':`Pratonton imej untuk dikongsi`,'sh.cap':`Kapsyen`,
'sh.share':`📤 Kongsi`,'sh.copy':`📋 Salin kapsyen`,'sh.save':`⬇ Simpan imej`,'sh.close':`Tutup`,
'sh.tip':`Saiz Story (1080×1920). Edit kapsyen sebelum menyalin. Jika Kongsi tidak terbuka, tekan dan tahan imej untuk menyimpannya.`,
'toast.copied':`Kapsyen disalin ✓`,'toast.blocked':`Salinan disekat: buka Kongsi dan salin dari kotak`
}};
const t=(k,v)=>{let x=(I[lang]&&I[lang][k])||I.en[k]||k;if(v)for(const n in v)x=x.split('{'+n+'}').join(v[n]);return x};
const DN_MS={'Cyber Security':'Keselamatan siber','Politics':'Politik','Economy':'Ekonomi','Tech & Infra':'Teknologi & infrastruktur','Energy':'Tenaga','Safety & Health':'Keselamatan & kesihatan','Society':'Masyarakat','Culture (local)':'Budaya (tempatan)','Inbound':'Pengaruh luar'};
const dn=d=>d==='All'?t('all'):(lang==='ms'?(DN_MS[d]||d):d);
let lvCur='simple';
function applyStatic(){document.querySelectorAll('[data-i]').forEach(el=>{el.textContent=t(el.dataset.i)});
 const h=$('#lvhint');if(h)h.textContent=t('hint.'+lvCur);if(document.documentElement)document.documentElement.lang=lang==='ms'?'ms':'en'}

function spark(s,w=74,h=34,col='var(--acc)'){const m=Math.max(...s,1),st=w/(s.length-1);
 const d=s.map((v,i)=>`${i?'L':'M'}${(i*st).toFixed(1)} ${(h-3-v/m*(h-6)).toFixed(1)}`).join(' ');
 return `<svg viewBox="0 0 ${w} ${h}" style="width:${w}px"><path class="draw" pathLength="1" d="${d}" fill="none" stroke="${col}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>`}
function forecast(s){const y=s.slice(-14),n=y.length,xm=(n-1)/2,ym=mean(y);let nu=0,de=0;y.forEach((v,i)=>{nu+=(i-xm)*(v-ym);de+=(i-xm)**2});
 const b=nu/de,a=ym-b*xm,sd=Math.sqrt(y.reduce((t,v,i)=>t+(v-(a+b*i))**2,0)/(n-2))||1;
 return Array.from({length:7},(_,j)=>{const m=Math.max(0,a+b*(n+j));return{m,lo:Math.max(0,m-1.64*sd),hi:m+1.64*sd}})}
const oppX=o=>{const p=opp(o);if(!p)return null;return{s:p.s,t:lang==='ms'?'Peluang '+({High:'tinggi',Medium:'sederhana',Low:'rendah'})[p.t]:p.t+' opportunity'}};
function hooks(o){const k=o.k,d=o.d,ms=lang==='ms';
 const fEn={'Cyber Security':'“what to do now” safety checklist','Politics':'“What this means for you” explainer','Economy':'“What it means for your wallet” carousel','Tech & Infra':'30-second explainer','Energy':'one-chart explainer','Safety & Health':'safety checklist reel','Society':'relatable story or reaction','Culture (local)':'maker spotlight or behind-the-scenes','Inbound':'local-twist take'};
 const fMs={'Cyber Security':'senarai semak “apa perlu dibuat sekarang”','Politics':'penjelasan “apa maksudnya untuk anda”','Economy':'karusel “kesan pada poket anda”','Tech & Infra':'penjelasan 30 saat','Energy':'penjelasan dengan satu carta','Safety & Health':'reel senarai semak keselamatan','Society':'cerita atau reaksi yang dekat di hati','Culture (local)':'sorotan pengrajin atau di sebalik tabir','Inbound':'ulasan dengan sentuhan tempatan'};
 const f=(ms?fMs:fEn)[d]||(ms?'penjelasan ringkas':'short explainer');
 let h;
 if(o.sparse)h=ms?[`“Ada yang perasan tentang ${k}? Ini yang saya jumpa.”`,`Buat tinjauan ringkas tentang ${k} untuk lihat sama ada orang berminat.`]:[`“Has anyone noticed ${k}? Here’s what I found.”`,`Run a quick poll about ${k} to test if people care.`];
 else if(o.status==='BREAKOUT')h=ms?[`“Semua orang bertanya tentang ${k}. Versi 30 saat.”`,`“${k}: apa yang tidak disebut dalam tajuk berita.”`]:[`“Everyone’s asking about ${k}. The 30-second version.”`,`“${k}: what the headlines are missing.”`];
 else if(o.status==='GROWTH')h=ms?[`“${k} semakin hangat. Ini yang berubah minggu ini.”`,`“3 perkara perlu tahu tentang ${k} sebelum ia tular.”`]:[`“${k} is picking up. Here’s what changed this week.”`,`“3 things to know about ${k} before it blows up.”`];
 else if(o.status==='FADING')h=ms?[`“${k}: situasi terkini.” (rumusan)`,`Panduan sentiasa relevan: “${k}, dijelaskan dengan mudah.”`]:[`“${k}: where things stand now.” (wrap-up)`,`Evergreen guide: “${k}, explained simply.”`];
 else h=ms?[`“${k} dijelaskan dalam bahasa mudah.”`,`“Mitos tentang ${k} yang masih dipercayai ramai.”`]:[`“${k} explained in plain English.”`,`“The ${k} myth people still believe.”`];
 return{h,fmt:f}}
function why(o){
 if(o.sparse)return o.dir==='▲'&&o.last7>=8?`Still small, but climbing from a low base. Worth watching.`:`Very little coverage right now. Too early to call a trend.`;
 const cov=o.supply<.35?'few outlets are covering it':o.supply<.7?'coverage is moderate':'lots of outlets already cover it';
 if(o.status==='BREAKOUT')return `Sharp jump in stories today and ${cov}.`;
 if(o.status==='GROWTH')return `Interest is building, ${cov}.`;
 if(o.status==='FADING')return `Interest is cooling, so go evergreen rather than breaking news.`;
 return `Steady interest, ${cov}.`}

function whyX(o){if(lang!=='ms')return why(o);
 if(o.sparse)return o.dir==='▲'&&o.last7>=8?'Masih kecil, tetapi sedang naik dari paras rendah. Wajar dipantau.':'Liputan sangat sedikit. Terlalu awal untuk menyimpulkan tren.';
 const cov=o.supply<.35?'hanya sedikit media yang melaporkannya':o.supply<.7?'liputan sederhana':'ramai media sudah melaporkannya';
 if(o.status==='BREAKOUT')return `Lonjakan berita yang tajam hari ini dan ${cov}.`;
 if(o.status==='GROWTH')return `Minat semakin meningkat, ${cov}.`;
 if(o.status==='FADING')return 'Minat semakin reda, jadi pilih kandungan sentiasa relevan dan bukan berita tergempar.';
 return `Minat stabil, ${cov}.`}

// ---------- shared card ----------
function card(o,i=0,big=false,rank=1){const op=oppX(o);
 return `<div class="card up ${big?'pick':''}" data-k="${esc(o.k)}" style="--i:${i}">
 ${big?`<div class="rk">${rank}</div>`:''}
 <div class="row"><div class="ic">${EM[o.d]||'📌'}</div>
  <div class="grow"><div class="kw">${esc(o.k)}</div>
   <div style="margin-top:3px">${pill(o)}</div></div>
  ${spark(o.s)}</div>
 <div class="sub">${talk(o)} · ${dirW(o)} ${o.dir} · ${t('c.mood')} ${mood(o.sent)}</div>
 ${o.sig?`<div style="margin-top:6px"><span class="pill BASELINE">${o.sig==='gl'?'🌐':o.sig==='my'?'🇲🇾':'🌐🇲🇾'} ${t('sig.'+o.sig)}</span></div>`:''}
 <div class="std tag" style="margin-top:3px">${HAS_GL&&o.gs?`🇲🇾 ${o.lastMY} · 🌐 ${o.lastGL} · `:''}${o.sparse?t('c.notranked'):`z ${fmt(o.z)} · ${t('c.tone')} ${o.sent.toFixed(2)} · ${t('c.oppscore')} ${o.gap}`}</div>
 <div class="pro tag">${t('c.vel')} ${o.vel>0?'+':''}${o.vel} · ${t('c.conf')} ${t('conf.'+(o.conf===0?'0':o.conf===1?'1':'5'))}${o.zt>2?' · '+t('c.spike'):''}</div>
 ${o.sparse?'':`<div class="heat"><i style="left:${heatPos(o)}%"></i></div><div class="heatl"><span>${t('c.quiet')}</span><span>${t('c.hot')}</span></div>`}
 ${op?`<div class="opp"><span class="stars">${op.s}</span> ${op.t}<span class="sm tag" style="font-weight:500">${t('c.lowcomp')}</span></div>`:''}
 ${big?`<div class="idea">💡 ${esc(hooks(o).h[0])}</div>`:''}
</div>`}

// ---------- TODAY ----------
let tab='today',dom='All',q='',sortBy='hot';
function countUp(){document.querySelectorAll('[data-count]').forEach(el=>{const to=+el.dataset.count;
 if(matchMedia('(prefers-reduced-motion:reduce)').matches){el.textContent=to.toLocaleString();return}
 const t0=performance.now();(function f(t){const p=Math.min(1,(t-t0)/900);el.textContent=Math.round(to*(1-Math.pow(1-p,3))).toLocaleString();if(p<1)requestAnimationFrame(f)})(t0)})}
function getPicks(){const ranked=KW.filter(o=>!o.sparse).sort((a,b)=>b.gap-a.gap),picks=[],seen=new Set();
 ranked.forEach(o=>{if(picks.length<3&&!seen.has(o.d)){picks.push(o);seen.add(o.d)}});
 ranked.forEach(o=>{if(picks.length<3&&!picks.includes(o))picks.push(o)});return picks}
function today(){
 const live=KW.filter(o=>!o.sparse),hotL=live.filter(o=>o.status==='BREAKOUT'||o.status==='GROWTH').sort((a,b)=>b.z-a.z);
 const picks=getPicks();
 const heads=[...KW].sort((a,b)=>b.last7-a.last7).filter(o=>o.h.length).slice(0,14).map(o=>({e:EM[o.d]||'📌',t:o.h[0][1],p:o.h[0][2],u:o.h[0][3]}));
 const tk=heads.map(h=>`<a href="${esc(h.u)}" target="_blank" rel="noopener">${h.e} ${esc(h.t)} <span>· ${esc(h.p)}</span></a>`).join('');
 const neg=live.filter(o=>o.sent<-.15).length,pos=live.filter(o=>o.sent>.15).length,neu=live.length-neg-pos;
 const worried=[...live].sort((a,b)=>a.sent-b.sent)[0],upbeat=[...live].sort((a,b)=>b.sent-a.sent)[0];
 const gl=KW.filter(o=>o.sig==='gl').sort((a,b)=>b.zGL-a.zGL),myo=KW.filter(o=>o.sig==='my').sort((a,b)=>b.zMY-a.zMY);
 const early=KW.filter(o=>o.sparse&&o.dir==='▲'&&o.last7>=8).sort((a,b)=>b.last7-a.last7);
 $('#main').innerHTML=`
 <div class="hero up"><div class="live"><span class="dot"></span> ${t('t.live',{gen:esc(DATA.gen)})}</div>
  <h2>${t('t.hero')}</h2>
  <p>${t('t.herop',{n:hotL.length,a:esc(hotL[0]?hotL[0].k:'–'),b:esc(picks[0]?picks[0].k:'–')})}</p>
  <div class="stats3"><div><b data-count="${KW.length}">0</b><span>${t('t.stat1')}</span></div>
   <div><b data-count="${sum(tot)}">0</b><span>${t('t.stat2')}</span></div>
   <div><b data-count="${hotL.length}">0</b><span>${t('t.stat3')}</span></div></div></div>
 <div class="ticker up" style="--i:1"><div>${tk}${tk}</div></div>
 <h3 class="sec">🎯 ${t('t.picks')} <small>${t('t.picks.s')}</small></h3>
 ${picks.map((o,i)=>card(o,i+2,true,i+1)).join('')}
 <div class="btnrow"><button class="btn wide" id="sharePicks">${t('t.shareimg')}</button><button class="btn wide" id="copyPicks" style="background:var(--ink);color:var(--bg)">${t('t.copycap')}</button></div>
 <h3 class="sec">${t('t.hotnow')}</h3>
 <div class="scroll">${hotL.slice(0,10).map(o=>`<div class="hot" data-k="${esc(o.k)}">${ST[o.status][1]} ${esc(o.k)} <span class="tag">${o.dir}</span></div>`).join('')}</div>
 ${gl.length?`<h3 class="sec">${t('t.glob')} <small>${t('t.glob.s')}</small></h3><div class="scroll">${gl.slice(0,10).map(o=>`<div class="hot" data-k="${esc(o.k)}">🌐 ${esc(o.k)} <span class="tag">${o.lastGL} 🌐 · ${o.lastMY} 🇲🇾</span></div>`).join('')}</div>`:''}
 ${myo.length?`<h3 class="sec">${t('t.myonly')} <small>${t('t.myonly.s')}</small></h3><div class="scroll">${myo.slice(0,10).map(o=>`<div class="hot" data-k="${esc(o.k)}">🇲🇾 ${esc(o.k)} <span class="tag">${o.lastMY} 🇲🇾 · ${o.lastGL} 🌐</span></div>`).join('')}</div>`:''}
 <h3 class="sec">${t('t.mood')} <small>${t('t.mood.s')}</small></h3>
 <div class="card"><div class="mood"><i style="width:${neg/live.length*100}%;background:var(--down)"></i><i style="width:${neu/live.length*100}%;background:var(--line)"></i><i style="width:${pos/live.length*100}%;background:var(--up)"></i></div>
  <div class="tag" style="display:flex;justify-content:space-between"><span>😟 ${t('t.worried',{n:neg})}</span><span>😐 ${t('t.neutral',{n:neu})}</span><span>😊 ${t('t.upbeat',{n:pos})}</span></div>
  <p class="sub">${t('t.mw',{a:`<b data-k="${esc(worried.k)}" style="cursor:pointer">${esc(worried.k)}</b>`,b:`<b data-k="${esc(upbeat.k)}" style="cursor:pointer">${esc(upbeat.k)}</b>`})}</p></div>
 ${early.length?`<h3 class="sec">${t('t.early')} <small>${t('t.early.s')}</small></h3><div class="scroll">${early.map(o=>`<div class="hot" data-k="${esc(o.k)}">${EM[o.d]||'📌'} ${esc(o.k)} <span class="tag">${t('t.thisweek',{n:o.last7})}</span></div>`).join('')}</div>`:''}
 <div class="foot">${t('t.foot')}</div>`;
 countUp()}

// ---------- EXPLORE ----------
const SORTS={hot:['s.hot',(a,b)=>b.z-a.z],opp:['s.opp',(a,b)=>b.gap-a.gap],talk:['s.talk',(a,b)=>b.avg-a.avg],mood:['s.mood',(a,b)=>a.sent-b.sent]};
const doms=['All',...new Set(KW.map(o=>o.d))];
function explore(){
 $('#main').innerHTML=`<h3 class="sec" style="margin-top:6px">${t('e.title')}</h3>
 <div class="ctl"><input id="q" type="search" placeholder="${esc(t('e.search'))}" value="${esc(q)}"><select id="sort">${Object.entries(SORTS).map(([k,v])=>`<option value="${k}" ${k===sortBy?'selected':''}>${t(v[0])}</option>`).join('')}</select></div>
 <div class="chips">${doms.map(d=>`<button class="chip ${d===dom?'on':''}" data-d="${esc(d)}">${EM[d]||'✨'} ${esc(dn(d))}</button>`).join('')}</div>
 <div id="lst"></div>`;fillList()}
function fillList(){
 const pool=KW.filter(o=>(dom==='All'||o.d===dom)&&o.k.toLowerCase().includes(q.toLowerCase()));
 const live=pool.filter(o=>!o.sparse).sort(SORTS[sortBy][1]),sp=pool.filter(o=>o.sparse);
 $('#lst').innerHTML=(live.length?live.map((o,i)=>card(o,Math.min(i,6))).join(''):`<p class="sub">${t('e.nomatch')}</p>`)+
 (sp.length?`<details><summary>${t('e.quiet')} <span class="tag">(${sp.length})</span></summary><div class="in">
 <p class="tag">${t('e.quietp')}</p>
 ${sp.map(o=>`<div class="line" data-k="${esc(o.k)}" style="cursor:pointer"><span>${EM[o.d]||'📌'}</span><span class="grow">${esc(o.k)}</span><span class="tag">${t('e.perday',{v:o.avg.toFixed(1)})} ${o.dir==='▲'&&o.last7>=8?t('e.rising'):''}</span></div>`).join('')}</div></details>`:'')}

// ---------- CULTURE ----------
function culture(){
 const A=KW.filter(o=>o.p==='A').sort((x,y)=>y.totalMY-x.totalMY),B=KW.filter(o=>o.p==='B').sort((x,y)=>(y.tg??-1e9)-(x.tg??-1e9));
 const maxA=Math.max(...A.map(o=>o.totalMY),1),pr=[...PAIRS].sort((x,y)=>(y.gap??-1)-(x.gap??-1));
 const wave=g=>g==null?[t('w.none'),'var(--mute)']:g>100?[t('w.surge'),'var(--hot)']:g>20?[t('w.rise'),'var(--up)']:g>-20?[t('w.flat'),'var(--mute)']:[t('w.fall'),'var(--acc2)'];
 const oppL=g=>g==null?t('cu.nodataS'):g>=3?t('opp.high'):g>=1?t('opp.mid'):t('opp.low');
 $('#main').innerHTML=`<h3 class="sec" style="margin-top:6px">${t('cu.title')}</h3>
 <p class="sub">${t('cu.intro')}</p>
 <h3 class="sec">${t('cu.match')}</h3>
 ${pr.map((p,i)=>`<div class="card up" style="--i:${i}"><div class="row"><div class="ic">✨</div><div class="grow">
  <div class="kw" style="text-transform:capitalize">${esc(p.inbound)} ✕ ${esc(p.local)}</div><div class="sub">${t('cu.idea',{a:esc(p.aesthetic)})}</div></div></div>
  <p class="sub">${p.inbound_growth_pct==null?t('cu.nodata',{a:esc(p.inbound)}):t('cu.growth',{a:esc(p.inbound),dir:p.inbound_growth_pct>=0?t('up'):t('down'),pct:Math.abs(Math.round(p.inbound_growth_pct)),n:p.local_supply_30d,b:esc(p.local)})}</p>
  <div class="opp"><span class="stars">${p.gap==null?'☆☆☆':p.gap>=3?'★★★':p.gap>=1?'★★☆':'★☆☆'}</span> ${oppL(p.gap)}</div>
  <div class="std tag">${t('cu.gapline',{g:p.gap==null?'–':p.gap,gr:p.inbound_growth_pct==null?'–':fmt(p.inbound_growth_pct)+'%',s:p.local_supply_30d})}</div></div>`).join('')}
 <div class="note">${t('cu.note')}</div>
 <h3 class="sec">${t('cu.waves')} <small>${t('cu.waves.s')}</small></h3>
 <div class="card">${B.map(o=>{const w=wave(o.tg);return `<div class="line" data-k="${esc(o.k)}" style="cursor:pointer"><span class="grow"><b>${esc(o.k)}</b></span>${o.t?spark(o.t,56,22,w[1]):''}<span class="tag" style="min-width:86px;text-align:right;color:${w[1]};font-weight:700">${w[0]}</span><span class="std tag" style="min-width:48px;text-align:right">${o.tg==null?'':fmt(o.tg)+'%'}</span></div>`}).join('')}</div>
 <h3 class="sec">${t('cu.craft')} <small>${t('cu.craft.s')}</small></h3>
 <div class="card">${A.map(o=>`<div class="line" data-k="${esc(o.k)}" style="cursor:pointer"><span class="grow">${esc(o.k)}</span><div style="width:90px"><div class="bar"><i style="width:${Math.max(o.totalMY/maxA*100,3)}%"></i></div></div><span class="tag" style="min-width:34px;text-align:right">${o.totalMY}</span></div>`).join('')}
 <p class="tag">${t('cu.last',{n:N})}</p></div>`}

// ---------- DATA & RULES (technical, kept separate) ----------
function briefObj(){const r=KW.filter(o=>!o.sparse);
 const ent=o=>({entity:o.k,category:o.d,z_score:+o.z.toFixed(2),velocity:o.vel,sentiment:o.sent,niche_gap:o.gap,status:o.status,confidence:o.conf});
 return{date:DAYS[N-1],top_breakouts:r.filter(o=>o.status==='BREAKOUT').sort((a,b)=>b.z-a.z).slice(0,5).map(ent),
  top_local:KW.filter(o=>o.p==='A'&&!o.sparse).sort((a,b)=>b.totalMY-a.totalMY).slice(0,5).map(ent),
  top_inbound_aesthetics:KW.filter(o=>o.p==='B'&&o.tg!=null).sort((a,b)=>b.tg-a.tg).slice(0,5).map(o=>({aesthetic:o.k,inbound_growth_90d_pct:o.tg,local_news_mentions:o.totalMY})),
  localization_opportunities:PAIRS.filter(p=>p.gap!=null).sort((a,b)=>b.gap-a.gap),global_vs_local:KW.filter(o=>o.sig).map(o=>({entity:o.k,signal:o.sig==='gl'?'global_wave_not_yet_local':o.sig==='my'?'malaysia_only_buzz':'rising_both',z_malaysia:+o.zMY.toFixed(2),z_global:+o.zGL.toFixed(2)})),suppressed_sparse:KW.filter(o=>o.sparse).map(o=>o.k),
  data_health:{generated:DATA.gen,history_days:N,days_needed_for_full_confidence:FULL_DAYS,
   sources:{google_news_keywords:KW.length,wikipedia_keywords:KW.filter(o=>o.w).length,trends_keywords:KW.filter(o=>o.t).length,trends_failed_batches:DATA.health.trends_failed_batches},
   forecast:'naive linear only; Prophet locked until 45+ days and >20/day',origin_sample_days:1}}}
function originBlock(){const it=DATA.origin.items,tg=it.filter(x=>x.o),by={};tg.forEach(x=>by[x.o]=(by[x.o]||0)+1);
 const keys=Object.keys(by).sort((a,b)=>by[b]-by[a]);if(!it.length)return `<p class="tag">${t('o.nodata')}</p>`;
 return `<p class="tag">${t('o.tagged',{a:tg.length,b:it.length})}</p>
 ${keys.map(c=>`<div class="line"><span class="og ${c}">${c}</span><div class="grow"><div class="bar"><i style="width:${by[c]/tg.length*100}%"></i></div></div><span class="tag">${by[c]}</span></div>`).join('')}
 <div class="note">${t('o.note',{d:dlabel(DATA.origin.date)})}</div>
 ${it.map(x=>`<div class="line"><span class="og ${x.o||'NULL'}">${x.o||'—'}</span><span class="grow">${esc(x.t)}</span><span class="tag">${esc(x.v)}</span></div>`).join('')}`}
function data(){const b=briefObj(),H=b.data_health,pct=Math.min(100,Math.round(N/FULL_DAYS*100));
 $('#main').innerHTML=`<h3 class="sec" style="margin-top:6px">${t('d.title')}</h3>
 <p class="sub">${t('d.sub')}</p>
 <details open><summary>${t('d.health')}</summary><div class="in">
  <p class="tag">${t('d.hist',{n:N,m:FULL_DAYS})}</p><div class="bar"><i style="width:${pct}%"></i></div>
  <p class="tag">${t('d.src',{a:H.sources.google_news_keywords,b:H.sources.wikipedia_keywords,c:H.sources.trends_keywords,f:H.sources.trends_failed_batches?t('d.batchfail',{n:H.sources.trends_failed_batches}):'',d:b.suppressed_sparse.length})}</p>
  <p class="tag">${t('d.fc')}</p></div></details>
 <details><summary>${t('d.read')}</summary><div class="in"><dl>
  <dt>${t('g.stories')}</dt><dd>${t('g.stories.d')}</dd>
  <dt>${t('g.z')}</dt><dd>${t('g.z.d')}</dd>
  <dt>${t('g.mood')}</dt><dd>${t('g.mood.d')}</dd>
  <dt>${t('g.opp')}</dt><dd>${t('g.opp.d')}</dd>
  <dt>${t('g.quiet')}</dt><dd>${t('g.quiet.d')}</dd>
  <dt>${t('g.conf')}</dt><dd>${t('g.conf.d')}</dd><dt>${t('g.scope')}</dt><dd>${t('g.scope.d')}</dd></dl></div></details>
 <details><summary>${t('d.formulas')}</summary><div class="in">
  <div class="formula">${t('d.f1')}</div>
  <div class="formula">${t('d.f2')}</div>
  <p class="tag">${t('d.fp')}</p>
  <div class="formula">${t('d.f3')}</div></div></details>
 <details><summary>${t('d.origin')}</summary><div class="in">${originBlock()}</div></details>
 <details><summary>${t('d.rules')}</summary><div class="in"><p class="tag">${t('d.rulesp')}</p></div></details>
 <details><summary>${t('d.sources')}</summary><div class="in"><p class="tag">${t('d.sourcesp')}</p>
  <p class="tag">${t('d.openq')}</p></div></details>
 <details><summary>${t('d.brief')}</summary><div class="in"><pre id="bj">${esc(JSON.stringify(b,null,1))}</pre><button class="btn" id="cp">${t('d.copyjson')}</button> <span class="tag" id="cpm"></span></div></details>`}

// ---------- detail sheet ----------
const hlist=arr=>arr.length?arr.map(h=>`<a class="hl" href="${esc(h[3])}" target="_blank" rel="noopener">${esc(h[1])}<small>${esc(h[2])} · ${dlabel(h[0])}</small></a>`).join(''):`<p class="sub">${t('s.nostories')}</p>`;
function openK(k){const o=byK(k);if(!o)return;curK=k;const n=o.s.length,canF=!o.sparse&&o.last7>=20,hk=hooks(o);
 const W=340,H=130;let fc=null;if(canF)fc=forecast(o.s);
 const T=n+(fc?7:0),mxv=Math.max(...o.s,...(fc?fc.map(x=>x.hi):[0]),1),st=W/(T-1),y=v=>H-10-v/mxv*(H-22),pt=(v,i)=>`${i?'L':'M'}${(i*st).toFixed(1)} ${y(v).toFixed(1)}`;
 const hist=o.s.map((v,i)=>pt(v,i)).join(' ');let extra='',trend='';
 if(fc){const fl=[o.s[n-1],...fc.map(x=>x.m)].map((v,i)=>pt(v,n-1+i)).join(' ');
  const band=fc.map((x,i)=>`${(( n+i)*st).toFixed(1)},${y(x.hi).toFixed(1)}`).join(' ')+' '+fc.map((x,i)=>`${((n+i)*st).toFixed(1)},${y(x.lo).toFixed(1)}`).reverse().join(' ');
  trend=fc[6].m>o.last*1.1?t('s.up'):fc[6].m<o.last*.9?t('s.cool'):t('s.level');
  extra=`<polygon points="${band}" fill="var(--acc)" opacity=".13"/><path d="${fl}" fill="none" stroke="var(--acc)" stroke-width="2.4" stroke-dasharray="5 4"/>`}
 const sig=(nm,s,z)=>s?`<div class="line"><span style="min-width:110px" class="tag">${nm}</span>${spark(s,110,26)}<b>${fmt(z)}</b><span class="tag">z</span></div>`:`<div class="line"><span class="tag">${t('s.nodata',{nm})}</span></div>`;
 $('#sheet').innerHTML=`<div class="grab"></div><button class="x" data-close>✕</button>
 <div class="row"><div class="ic">${EM[o.d]||'📌'}</div><div class="grow"><div class="kw" style="font-size:20px">${esc(o.k)}</div><div style="margin-top:3px">${pill(o)}</div></div></div>
 <p class="sub" style="margin-top:10px">${esc(t('s.why',{why:whyX(o),talk:talk(o),n:o.last,dir:dirW(o)}))}</p>
 <svg viewBox="0 0 ${W} ${H}">${extra}<path class="draw" pathLength="1" d="${hist}" fill="none" stroke="var(--acc)" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
 <div class="tag" style="display:flex;justify-content:space-between"><span>${t('s.last',{n})}</span><span>${trend?trend:t('s.tooearly')}</span></div>
 <div class="stats4 std"><div><b>${o.last}</b><span>${t('s.yest')}</span></div><div><b>${o.sparse?'–':fmt(o.z)}</b><span>${t('s.z')}</span></div><div><b>${o.sent.toFixed(2)}</b><span>${t('s.tone')}</span></div><div><b>${o.gap==null?'–':o.gap}</b><span>${t('s.opp')}</span></div></div>
 ${HAS_GL&&o.gs?`<h4>${t('s.vs')}</h4>
 <div class="line"><span class="tag" style="min-width:96px">🇲🇾 ${t('scope.my')}</span>${spark(o.sMY,110,26)}<b class="std">${fmt(o.zMY)}</b><span class="tag">${o.lastMY}</span></div>
 <div class="line"><span class="tag" style="min-width:96px">🌐 ${t('scope.gl')}</span>${spark(o.sGL,110,26,'var(--acc2)')}<b class="std">${fmt(o.zGL)}</b><span class="tag">${o.lastGL}</span></div>
 ${o.sig?`<div class="note">${t('sig.'+o.sig+'.d')}</div>`:''}`:''}
 <h4>${t('s.how')}</h4>
 <div class="idea">${hk.h.map(esc).join('<br><br>')}</div>
 <p class="sub">${t('s.bestfmt')}: <b>${esc(hk.fmt)}</b>.</p>
 <div class="btnrow"><button class="btn wide" data-share="${esc(o.k)}">${t('t.shareimg')}</button><button class="btn wide" data-copy="${esc(o.k)}" style="background:var(--ink);color:var(--bg)">${t('t.copycap')}</button></div>
 <div class="pro"><h4>${t('s.signals')}</h4>${sig(t('s.gt'),o.t,o.zt)}${sig(t('s.wiki'),o.w,o.zw)}
  ${o.tg!=null?`<div class="line"><span class="tag">${t('s.tg')}</span><b>${fmt(o.tg)}%</b></div>`:''}
  <div class="note">${t('s.pipe',{p:o.p,c:t('conf.'+(o.conf===0?'0':o.conf===1?'1':'5')),v:(o.vel>0?'+':'')+o.vel,lock:canF?'':t('s.lock')})}</div></div>
 <h4>${t('s.latest')} · 🇲🇾</h4>${hlist(o.hMY)}
 ${HAS_GL&&o.gs?`<h4>${t('s.latest')} · 🌐</h4>${hlist(o.hGL)}${o.gq?`<p class="tag pro">${t('s.gq')}: ${esc(o.gq)}</p>`:''}`:(HAS_GL?`<p class="tag">${t('s.nogl')}</p>`:'')}`;
 $('#sheet').classList.add('open');$('#bk').classList.add('on');$('#sheet').scrollTop=0}
function closeK(){curK=null;$('#sheet').classList.remove('open');$('#bk').classList.remove('on')}



// ---------- captions & hashtags ----------
const DTAGS={'Cyber Security':['#CyberSecurity','#CyberSafetyMY'],'Politics':['#MalaysiaNews','#PolitikMalaysia'],'Economy':['#EkonomiMalaysia','#MalaysiaFinance'],'Tech & Infra':['#TechMalaysia','#MalaysiaInfra'],'Energy':['#EnergyMalaysia'],'Safety & Health':['#WaspadaMalaysia','#KesihatanAwam'],'Society':['#MalaysiaLife'],'Culture (local)':['#BudayaMalaysia','#WarisanMalaysia'],'Inbound':['#TrendMalaysia']};
const topicTag=k=>'#'+k.replace(/[^A-Za-z0-9 ]+/g,'').split(/\s+/).filter(Boolean).map(w=>w[0].toUpperCase()+w.slice(1)).join('');
const DTAGS_MS={'Cyber Security':['#KeselamatanSiber','#WaspadaSiber'],'Politics':['#BeritaMalaysia','#PolitikMalaysia'],'Economy':['#EkonomiMalaysia','#KewanganMalaysia'],'Tech & Infra':['#TeknologiMalaysia','#InfrastrukturMalaysia'],'Energy':['#TenagaMalaysia'],'Safety & Health':['#WaspadaMalaysia','#KesihatanAwam'],'Society':['#GayaHidupMalaysia'],'Culture (local)':['#BudayaMalaysia','#WarisanMalaysia'],'Inbound':['#TrenMalaysia']};
function hashtags(items){const t=new Set(['#Malaysia']);items.forEach(o=>{const tt=topicTag(o.k);if(tt.length>2)t.add(tt);((lang==='ms'?DTAGS_MS:DTAGS)[o.d]||[]).forEach(x=>t.add(x))});return [...t].slice(0,8).join(' ')}
const dnice=()=>new Date(DAYS[N-1]+'T00:00').toLocaleDateString(lang==='ms'?'ms-MY':'en-MY',{day:'numeric',month:'short',year:'numeric'});
function capFor(items){
 const ms=lang==='ms',src=ms?'Sumber: Google News MY, Google Trends, Wikipedia · melalui Malaysia Trend Radar':'Sources: Google News MY, Google Trends, Wikipedia · via Malaysia Trend Radar';
 if(items.length===1){const o=items[0],hk=hooks(o),hook=hk.h[0].replace(/^[“"]|[”"]$/g,'');
  return `${hook}\n\n${whyX(o)} ${ms?'Liputan':'Coverage'}: ${talkX(o)}, ${dirX(o)}.\n\n${ms?'Apa pendapat anda?':'What’s your take?'} 👇\n\n${hashtags(items)}\n${src}`}
 return (ms?`🎯 Apa yang rakyat Malaysia bualkan (berita sehingga ${dnice()})`:`🎯 What Malaysia is talking about (news to ${dnice()})`)+'\n\n'+
  items.map((o,i)=>`${i+1}. ${o.k} ${stX(o)[1]} ${stX(o)[0]} — ${talkX(o)}, ${dirX(o)}\n   💡 ${hooks(o).h[0]}`).join('\n\n')+
  `\n\n${ms?'Mana satu anda mahu bincangkan?':'Which one are you posting about?'} 👇\n\n${hashtags(items)}\n${src}`}
let toastT=null;
function toast(m){const t=$('#toast');t.textContent=m;t.classList.add('on');clearTimeout(toastT);toastT=setTimeout(()=>t.classList.remove('on'),1900)}
function copyText(t){
 const fb=()=>{try{const a=document.createElement('textarea');a.value=t;a.style.cssText='position:fixed;opacity:0;top:0';document.body.appendChild(a);a.select();const ok=document.execCommand('copy');document.body.removeChild(a);toast(ok?t('toast.copied'):t('toast.blocked'))}catch(e){toast(t('toast.blocked'))}};
 if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(t).then(()=>toast(t('toast.copied')),fb);else fb()}

// ---------- share as image (canvas, 1080x1920 Story size) ----------
function rrect(g,x,y,w,h,r){g.beginPath();g.moveTo(x+r,y);g.arcTo(x+w,y,x+w,y+h,r);g.arcTo(x+w,y+h,x,y+h,r);g.arcTo(x,y+h,x,y,r);g.arcTo(x,y,x+w,y,r);g.closePath()}
function wrapLines(g,t,mw,max){const w=String(t).split(/\s+/),out=[];let l='';
 for(const x of w){const tt=l?l+' '+x:x;if(g.measureText(tt).width>mw&&l){out.push(l);l=x}else l=tt}
 if(l)out.push(l);
 if(out.length>max){out.length=max;let s=out[max-1];while(g.measureText(s+'…').width>mw&&s.length>1)s=s.slice(0,-1);out[max-1]=s+'…'}return out}
const PILL={BREAKOUT:['#ffe4de','#e0431f'],GROWTH:['#dcfce7','#15803d'],BASELINE:['#eef1f8','#5b6578'],FADING:['#cffafe','#0e7490'],SPARSE:['#eef1f8','#5b6578']};
function drawShare(items){
 const W=1080,H=1920,c=document.createElement('canvas');c.width=W;c.height=H;const g=c.getContext('2d');
 const F='-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif',B=px=>'bold '+px+'px '+F,R=px=>px+'px '+F;
 const bg=g.createLinearGradient(0,0,W,H);bg.addColorStop(0,'#4338ca');bg.addColorStop(1,'#0891b2');g.fillStyle=bg;g.fillRect(0,0,W,H);
 g.fillStyle='rgba(255,255,255,.09)';g.beginPath();g.arc(W-40,120,320,0,7);g.fill();g.beginPath();g.arc(80,H-260,280,0,7);g.fill();
 g.textBaseline='alphabetic';g.textAlign='left';g.fillStyle='#fff';
 const single=items.length===1;
 g.font=B(38);g.fillText('🇲🇾  TREND RADAR',70,112);
 g.font=B(single?92:84);g.fillText(single?(lang==='ms'?'Topik pilihan':'Topic spotlight'):(lang==='ms'?'Siarkan ini hari ini':'Post about this today'),70,230);
 g.globalAlpha=.92;{const sub=(lang==='ms'?'Berita hingga ':'News to ')+dnice()+(lang==='ms'?' · saingan rendah, minat naik':' · low competition, rising interest');let fs=36;g.font=R(fs);while(g.measureText(sub).width>W-140&&fs>22){fs--;g.font=R(fs)}g.fillText(sub,70,296)}g.globalAlpha=1;
 const top=single?360:350,bottom=H-150,gap=30,n=items.length;
 const ch=Math.min((bottom-top-(n-1)*gap)/n,single?1000:480),cx=50,cw=W-100;
 items.forEach((o,i)=>{
  const y=top+i*(ch+gap),px=cx+48;
  g.shadowColor='rgba(0,0,0,.25)';g.shadowBlur=30;g.shadowOffsetY=10;g.fillStyle='#ffffff';rrect(g,cx,y,cw,ch,44);g.fill();
  g.shadowColor='transparent';g.shadowBlur=0;g.shadowOffsetY=0;
  g.fillStyle='#eef1f8';rrect(g,px,y+36,110,110,34);g.fill();
  g.fillStyle='#000';g.font=R(62);g.textAlign='center';g.fillText(EM[o.d]||'📌',px+55,y+112);
  if(!single){g.fillStyle='#4f46e5';g.beginPath();g.arc(px+6,y+42,26,0,7);g.fill();g.strokeStyle='#fff';g.lineWidth=4;g.stroke();g.fillStyle='#fff';g.font=B(30);g.fillText(String(i+1),px+6,y+53)}
  g.textAlign='left';
  g.fillStyle='#141a26';g.font=B(single?70:62);g.fillText(wrapLines(g,o.k,cw-520,1)[0],px+140,y+88);
  const pc=PILL[o.status],label=stX(o)[1]+' '+stX(o)[0];g.font=B(32);const pw=g.measureText(label).width+44;
  g.fillStyle=pc[0];rrect(g,px+140,y+104,pw,52,26);g.fill();g.fillStyle=pc[1];g.fillText(label,px+162,y+141);
  const sw=210,sh=84,sx=cx+cw-48-sw,sy=y+48,m=Math.max(...o.s,1),st=sw/(o.s.length-1);
  g.strokeStyle='#4f46e5';g.lineWidth=7;g.lineCap='round';g.lineJoin='round';g.beginPath();
  o.s.forEach((v,k)=>{const X=sx+k*st,Y=sy+sh-v/m*sh;k?g.lineTo(X,Y):g.moveTo(X,Y)});g.stroke();
  let yy=y+(single?226:206);
  g.fillStyle='#475069';g.font=R(34);g.fillText(`${talkX(o)} · ${dirX(o)} ${o.dir} · ${lang==='ms'?'emosi':'mood'} ${mood(o.sent)}`,px,yy);yy+=single?62:52;
  const op=oppX(o);
  if(op){g.fillStyle='#f59e0b';g.font=B(38);g.fillText(op.s,px,yy);const w2=g.measureText(op.s+'  ').width;g.fillStyle='#141a26';g.font=B(36);g.fillText(op.t,px+w2,yy);yy+=single?24:10}
  if(single){g.fillStyle='#475069';g.font=R(34);const wl=wrapLines(g,whyX(o),cw-96,3);wl.forEach((t,k)=>g.fillText(t,px,yy+50+k*46));yy+=50+wl.length*46}
  const by=yy+(single?28:16),bh=y+ch-(single?40:26)-by,lh=single?54:46,fs=single?40:34;
  if(bh>90){g.fillStyle='#eef1f8';rrect(g,px,by,cw-96,bh,28);g.fill();
   g.fillStyle='#1e2433';g.font='italic '+R(fs);
   const mw=cw-96-64,hk=hooks(o),cap=Math.max(1,Math.floor((bh-(single?48:30))/lh));
   const hl=single?[...wrapLines(g,'💡 '+hk.h[0],mw,3),'',...wrapLines(g,'💡 '+hk.h[1],mw,3),'',...wrapLines(g,(lang==='ms'?'Format terbaik: ':'Best format: ')+hk.fmt,mw,2)].slice(0,cap):wrapLines(g,'💡 '+hk.h[0],mw,cap);
   hl.forEach((t,k)=>g.fillText(t,px+32,by+(single?62:50)+k*lh))}
 });
 g.fillStyle='#ffffff';g.font=B(32);g.fillText((lang==='ms'?'Dikemas kini ':'Updated ')+DATA.gen,70,H-92);
 g.globalAlpha=.85;g.font=R(28);g.fillText(lang==='ms'?'Google News MY · Trends · Wikipedia · Bukan nasihat kewangan':'Google News MY · Trends · Wikipedia · Not financial advice',70,H-50);g.globalAlpha=1;
 return c}
let shareFile=null,shareUrl=null;
function showShare(items){
 if(!items||!items.length)return;
 let c;try{c=drawShare(items)}catch(e){alert(t('sh.a1'));return}
 const name=items.length===1?'trend-'+items[0].k.toLowerCase().replace(/[^a-z0-9]+/g,'-'):'trend-radar-picks-'+DAYS[N-1];
 c.toBlob(b=>{
  if(!b){alert(t('sh.a2'));return}
  if(shareUrl)URL.revokeObjectURL(shareUrl);
  shareUrl=URL.createObjectURL(b);
  try{shareFile=new File([b],name+'.png',{type:'image/png'})}catch(e){shareFile=null}
  $('#shr').innerHTML=`<div class="shin"><img src="${shareUrl}" alt="${esc(t('sh.alt'))}">
   <textarea class="cap" id="capt" aria-label="${esc(t('sh.cap'))}">${esc(capFor(items))}</textarea>
   <div class="shb"><button class="btn" id="shgo">${t('sh.share')}</button><button class="btn alt" id="shcp">${t('sh.copy')}</button><a class="btn alt" id="shdl" href="${shareUrl}" download="${name}.png">${t('sh.save')}</a><button class="btn alt" data-shclose>${t('sh.close')}</button></div>
   <p class="tag" style="margin-top:8px">${t('sh.tip')}</p></div>`;
  $('#shr').classList.add('on')},'image/png')}
function closeShare(){$('#shr').classList.remove('on')}
function doShare(){
 const cp=$('#capt');const data={files:shareFile?[shareFile]:[],title:'Malaysia Trend Radar',text:cp&&cp.value?cp.value:'Content picks from Malaysia Trend Radar'};
 if(shareFile&&navigator.canShare&&navigator.canShare({files:data.files})){navigator.share(data).catch(()=>{})}
 else{const a=$('#shdl');if(a&&a.click)a.click()}}

// ---------- shell ----------
function render(){document.querySelectorAll('#nav button').forEach(b=>b.classList.toggle('on',b.dataset.t===tab));
 ({today,explore,culture,data}[tab])()}
document.addEventListener('click',e=>{
 const scb=e.target.closest('#sc button');if(scb){scope=scb.dataset.sc;document.querySelectorAll('#sc button').forEach(b=>b.classList.toggle('on',b===scb));const k=curK;closeShare();applyScope();render();if(k)openK(k);toast(t('toast.scope.'+scope));return}
 const lgb=e.target.closest('#lg button');if(lgb){lang=lgb.dataset.lang;document.querySelectorAll('#lg button').forEach(b=>b.classList.toggle('on',b===lgb));const k=curK;closeShare();applyStatic();render();if(k)openK(k);toast(t('toast.lang'));return}
 if(e.target.closest('[data-shclose]')||e.target.id==='shr'){closeShare();return}
 if(e.target.id==='shgo'){doShare();return}
 if(e.target.id==='shcp'){copyText($('#capt').value);return}
 if(e.target.id==='copyPicks'){copyText(capFor(getPicks()));return}
 const cpb=e.target.closest('[data-copy]');if(cpb){copyText(capFor([byK(cpb.dataset.copy)]));return}
 if(e.target.id==='sharePicks'){showShare(getPicks());return}
 const shb=e.target.closest('[data-share]');if(shb){showShare([byK(shb.dataset.share)]);return}
 const lv=e.target.closest('#lv button');if(lv){document.body.dataset.lv=lv.dataset.lv;document.querySelectorAll('#lv button').forEach(b=>b.classList.toggle('on',b===lv));lvCur=lv.dataset.lv;$('#lvhint').textContent=t('hint.'+lvCur);return}
 if(e.target.closest('[data-close]')||e.target.id==='bk'){closeK();return}
 const nv=e.target.closest('#nav button');if(nv){tab=nv.dataset.t;closeK();render();scrollTo(0,0);return}
 const ch=e.target.closest('.chip');if(ch){dom=ch.dataset.d;explore();return}
 if(e.target.id==='cp'){const t=$('#bj').textContent;(navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject()).then(()=>$('#cpm').textContent=t('d.copied'),()=>{const r=document.createRange();r.selectNodeContents($('#bj'));const s=getSelection();s.removeAllRanges();s.addRange(r);$('#cpm').textContent=t('d.selected')});return}
 const kc=e.target.closest('[data-k]');if(kc&&!e.target.closest('a'))openK(kc.dataset.k)});
document.addEventListener('input',e=>{if(e.target.id==='q'){q=e.target.value;fillList()}});
document.addEventListener('change',e=>{if(e.target.id==='sort'){sortBy=e.target.value;fillList()}});
if(!HAS_GL){const r=$('#scrow');if(r)r.style.display='none';document.querySelectorAll('#sc button').forEach(b=>b.classList.toggle('on',b.dataset.sc==='my'))}
applyStatic();
render();
</script>
</body>
</html>
'''

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=21)
    ap.add_argument("--out", default="index.html")
    ap.add_argument("--no-trends", action="store_true")
    ap.add_argument("--no-global", action="store_true", help="skip worldwide news (Malaysia only)")
    ap.add_argument("--data-out", default=None, help="also write the raw data as JSON (used by notify.py)")
    a = ap.parse_args()

    today = dt.date.today()
    days = [today - dt.timedelta(days=i) for i in range(a.days, 0, -1)]  # today excluded (partial)
    names = [k for k, _, _ in KEYWORDS]
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    GL = not a.no_global
    import hashlib
    def gpref(k):   # default query keeps the original cache key; a custom query gets its own so edits never reuse stale results
        q = GLOBAL_QUERY.get(k)
        return "G|" if not q else "G:" + hashlib.md5(q.encode()).hexdigest()[:6] + "|"
    has_g = lambda k: GL and GLOBAL_QUERY.get(k, "") is not None
    keep = {f"{k}|{d}" for k in names for d in days}
    if GL: keep |= {f"{gpref(k)}{k}|{d}" for k in names if has_g(k) for d in days}
    cache = {k: v for k, v in cache.items() if k in keep}

    todo = [(k, d, False) for k in names for d in days if f"{k}|{d}" not in cache]
    if GL: todo += [(k, d, True) for k in names if has_g(k) for d in days if f"{gpref(k)}{k}|{d}" not in cache]
    print(f"news: {len(todo)} to fetch, {len(keep) - len(todo)} cached (Malaysia + {'worldwide' if GL else 'no worldwide'})")
    ck = lambda x: (f"{gpref(x[0])}{x[0]}|{x[1]}" if x[2] else f"{x[0]}|{x[1]}")
    def work(x):
        r = fetch_day(x[0], x[1], x[2], GLOBAL_QUERY.get(x[0]) if x[2] else None)
        if r is not None: cache[ck(x)] = r
    for i in range(0, len(todo), 200):                      # save progress every 200 requests
        with ThreadPoolExecutor(8) as ex: list(ex.map(work, todo[i:i + 200]))
        json.dump(cache, open(CACHE, "w"))
        if len(todo) > 200: print(f"  fetched {min(i + 200, len(todo))}/{len(todo)}", flush=True)
    miss = [x for x in todo if ck(x) not in cache]
    if miss: print(f"warning: {len(miss)} day-queries failed; rerun to fill gaps (counted as 0 for now)")

    sia = get_sia()
    out = {}
    def digest(prefix, k):
        counts, heads = [], []
        for d in days:
            ents = cache.get(f"{prefix}{k}|{d}", [])
            counts.append(len(ents))
            for title, link in ents:
                t = re.sub(r"\s+-\s+[^-]+$", "", title)
                pub = title.rsplit(" - ", 1)[-1] if " - " in title else ""
                heads.append((d.isoformat(), t, pub, link, sia.polarity_scores(t)["compound"]))
        recent = [h[4] for h in heads if h[0] >= days[-min(7, len(days))].isoformat()] or [h[4] for h in heads]
        seen, top = set(), []
        for h in sorted(heads, key=lambda x: x[0], reverse=True):
            if h[1] in seen: continue
            seen.add(h[1]); top.append(list(h[:4]))
            if len(top) == 4: break
        return counts, top, (round(sum(recent) / len(recent), 3) if recent else 0)
    for k, dom, pipe in KEYWORDS:
        counts, top, sent = digest("", k)
        out[k] = {"k": k, "d": dom, "p": pipe, "s": counts, "w": None, "t": None, "tg": None, "sent": sent, "h": top}
        if has_g(k):
            gc, gtop, gsent = digest(gpref(k), k)
            out[k].update({"gs": gc, "gh": gtop, "gsent": gsent})
            if GLOBAL_QUERY.get(k): out[k]["gq"] = GLOBAL_QUERY[k]

    for k, p in WIKI.items():
        if k in out: out[k]["w"] = wiki_views(p, days); time.sleep(0.3)
    print("wikipedia: done")

    health = {"trends_failed_batches": 0}
    TC = "trends_cache.json"
    tc = json.load(open(TC)) if os.path.exists(TC) else {}
    if not a.no_trends:
        try:
            from trendspy import Trends
            tr = Trends(request_delay=2.0)
            need = [k for k in names if not (k in tc and tc[k].get("date") == today.isoformat())]
            for grp in [need[i:i + 5] for i in range(0, len(need), 5)]:
                ok = False
                for attempt in (1, 2):
                    try:
                        df = tr.interest_over_time(grp, geo="MY", timeframe="today 3-m")
                        for k in grp: tc[k] = {"date": today.isoformat(), "series": [float(v) for v in df[k].tolist()]}
                        ok = True; break
                    except Exception as e:
                        print(f"trends batch {grp[0]!r} attempt {attempt} failed:", repr(e)[:50])
                        time.sleep(30 if attempt == 1 else 0)
                if not ok: health["trends_failed_batches"] += 1
                json.dump(tc, open(TC, "w"))
                time.sleep(6)
            print("trends: done")
        except ImportError:
            print("trends skipped (pip install trendspy)")
    for k in names:
        ser = (tc.get(k) or {}).get("series")
        if ser:
            out[k]["t"] = ser[-len(days):]
            if len(ser) >= 60:   # 90d growth proxy: last 30d mean vs prior mean
                rec, prev = sum(ser[-30:]) / 30, sum(ser[:-30]) / max(len(ser) - 30, 1)
                out[k]["tg"] = round((rec / prev - 1) * 100, 1) if prev > 0 else None

    origin = trending_now()
    print(f"origin: {len(origin['items'])} trending-now items")

    now_utc = dt.datetime.now(dt.timezone.utc)
    myt = now_utc.astimezone(dt.timezone(dt.timedelta(hours=8)))   # Malaysia time (no daylight saving)
    payload = {"gen": myt.strftime("%Y-%m-%d %H:%M MYT"), "genIso": now_utc.isoformat(timespec="seconds"),
               "days": [d.isoformat() for d in days], "kw": [out[k] for k in names],
               "origin": origin, "pairs": [list(p) for p in PAIRS], "health": health}
    blob = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    if a.data_out:
        os.makedirs(os.path.dirname(a.data_out) or ".", exist_ok=True)
        json.dump(payload, open(a.data_out, "w", encoding="utf-8"), ensure_ascii=False)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    open(a.out, "w", encoding="utf-8").write(TEMPLATE.replace("__DATA__", blob))
    print(f"wrote {a.out}  ({len(names)} keywords, {len(days)} days)")


if __name__ == "__main__":
    main()
