#!/usr/bin/env python3
"""
notify.py — send the daily Trend Radar brief to Telegram.

    python notify.py --data brief/data.json --dry-run      # print the message, send nothing
    python notify.py --data brief/data.json                # send it

Environment variables
    TELEGRAM_BOT_TOKEN   token from @BotFather
    TELEGRAM_CHAT_ID     your chat id, or @yourchannel for a channel the bot administers
    PAGE_URL             link to your published dashboard (optional)
    NOTIFY_LANG          en (default), ms, or both (sends two messages)
    NOTIFY_SCOPE         both (default), my or gl: which news the picks are based on
Without a token and chat id the script prints a notice and exits quietly.
The scoring below mirrors the dashboard, so the message and the page agree.
"""
import argparse, datetime as dt, html, json, os, sys
import requests

FULL_DAYS = 45
STEN = {"BREAKOUT": ("Exploding", "🔥"), "GROWTH": ("Heating up", "📈"), "BASELINE": ("Steady", "〰️"),
        "FADING": ("Cooling down", "🧊"), "SPARSE": ("Too quiet to call", "🤫")}
STMS = {"BREAKOUT": ("Meletup", "🔥"), "GROWTH": ("Semakin naik", "📈"), "BASELINE": ("Stabil", "〰️"),
        "FADING": ("Semakin reda", "🧊"), "SPARSE": ("Terlalu senyap", "🤫")}
MON_EN = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
MON_MS = "Jan Feb Mac Apr Mei Jun Jul Ogo Sep Okt Nov Dis".split()


from topics import TOPICS

def desc(o, lang):
    """Plain-English description of the topic. Falls back to the category if a topic has no entry."""
    t = o.get("i") or TOPICS.get(o["k"])
    if t: return t[1] if lang == "ms" else t[0]
    return (f"Berita dan carian berkaitan {o['k']} ({o['d']})." if lang == "ms" else f"News and searches about {o['k']} ({o['d']}).")

def latest_headline(o):
    """Newest saved headline for the topic (Malaysia or worldwide), or None."""
    hs = [h for h in (o.get("h") or []) + (o.get("gh") or []) if len(h) >= 4]
    return max(hs, key=lambda h: h[0]) if hs else None

# ---------- Malaysia time (UTC+8, no daylight saving) ----------
MYT = dt.timezone(dt.timedelta(hours=8))
def gen_local(d):
    """Return the build time as a Malaysia-time datetime."""
    iso = d.get("genIso")
    try:
        if iso: return dt.datetime.fromisoformat(iso).astimezone(MYT)
        g = d.get("gen", "").replace(" UTC", "").replace(" MYT", "")
        t = dt.datetime.strptime(g, "%Y-%m-%d %H:%M")
        return t.replace(tzinfo=MYT) if "MYT" in d.get("gen", "") else t.replace(tzinfo=dt.timezone.utc).astimezone(MYT)
    except Exception:
        return dt.datetime.now(MYT)
def fstamp(d, lang):
    t = gen_local(d); m = (MON_MS if lang == "ms" else MON_EN)[t.month - 1]
    return f"{t.day} {m} {t.year}, {t.strftime('%H:%M')} MYT"

def mean(a): return sum(a) / len(a)
def sdev(a):
    if len(a) < 2: return 1.0
    m = mean(a); sd = (sum((v - m) ** 2 for v in a) / (len(a) - 1)) ** .5
    return sd or 1.0
def zscore(s):
    b = s[-15:-1]
    return (s[-1] - mean(b)) / sdev(b)
def clamp(v, lo, hi): return max(lo, min(hi, v))
def rnd(x): return int(x + 0.5)

def compute(d, scope="both"):
    kws, N = d["kw"], len(d["days"])
    for o in kws:
        o["sMY"], o["sGL"] = o["s"], (o.get("gs") or [0] * N)
        o["zMY"], o["zGL"] = zscore(o["sMY"]), zscore(o["sGL"])
        o["avgMY"], o["avgGL"] = mean(o["sMY"]), mean(o["sGL"])
        o["totMY"], o["totGL"] = sum(o["sMY"]), sum(o["sGL"])
        o["lastMY"], o["lastGL"] = o["sMY"][-1], o["sGL"][-1]
        o["sentMY"] = o.get("sent", 0); o["sentGL"] = o.get("gsent", o["sentMY"]) if o.get("gs") else o["sentMY"]
        sig = None
        if o.get("gs"):
            if o["avgGL"] >= 5 and o["zGL"] > 1.5 and o["zMY"] < .5: sig = "gl"
            elif o["avgMY"] >= 5 and o["zMY"] > 1.5 and o["zGL"] < .5: sig = "my"
            elif o["avgMY"] >= 5 and o["zMY"] > 1 and o["zGL"] > 1: sig = "both"
        o["sig"] = sig
        if scope == "my": o["ss"], o["sent"] = o["sMY"], o["sentMY"]
        elif scope == "gl": o["ss"], o["sent"] = o["sGL"], o["sentGL"]
        else:
            o["ss"] = [a + b for a, b in zip(o["sMY"], o["sGL"])]
            o["sent"] = (o["sentMY"] * o["totMY"] + o["sentGL"] * o["totGL"]) / max(o["totMY"] + o["totGL"], 1)
    tot = [sum(o["ss"]) for o in kws]; tmax = max(tot + [1])
    for o, tt in zip(kws, tot):
        s = o["ss"]
        o["avg"], o["last"] = mean(s), s[-1]
        o["vel"] = s[-1] - s[-2]; o["last7"] = sum(s[-7:])
        o["z"] = zscore(s); o["sparse"] = o["avg"] < 5 or N < 15
        o["status"] = ("SPARSE" if o["sparse"] else "BREAKOUT" if (o["z"] > 2 and o["vel"] > 0)
                       else "GROWTH" if (o["z"] > .5 and o["vel"] >= 0) else "FADING" if (o["z"] < -1 and o["vel"] < 0) else "BASELINE")
        seg = s[-21:-7]; p14 = (mean(seg) if seg else 0) or .1; r = mean(s[-7:]) / p14
        o["ratio"] = r
        o["dir"] = "▲" if r > 1.15 else "▼" if r < .85 else "▬"
        o["supply"] = tt / tmax
        o["raw"] = None if o["sparse"] else clamp(o["z"], 0, 3) / 3 * (1 - o["supply"]) * (.25 + abs(o["sent"]))
    rmax = max([o["raw"] for o in kws if o["raw"] is not None] + [0]) or 1
    for o in kws: o["gap"] = None if o["raw"] is None else rnd(o["raw"] / rmax * 100)
    return kws

def picks(kws):
    ranked = sorted([o for o in kws if not o["sparse"]], key=lambda o: -o["gap"])
    out, seen = [], set()
    for o in ranked:
        if len(out) < 3 and o["d"] not in seen: out.append(o); seen.add(o["d"])
    for o in ranked:
        if len(out) < 3 and o not in out: out.append(o)
    return out

def hook(o, lang):
    k, ms = o["k"], lang == "ms"
    if o["sparse"]: return f"“Ada yang perasan tentang {k}? Ini yang saya jumpa.”" if ms else f"“Has anyone noticed {k}? Here’s what I found.”"
    if o["status"] == "BREAKOUT": return f"“Semua orang bertanya tentang {k}. Versi 30 saat.”" if ms else f"“Everyone’s asking about {k}. The 30-second version.”"
    if o["status"] == "GROWTH": return f"“{k} semakin hangat. Ini yang berubah minggu ini.”" if ms else f"“{k} is picking up. Here’s what changed this week.”"
    if o["status"] == "FADING": return f"“{k}: situasi terkini.” (rumusan)" if ms else f"“{k}: where things stand now.” (wrap-up)"
    return f"“{k}: apa itu dan kenapa ia penting sekarang.”" if ms else f"“{k}: what it is and why it matters right now.”"

def talk(o, lang):
    n = rnd(o["avg"])
    if lang == "ms": return "kurang daripada 1 berita sehari" if o["avg"] < 1 else f"kira-kira {n} berita sehari"
    return "under 1 story a day" if o["avg"] < 1 else f"about {n} stor{'y' if n == 1 else 'ies'} a day"

def dirw(o, lang):
    w = {"▲": ("rising", "meningkat"), "▼": ("falling", "menurun"), "▬": ("steady", "stabil")}[o["dir"]]
    return w[1] if lang == "ms" else w[0]

def fdate(iso, lang):
    d = dt.date.fromisoformat(iso); m = (MON_MS if lang == "ms" else MON_EN)[d.month - 1]
    return f"{d.day} {m} {d.year}"

ANGLES = {   # content angles by domain, English / Bahasa Malaysia. {k} = topic. Rotated daily so the brief never reads the same twice.
 "Politics": (["What {k} means for ordinary Malaysians, in 30 seconds", "Everyone is arguing about {k}. Here is the part nobody explains", "3 things to know about {k} before the next headline"],
              ["Apa maksud {k} untuk rakyat biasa, dalam 30 saat", "Semua orang bertegang urat tentang {k}. Ini bahagian yang jarang dijelaskan", "3 perkara tentang {k} sebelum tajuk berita seterusnya"]),
 "Economy": (["What {k} means for your wallet this month", "{k}: the number everyone is watching, explained simply", "Should you care about {k}? A quick reality check"],
             ["Apa kesan {k} pada poket anda bulan ini", "{k}: angka yang semua orang tunggu, dijelaskan dengan mudah", "Perlukah anda ambil peduli tentang {k}? Semakan pantas"]),
 "Tech & Infra": (["{k} is changing Malaysia faster than you think", "What {k} really means for your daily life", "{k} in 30 seconds: hype versus reality"],
                  ["{k} mengubah Malaysia lebih pantas daripada sangkaan anda", "Apa sebenarnya maksud {k} dalam hidup harian anda", "{k} dalam 30 saat: gah atau realiti"]),
 "Energy": (["Why {k} matters at the pump and on your bill", "{k}: who wins and who pays", "The simple story behind {k}"],
            ["Kenapa {k} penting di stesen minyak dan pada bil anda", "{k}: siapa untung dan siapa menanggung", "Kisah ringkas di sebalik {k}"]),
 "Safety & Health": (["Protect your family: what to know about {k} right now", "{k}: the warning signs most people miss", "A 1-minute safety check on {k}"],
                     ["Lindungi keluarga: apa perlu diketahui tentang {k} sekarang", "{k}: tanda amaran yang sering terlepas pandang", "Semakan keselamatan 1 minit tentang {k}"]),
 "Society": (["Why everyone in Malaysia is talking about {k}", "{k}: the story behind the queue", "What {k} says about how we shop now"],
             ["Kenapa semua orang di Malaysia bercakap tentang {k}", "{k}: kisah di sebalik barisan panjang", "Apa yang {k} beritahu tentang cara kita membeli-belah"]),
 "Cyber Security": (["Could this happen to you? What {k} means for your phone and bank", "3 signs you are being targeted by {k}", "{k} explained: how to protect yourself in 2 minutes"],
                    ["Boleh jadi pada anda? Apa maksud {k} untuk telefon dan bank anda", "3 tanda anda menjadi sasaran {k}", "{k} dijelaskan: cara melindungi diri dalam 2 minit"]),
 "Culture (local)": (["The story behind {k} that most people never hear", "{k} is trending again. Here is why it deserves your attention", "Meet the makers keeping {k} alive"],
                     ["Kisah di sebalik {k} yang jarang didengar", "{k} kembali tular. Sebab ia layak anda perhatikan", "Kenali pembuat yang memelihara {k}"]),
 "Inbound": (["Everyone is talking about {k}. Here is the Malaysian take", "{k}: how to try the trend with a local twist", "Why {k} is blowing up, and how to wear it locally"],
             ["Semua orang bercakap tentang {k}. Ini versi Malaysia", "{k}: cara cuba trend ini dengan sentuhan tempatan", "Kenapa {k} meletup, dan cara menggayakannya secara tempatan"]),
}

def angle(o, lang, i):
    """Pick a different angle each day and for each slot, so the message stays fresh."""
    en, ms_ = ANGLES.get(o["d"], (["The 30-second version of {k}", "What changed with {k} this week", "{k}: what you need to know today"],
                                  ["Versi 30 saat tentang {k}", "Apa yang berubah dengan {k} minggu ini", "{k}: apa perlu anda tahu hari ini"]))
    opts = ms_ if lang == "ms" else en
    return opts[(dt.date.today().toordinal() + i) % len(opts)].format(k=o["k"])

def mood_txt(o, lang):
    v = o["sent"]; ms = lang == "ms"
    return (("bimbang 😟" if ms else "worried 😟") if v < -.15 else ("positif 😊" if ms else "upbeat 😊") if v > .15 else ("bercampur 😐" if ms else "mixed 😐"))

def greeting(t, lang):
    h = t.hour
    if lang == "ms": return "Selamat pagi" if h < 12 else "Selamat tengah hari" if h < 14 else "Selamat petang" if h < 19 else "Selamat malam"
    return "Good morning" if h < 12 else "Good afternoon" if h < 18 else "Good evening"

def fday(t, lang):
    wd = ("Isn Sel Rab Kha Jum Sab Aha" if lang == "ms" else "Mon Tue Wed Thu Fri Sat Sun").split()[t.weekday()]
    m = (MON_MS if lang == "ms" else MON_EN)[t.month - 1]
    return f"{wd} {t.day} {m} · {t.strftime('%-I:%M%p').lower()} MYT"

NUMS = ["1️⃣", "2️⃣", "3️⃣"]

def caption(d, lang="en", scope="both"):
    """Short line shown under the picture."""
    n = len(picks(compute(json.loads(json.dumps(d)), scope)))
    return (f"☀️ {n} topik untuk disiarkan hari ini" if lang == "ms" else f"☀️ {n} topics to post about today")

def message(d, lang="en", scope="both", url=""):
    d = json.loads(json.dumps(d))                       # work on a copy
    kws = compute(d, scope); ms = lang == "ms"
    E = html.escape; t = gen_local(d)
    L = [f"{'☀️' if t.hour < 18 else '🌙'} <b>{greeting(t, lang)}!</b> <i>{fday(t, lang)}</i>", ""]
    P = picks(kws)
    if P:
        L.append("<b>" + ("Topik yang patut anda siarkan hari ini 👇" if ms else "What to post about today 👇") + "</b>"); L.append("")
        for i, o in enumerate(P):
            L.append(f"{NUMS[i]} <b>{E(o['k'])}</b>")
            h = latest_headline(o)
            if h:
                ttl = h[1] if len(h[1]) <= 100 else h[1][:97] + "…"
                L.append(f"📰 <a href=\"{E(h[3], quote=True)}\">{E(ttl)}</a>" + (f" <i>({E(h[2])})</i>" if h[2] else ""))
            bits = []
            if o["ratio"] >= 1.3: bits.append((f"{o['ratio']:.1f}× liputan biasa" if ms else f"{o['ratio']:.1f}× its usual coverage") + " 📈")
            bits.append(("nada berita: " if ms else "mood: ") + mood_txt(o, lang))
            L.append(" · ".join(bits))
            L.append(f"✍️ <i>{'Sudut' if ms else 'Angle'}:</i> “{E(angle(o, lang, i))}”"); L.append("")
    br = sorted([o for o in kws if o["status"] == "BREAKOUT" and o not in P], key=lambda o: -o["z"])[:4]
    br_all = [o for o in kws if o["status"] == "BREAKOUT"]
    if br:
        L.append(f"🔥 <b>{'Sedang meletup' if ms else 'Exploding right now'}:</b> " + ", ".join(E(o["k"]) for o in br))
    elif br_all:
        L.append("🔥 " + ("Topik paling panas hari ini sudah ada di atas." if ms else "The hottest topics are already in your top 3."))
    gl = sorted([o for o in kws if o["sig"] == "gl" and o not in P], key=lambda o: -o["zGL"])[:4]
    if gl:
        L.append(f"🌐 <b>{'Hangat di luar negara, Malaysia belum ikut' if ms else 'Big abroad, Malaysia has not caught up'}:</b> " + ", ".join(E(o["k"]) for o in gl))
        L.append("   " + ("Jadilah yang pertama menerangkannya di sini." if ms else "Be the first to explain these locally."))
    my = sorted([o for o in kws if o["sig"] == "my" and o not in P], key=lambda o: -o["zMY"])[:4]
    if my:
        L.append(f"🇲🇾 <b>{'Hanya hangat di Malaysia' if ms else 'Only buzzing in Malaysia'}:</b> " + ", ".join(E(o["k"]) for o in my))
    rest = len([o for o in kws if not o["sparse"]]) - len(P)
    if url:
        if L[-1] != "": L.append("")
        L.append(f"👉 <a href=\"{E(url, quote=True)}\">" + (f"Lihat {rest} topik lain dan carta penuh" if ms else f"See the other {rest} topics and full charts") + "</a>")
    L.append("<i>" + ("Isyarat masa kandungan, bukan nasihat kewangan." if ms else "Content-timing signals, not financial advice.") + "</i>")
    text = "\n".join(L)
    return text if len(text) <= 4000 else text[:3990] + "…"


# ---------- picture for Telegram (Pillow; no emoji font required) ----------
FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/share/fonts/truetype/liberation",
             "/Library/Fonts", "/System/Library/Fonts/Supplemental", "C:/Windows/Fonts"]
FONT_FILES = {"reg": ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf", "arial.ttf"],
              "bold": ["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf"],
              "ital": ["DejaVuSans-Oblique.ttf", "LiberationSans-Italic.ttf", "Arial Italic.ttf", "ariali.ttf"]}
def load_font(kind, size):
    from PIL import ImageFont
    for d in FONT_DIRS:
        for f in FONT_FILES[kind]:
            p = os.path.join(d, f)
            if os.path.exists(p):
                try: return ImageFont.truetype(p, size)
                except Exception: pass
    return ImageFont.load_default(size=size)

def wrap_px(draw, text, font, maxw, maxlines):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) > maxw and cur: lines.append(cur); cur = w
        else: cur = t
    if cur: lines.append(cur)
    if len(lines) > maxlines:
        lines = lines[:maxlines]; l = lines[-1]
        while draw.textlength(l + "…", font=font) > maxw and len(l) > 1: l = l[:-1]
        lines[-1] = l + "…"
    return lines

STATUS_COL = {"BREAKOUT": ("#ffe4de", "#e0431f"), "GROWTH": ("#dcfce7", "#15803d"), "BASELINE": ("#eef1f8", "#5b6578"),
              "FADING": ("#cffafe", "#0e7490"), "SPARSE": ("#eef1f8", "#5b6578")}

def render_image(d, lang="en", scope="both"):
    """1080x1920 'Post about this today' card, same look as the dashboard's share image. Returns PNG bytes."""
    from PIL import Image, ImageDraw, ImageFilter
    import io
    d = json.loads(json.dumps(d)); kws = compute(d, scope); P = picks(kws); ms = lang == "ms"; ST = STMS if ms else STEN
    W, H = 1080, 1920
    img = Image.new("RGB", (W, H)); dr = ImageDraw.Draw(img)
    c0, c1 = (67, 56, 202), (8, 145, 178)
    for y in range(H):
        t = y / (H - 1); dr.line([(0, y), (W, y)], fill=tuple(int(c0[i] + (c1[i] - c0[i]) * t) for i in range(3)))
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0)); od = ImageDraw.Draw(ov)
    od.ellipse((W - 360, -200, W + 280, 440), fill=(255, 255, 255, 24)); od.ellipse((-200, H - 540, 360, H + 20), fill=(255, 255, 255, 24))
    img = Image.alpha_composite(img.convert("RGBA"), ov); dr = ImageDraw.Draw(img)
    F = lambda k, s: load_font(k, s)
    dr.text((70, 80), "MALAYSIA TREND RADAR", font=F("bold", 38), fill="white")
    dr.text((70, 150), "Siarkan ini hari ini" if ms else "Post about this today", font=F("bold", 80), fill="white")
    today = (dt.date.fromisoformat(d["days"][-1]) + dt.timedelta(days=1)).isoformat()
    sub = "Saingan rendah, minat meningkat" if ms else "Low competition, rising interest"
    fs = 36; fo = F("reg", fs)
    while dr.textlength(sub, font=fo) > W - 140 and fs > 22: fs -= 1; fo = F("reg", fs)
    dr.text((70, 262), sub, font=fo, fill=(255, 255, 255, 235))
    top, bottom, gap, n = 350, H - 150, 30, max(len(P), 1)
    ch = int(min((bottom - top - (n - 1) * gap) / n, 480)); cx, cw = 50, W - 100
    for i, o in enumerate(P):
        y = top + i * (ch + gap); px = cx + 48
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0)); ImageDraw.Draw(sh).rounded_rectangle((cx, y + 10, cx + cw, y + ch + 10), 44, fill=(0, 0, 0, 70))
        img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(14))); dr = ImageDraw.Draw(img)
        dr.rounded_rectangle((cx, y, cx + cw, y + ch), 44, fill="white")
        dr.rounded_rectangle((px, y + 36, px + 110, y + 146), 34, fill="#eef1f8")
        ini = (o["d"].strip()[:1] or "•").upper(); fi = F("bold", 56)
        dr.text((px + 55, y + 91), ini, font=fi, fill="#4f46e5", anchor="mm")
        dr.ellipse((px - 20, y + 16, px + 32, y + 68), fill="#4f46e5", outline="white", width=4)
        dr.text((px + 6, y + 42), str(i + 1), font=F("bold", 30), fill="white", anchor="mm")
        dr.text((px + 140, y + 36), wrap_px(dr, o["k"], F("bold", 62), cw - 520, 1)[0], font=F("bold", 62), fill="#141a26")
        lab = ST[o["status"]][0]; fl = F("bold", 32); pw = int(dr.textlength(lab, font=fl)) + 44
        bg, fg = STATUS_COL[o["status"]]
        dr.rounded_rectangle((px + 140, y + 104, px + 140 + pw, y + 156), 26, fill=bg); dr.text((px + 162, y + 130), lab, font=fl, fill=fg, anchor="lm")
        s = o["ss"]; sw, shh, sx, sy = 210, 84, cx + cw - 48 - 210, y + 48; m = max(max(s), 1); st = sw / (len(s) - 1)
        pts = [(sx + k * st, sy + shh - v / m * shh) for k, v in enumerate(s)]
        dr.line(pts, fill="#4f46e5", width=7, joint="curve")
        yy = y + 206
        dr.text((px, yy), f"{talk(o, lang)} · {dirw(o, lang)} {o['dir']}", font=F("reg", 34), fill="#475069", anchor="ls")
        yy += 52
        stars = "★★★" if o["gap"] >= 60 else "★★☆" if o["gap"] >= 30 else "★☆☆"
        word = (("Peluang tinggi" if o["gap"] >= 60 else "Peluang sederhana" if o["gap"] >= 30 else "Peluang rendah") if ms
                else ("High opportunity" if o["gap"] >= 60 else "Medium opportunity" if o["gap"] >= 30 else "Low opportunity"))
        dr.text((px, yy), stars, font=F("bold", 38), fill="#f59e0b", anchor="ls")
        dr.text((px + int(dr.textlength(stars + "  ", font=F("bold", 38))), yy), word, font=F("bold", 36), fill="#141a26", anchor="ls")
        by = yy + 16; bh = y + ch - 26 - by
        if bh > 90:
            dr.rounded_rectangle((px, by, px + cw - 96, by + bh), 28, fill="#eef1f8")
            fi2 = F("ital", 34); cap = max(1, (bh - 30) // 46)
            for k, ln in enumerate(wrap_px(dr, desc(o, lang), fi2, cw - 96 - 64, cap)):
                dr.text((px + 32, by + 50 + k * 46), ln, font=fi2, fill="#1e2433", anchor="ls")
    dr.text((70, H - 92), ("Dikemas kini " if ms else "Updated ") + fstamp(d, lang), font=F("bold", 32), fill="white", anchor="ls")
    foot = "Google News MY + worldwide · Trends · Wikipedia · " + ("Bukan nasihat kewangan" if ms else "Not financial advice")
    fs = 28; ff = F("reg", fs)
    while dr.textlength(foot, font=ff) > W - 140 and fs > 18: fs -= 1; ff = F("reg", fs)
    dr.text((70, H - 50), foot, font=ff, fill=(255, 255, 255, 225), anchor="ls")
    buf = io.BytesIO(); img.convert("RGB").save(buf, "PNG", optimize=True); return buf.getvalue()

def send_photo(png, caption, token, chat, base):
    r = requests.post(f"{base}/bot{token}/sendPhoto", timeout=60, data={"chat_id": chat, "caption": caption[:1000]},
                      files={"photo": ("trend-radar-picks.png", png, "image/png")})
    if not r.ok:
        print(f"Telegram photo error {r.status_code}: {r.text[:300]}", file=sys.stderr); return False
    return True

def send(text, token, chat, base):
    r = requests.post(f"{base}/bot{token}/sendMessage", timeout=30,
                      json={"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
    if not r.ok:
        print(f"Telegram error {r.status_code}: {r.text[:300]}", file=sys.stderr); return False
    return True

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="brief/data.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-image", action="store_true", help="send text only")
    a = ap.parse_args()
    d = json.load(open(a.data, encoding="utf-8"))
    lang = os.getenv("NOTIFY_LANG", "en").lower(); scope = os.getenv("NOTIFY_SCOPE", "both").lower()
    if scope not in ("both", "my", "gl"): scope = "both"
    langs = ["en", "ms"] if lang == "both" else ["ms" if lang == "ms" else "en"]
    url = os.getenv("PAGE_URL", "")
    texts = [message(d, l, scope, url) for l in langs]
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN", "").strip(), os.getenv("TELEGRAM_CHAT_ID", "").strip()
    use_img = not a.no_image and os.getenv("NOTIFY_IMAGE", "1") != "0"
    def try_image(l):
        if not use_img: return None
        try: return render_image(d, l, scope)
        except Exception as e:
            print(f"(picture skipped: {e})", file=sys.stderr); return None
    if a.dry_run or not (token and chat):
        for l, t in zip(langs, texts):
            print(t, "\n" + "-" * 40)
            png = try_image(l)
            if png and a.dry_run:
                os.makedirs("brief", exist_ok=True); open(f"brief/preview_{l}.png", "wb").write(png); print(f"[picture saved: brief/preview_{l}.png]")
        if not a.dry_run: print("Telegram not configured (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID). Nothing sent.")
        return 0
    base = os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")
    ok = True
    for l, t in zip(langs, texts):
        png = try_image(l)
        if png: send_photo(png, caption(d, l, scope), token, chat, base)
        ok = send(t, token, chat, base) and ok
    print("Telegram message sent." if ok else "Telegram send failed.")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
