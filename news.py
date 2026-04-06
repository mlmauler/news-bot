# =========================================================
# The Daily Brief — Single-Column, Paragraph Summaries 

# =========================================================
# - Centered title banner (Helvetica), right-aligned date
# - Sections rendered as 1–2 paragraph summaries (no bullets)
# - Shaded "Strategic Impact" boxes
# - Color-coded terrorism items (🔴🟠🟡🟢) with synopsis + small link line
# - Travel Warnings: title + 1-line synopsis + hyperlink
# - Optional auto-translate to English (LibreTranslate-compatible endpoint)
# - Footer with page number + internal-use note
# - SendGrid email + (optional) Google Drive backup (PyDrive2)
# - Keeps last 7 PDFs
# - TEST MODE: set RUN_TESTS=1 to run basic unit tests and exit
# =========================================================

import html
import os
import sys
import re
import base64
import requests
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

# Built-in XML parser (no external feedparser dependency)
import xml.etree.ElementTree as ET

# -------- ReportLab --------
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle
)
from reportlab.platypus.flowables import HRFlowable as HR

# -------- Email (SendGrid) --------
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition
import smtplib
from email.message import EmailMessage
from email.utils import formatdate

def send_pdf_via_gmail(pdf_path):
    gmail_user = os.getenv("GMAIL_USER")
    gmail_pass = os.getenv("GMAIL_APP_PASSWORD")

    if not gmail_user or not gmail_pass:
        print("[gmail] Gmail credentials not set, skipping email.")
        return

    try:
        msg = EmailMessage()
        msg["From"] = gmail_user
        msg["To"] = gmail_user          # send to yourself
        msg["Date"] = formatdate(localtime=True)
        msg["Subject"] = "The Daily Brief"

        msg.set_content(
            "Attached is today’s Daily Brief.\n\n"
            "— Automated Intelligence Report"
        )

        with open(pdf_path, "rb") as f:
            pdf_data = f.read()

        msg.add_attachment(
            pdf_data,
            maintype="application",
            subtype="pdf",
            filename=os.path.basename(pdf_path)
        )

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_pass)
            server.send_message(msg)

        print("[gmail] Email sent successfully.")

    except Exception as e:
        print(f"[gmail] Send failed: {e}")

# -------- Optional Drive (PyDrive2) --------
try:
    from pydrive2.auth import GoogleAuth
    from pydrive2.drive import GoogleDrive
    HAVE_PYDRIVE2 = True
except Exception:
    HAVE_PYDRIVE2 = False

# -------- Config (env) --------
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
EMAIL_FROM       = os.getenv("EMAIL_FROM")
EMAIL_TO         = os.getenv("EMAIL_TO")
NEWSAPI_KEY      = os.getenv("NEWSAPI_KEY")  # optional

# ---- Optional auto-translation helpers (LibreTranslate-compatible) ----
AUTO_TRANSLATE = os.getenv("AUTO_TRANSLATE", "1") == "1"
TRANSLATE_URL = os.getenv("TRANSLATE_URL")  # e.g., https://libretranslate.de/translate
TRANSLATE_API_KEY = os.getenv("TRANSLATE_API_KEY")  # optional

def _safe(x):
    return x or ""

def _looks_non_english(text: str) -> bool:
    if not text:
        return False
    total = len(text)
    if total == 0:
        return False
    non_ascii = sum(1 for ch in text if ord(ch) > 127)
    return (non_ascii / total) > 0.15

def translate_text_to_en(text: str) -> str:
    if not text or not TRANSLATE_URL:
        return text
    try:
        payload = {
            "q": text,
            "source": "auto",
            "target": "en",
            "format": "text",
        }
        if TRANSLATE_API_KEY:
            payload["api_key"] = TRANSLATE_API_KEY
        resp = requests.post(TRANSLATE_URL, json=payload, timeout=15)
        if resp.ok:
            data = resp.json()
            if isinstance(data, dict) and data.get("translatedText"):
                return data["translatedText"]
            if isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get("translatedText"):
                return data[0]["translatedText"]
    except Exception:
        pass
    return text

def ensure_english(text: str) -> str:
    if AUTO_TRANSLATE and _looks_non_english(text):
        return translate_text_to_en(text)
    return text
# =========================================================
# Feed cache & health logging (additive, non-visual)
# =========================================================

CACHE_DIR = ".cache"
os.makedirs(CACHE_DIR, exist_ok=True)

def _today_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def _cache_path(name):
    safe = re.sub(r"[^a-z0-9_]+", "_", name.lower())
    return os.path.join(CACHE_DIR, f"{safe}_{_today_utc()}.json")

def load_cache(name):
    p = _cache_path(name)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def save_cache(name, data):
    try:
        with open(_cache_path(name), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def log_feed(name, status):
    print(f"[feed] {name}: {status}")



# =========================================================
# Data fetchers
# =========================================================

def fetch_news(topic, max_articles=8):
    if not NEWSAPI_KEY:
        return []
    url = (
        f"https://newsapi.org/v2/everything?q={requests.utils.quote(topic)}"
        f"&language=en&pageSize={max_articles}&sortBy=publishedAt&apiKey={NEWSAPI_KEY}"
    )
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        out = []
        for a in data.get("articles", []):
            out.append({
                "title": a.get("title"),
                "source": (a.get("source") or {}).get("name"),
                "description": a.get("description"),
                "url": a.get("url"),
            })
        return out
    except Exception as e:
        print(f"[fetch_news] failed for {topic}: {e}")
        return []

def fetch_bellingcat(max_items=8):
    cached = load_cache("bellingcat")
    if cached:
        log_feed("Bellingcat", "cached")
        return cached

    try:
        r = requests.get(
            "https://www.bellingcat.com/feed/",
            timeout=20,
            headers={"User-Agent": "DailyBrief/1.0"},
        )
        r.raise_for_status()
        root = ET.fromstring(r.content)

        out = []
        for item in root.findall(".//item")[:max_items]:
            out.append({
                "title": ensure_english(_safe(item.findtext("title"))),
                "source": "Bellingcat",
                "description": ensure_english(strip_html_tags(item.findtext("description"))),
                "url": item.findtext("link"),
            })

        save_cache("bellingcat", out)
        log_feed("Bellingcat", "OK")
        return out

    except Exception as e:
        log_feed("Bellingcat", "FAIL")
        return []
def fetch_html_analysis(name, url, link_pattern, max_items=6):
    cached = load_cache(name)
    if cached:
        log_feed(name, "cached")
        return cached

    try:
        r = requests.get(
            url,
            timeout=20,
            headers={"User-Agent": "DailyBrief/1.0"},
        )
        r.raise_for_status()

        matches = re.findall(link_pattern, r.text, flags=re.I)
        out, seen = [], set()

        for href, title in matches:
            t = strip_html_tags(title)
            if not t or t.lower() in seen:
                continue
            seen.add(t.lower())

            out.append({
                "title": ensure_english(t),
                "source": name,
                "description": "",
                "url": url.rstrip("/") + href if href.startswith("/") else href,
            })

            if len(out) >= max_items:
                break

        save_cache(name, out)
        log_feed(name, "OK")
        return out

    except Exception:
        log_feed(name, "FAIL")
        return []



# ---- Travel advisories (built-in XML parser) ----

def parse_travel_rss(xml_bytes):
    """Parse State Dept RSS/Atom -> list[{title, link, desc}]"""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    items = []
    # RSS 2.0
    for item in root.findall('.//item'):
        title = (item.findtext('title') or '').strip()
        link  = (item.findtext('link') or '').strip()
        desc  = (item.findtext('description') or '').strip()
        if title:
            items.append({"title": title, "link": link, "desc": desc})
    if items:
        return items
    # Atom fallback
    ns = {'atom': 'http://www.w3.org/2005/Atom'}
    for entry in root.findall('.//atom:entry', ns):
        t_el = entry.find('atom:title', ns)
        l_el = entry.find('atom:link', ns)
        c_el = entry.find('atom:summary', ns) or entry.find('atom:content', ns)
        title = (t_el.text if t_el is not None else '').strip()
        link = l_el.get('href') if l_el is not None else ''
        desc = (c_el.text if c_el is not None else '').strip()
        if title:
            items.append({"title": title, "link": link, "desc": desc})
    return items

def fetch_travel_warnings(max_items: int = 200):
    """
    Fetch current U.S. State Department Travel Alerts & Warnings.

    Uses the official TAsTWs.xml feed, which contains all currently
    active alerts/warnings, not just the latest handful.
    """
    rss = "https://travel.state.gov/_res/rss/TAsTWs.xml"

    for attempt in range(3):
        try:
            r = requests.get(rss, timeout=45)
            r.raise_for_status()
            items = parse_travel_rss(r.content)
            norm = []

            # Pull up to max_items (you can set this higher/lower)
            for it in items[:max_items]:
                title = ensure_english(_safe(it.get("title")))
                desc  = ensure_english(_safe(it.get("desc")))
                link  = _safe(it.get("link"))
                norm.append({"title": title, "desc": desc, "link": link})

            return norm

        except Exception as e:
            print(f"[fetch_travel_warnings] attempt {attempt+1}/3 failed: {e}")

    return []

import os
import base64
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition

def send_email(pdf_path):
print("Sending email via SendGrid...")

```
message = Mail(
    from_email='your_email@example.com',
    to_emails='your_email@example.com',
    subject='Your Daily Brief',
    html_content='<strong>Your daily newspaper is attached.</strong>'
)

with open(pdf_path, 'rb') as f:
    data = f.read()
    encoded = base64.b64encode(data).decode()

attachment = Attachment(
    FileContent(encoded),
    FileName(os.path.basename(pdf_path)),
    FileType('application/pdf'),
    Disposition('attachment')
)

message.attachment = attachment

sg = SendGridAPIClient(os.environ['SENDGRID_API_KEY'])

response = sg.send(message)
print("EMAIL STATUS:", response.status_code)
print("EMAIL BODY:", response.body)
```


# ---- Terrorism via GDELT ----

def _format_gdelt_date(ts: Optional[str]) -> str:
    """
    GDELT seendate comes in like '20251031T221200Z'.
    Turn that into something human-readable for the PDF.
    """
    if not ts:
        return ""
    try:
        # use the datetime *class* imported from datetime
        dt = datetime.strptime(ts, "%Y%m%dT%H%M%SZ")
        return dt.strftime("%d %b %Y %H:%M UTC")
    except Exception:
        return ts

def _format_gdelt_date(ts: str) -> str:
    """Turn a GDELT seendate like 20251207T154200Z into '07 Dec 2025 15:42 UTC'."""
    if not ts:
        return ""
    try:
        dt = datetime.strptime(ts, "%Y%m%dT%H%M%SZ")
        return dt.strftime("%d %b %Y %H:%M UTC")
    except Exception:
        return ts


# ---- Terrorism derived from State Dept travel advisories ----

# --- Key terrorist organizations (for OSINT-style updates) ---

TERROR_ORG_PROFILES = [
    {
        "name": "Islamic State (ISIS/ISIL)",
        "query": "\"Islamic State\" OR ISIS OR ISIL OR Daesh",
        "region": "Iraq / Syria; global networks",
    },
    {
        "name": "Al-Qaeda & affiliates",
        "query": "\"al-Qaeda\" OR \"al Qaida\" OR \"al Qa'ida\"",
        "region": "Afghanistan / Pakistan; Sahel; Arabian Peninsula",
    },
    {
        "name": "Taliban",
        "query": "Taliban AND (attack OR bombing OR \"suicide\" OR terror)",
        "region": "Afghanistan / Pakistan",
    },
    {
        "name": "Hamas",
        "query": "Hamas AND (rocket OR attack OR militant OR terror)",
        "region": "Gaza / West Bank / Israel",
    },
    {
        "name": "Hezbollah",
        "query": "Hezbollah AND (rocket OR attack OR border OR missile OR terror)",
        "region": "Lebanon / Syria / Israel borders",
    },
    {
        "name": "Al-Shabaab",
        "query": "\"al-Shabaab\" OR \"Al Shabaab\"",
        "region": "Somalia / Kenya",
    },
    {
        "name": "Boko Haram",
        "query": "\"Boko Haram\"",
        "region": "Nigeria / Lake Chad Basin",
    },
    {
        "name": "Abu Sayyaf Group (ASG)",
        "query": "\"Abu Sayyaf\" OR \"Abu Sayyaf Group\"",
        "region": "Southern Philippines / Sulu archipelago",
    },
    {
        "name": "New IRA / dissident IRA",
        "query": "\"New IRA\" OR \"Irish Republican Army\"",
        "region": "Northern Ireland / Great Britain",
    },
    {
        "name": "ETA remnants",
        "query": "ETA AND (Spain OR Basque) AND (terror OR bombing OR attack)",
        "region": "Spain / Basque Country",
    },
]


def fetch_terrorism_alerts(max_orgs: int = 8, max_headlines_per_org: int = 2):
    """
    Build terrorism / counter-terrorism updates by tracking a roster
    of major terrorist organizations and pulling recent headlines
    for each via NewsAPI (through fetch_news).

    Output is shaped like:
      { title, source, excerpt, url, date, location_hint }
    which the existing PDF builder already expects.
    """
    alerts = []

    if not NEWSAPI_KEY:
        print("[fetch_terrorism_alerts] NEWSAPI_KEY not set; skipping terrorism updates.")
        return alerts

    for profile in TERROR_ORG_PROFILES[:max_orgs]:
        query = profile["query"]
        region = profile["region"]
        name = profile["name"]

        try:
            articles = fetch_news(query, max_articles=max_headlines_per_org)
        except Exception as e:
            print(f"[fetch_terrorism_alerts] news fetch failed for {name}: {e}")
            continue

        # Build a compact synopsis line from 0–2 headlines
        bits = []
        first_url = ""

        for a in articles:
            title = ensure_english(_safe(a.get("title")))
            if not title:
                continue
            src = _safe(a.get("source") or "")
            if src:
                bits.append(f"{title} ({src})")
            else:
                bits.append(title)
            if not first_url:
                first_url = a.get("url") or ""

        if not bits:
            # No fresh mainstream coverage; still show baseline risk profile
            excerpt = f"No major new mainstream reporting in the last month; core areas: {region}."
        else:
            excerpt = " | ".join(bits)
            # Trim very long synopses
            max_len = 280
            if len(excerpt) > max_len:
                cut = excerpt.rfind(". ", 0, max_len)
                if cut == -1:
                    cut = max_len
                excerpt = excerpt[:cut].rstrip() + "..."

        alerts.append(
            {
                # headline line in PDF
                "title": name,
                # will appear after dash in the header line
                "source": "Org focus",
                # short synopsis under the header
                "excerpt": excerpt,
                # first relevant link if present
                "url": first_url,
                # we don't track dates per org in this simple version
                "date": "",
                # shows up in square brackets e.g. [Iraq / Syria; global networks]
                "location_hint": region,
            }
        )

    return alerts



# =========================================================
# Heuristics & text utilities
# =========================================================

US_DOMESTIC_KEYWORDS = [
    "united states", "u.s.", "usa", "michigan", "texas", "california", "florida", "ny", "washington", "detroit"
]

def is_us_domestic(alert):
    t = (_safe(alert.get("title")) + " " + _safe(alert.get("location_hint"))).lower()
    return any(kw in t for kw in US_DOMESTIC_KEYWORDS)

def severity_from_text(alert):
    text = (_safe(alert.get("title")) + " " + _safe(alert.get("excerpt"))).lower()
    if any(k in text for k in ["attack","bomb","explosion","hostage","shooting"]):
        return "severe"
    if any(k in text for k in ["foiled","arrest","plot","cell","raid"]):
        return "elevated"
    if any(k in text for k in ["threat","warn","suspicious"]):
        return "guarded"
    return "routine"

    TRUE_TERROR_POSITIVE = [
        "terrorist attack",
        "terror attack",
        "terrorist bombing",
        "suicide bombing",
        "suicide bomb",
        "car bomb",
        "improvised explosive device",
        "ied",
        "roadside bomb",
        "jihadist",
        "terrorist group",
        "militant group",
        "insurgent group",
        "extremist group",
        "islamic state",
        "isis",
        "isil",
        "daesh",
        "al-qaeda",
        "al qaeda",
        "al-qaida",
        "boko haram",
        "al-shabaab",
        "hezbollah",
        "hamas",
        "taliban",
]

    TRUE_TERROR_NEGATIVE = [
        "domestic dispute",
        "domestic violence",
        "road rage",
        "drug bust",
        "drug trafficking",
        "narcotics",
        "gang shooting",
        "gang-related",
        "armed robbery",
        "robbery",
        "shoplifting",
        "burglary",
        "home invasion",
        "carjacking",
        "police chase",
        "stabbing after argument",
        "bar fight",
]    


def looks_like_true_terrorism(title: str, desc: str = "") -> bool:
    """Heuristic to keep real terrorism/CT stories and drop generic local crime."""
    text = f"{_safe(title)} {_safe(desc)}".lower()

    # Must contain at least one terrorism-specific signal
    if not any(k in text for k in TRUE_TERROR_POSITIVE):
        return False

    # Drop if clearly just generic crime keywords
    if any(k in text for k in TRUE_TERROR_NEGATIVE):
        return False

    return True


SEVERITY_BADGES = {
    "severe":   ("🔴", colors.red),
    "elevated": ("🟠", colors.orange),
    "guarded":  ("🟡", colors.gold),
    "routine":  ("🟢", colors.green),
}

def make_exec_summary_full(geopolitics, economics, finance, terrorism, travel_warnings):
    """Compose a compact, 3–4 sentence executive summary (auto-translate inputs)."""
    gp = geopolitics[:1]; ec = economics[:1]; fi = finance[:1]
    te_n = len(terrorism); tr_n = len(travel_warnings)
    parts = []
    parts.append("Global risk posture remains mixed; alliance signaling, economic prints, and security alerts continue to shape decision space.")
    if gp and ec:
        parts.append(f'Geopolitically, “{ensure_english(_safe(gp[0].get("title")))}”; macro backdrop tracks “{ensure_english(_safe(ec[0].get("title")))},” with implications for energy, trade, and inflation.')
    elif gp:
        parts.append(f'Geopolitically, “{ensure_english(_safe(gp[0].get("title")))}” influences regional deterrence and coalition management.')
    elif ec:
        parts.append(f'Macro backdrop tracks “{ensure_english(_safe(ec[0].get("title")))},” affecting supply chains and policy path.')
    if fi:
        parts.append(f'Markets remain headline-sensitive; finance highlights include “{ensure_english(_safe(fi[0].get("title")))}.”')
    else:
        parts.append('Markets remain headline-sensitive to policy, energy, and security developments.')
    if te_n or tr_n:
        tail = f"Security posture reflects {te_n} terrorism-related items" if te_n else ""
        if tr_n:
            tail += (" and " if tail else "") + f"{tr_n} travel advisories"
        parts.append(tail + ", warranting ongoing monitoring for cascading effects.")
    else:
        parts.append("No priority security alerts or new travel advisories surfaced in the latest cycle.")
    return " ".join(parts)

def summarize_section_paragraphs(items, max_items=20, max_chars=320):
    """
    Turn a list of articles into several short, per-topic paragraphs
    instead of one long block.

    - Each item becomes its own paragraph: "Title: description"
    - Description is trimmed to keep each paragraph readable.
    """
    if not items:
        return ["No notable items this cycle."]

    paragraphs = []

    for i in items[:max_items]:
        title = ensure_english(_safe(i.get("title")))
        desc  = ensure_english(_safe(i.get("description")))

        if not title and not desc:
            continue

        if title and desc:
            para = f"{title}: {desc}"
        elif title:
            para = title
        else:
            para = desc

        # Trim overly long paragraphs to avoid walls of text
        if len(para) > max_chars:
            cut = para.rfind(". ", 0, max_chars)
            if cut == -1:
                cut = max_chars
            para = para[:cut].rstrip() + "…"

        paragraphs.append(para)

    if not paragraphs:
        return ["No notable items this cycle."]

    return paragraphs


def strip_html_tags(text: str) -> str:
    """Convert HTML to plain text for safe use in ReportLab Paragraphs."""
    if not text:
        return ""
    # Drop the adhocenable attribute specifically (and any similar junk)
    text = re.sub(r'\sadhocenable="[^"]*"', '', text)
    # Remove all HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text)
    # Decode HTML entities like &nbsp;
    text = html.unescape(text).strip()

    # Optionally trim very long blurbs so they don’t become a wall of text
    max_len = 400
    if len(text) > max_len:
        cut = text.rfind(". ", 0, max_len)
        if cut == -1:
            cut = max_len
        text = text[:cut].rstrip() + "…"

    return text


# =========================================================
# PDF builder (single-column, clean spacing)
# =========================================================
def build_pdf(pdf_filename, exec_summary, sections, terrorism_items, travel_items, greynoise_map=None):
    page_w, page_h = letter
    left, right, top, bottom = 0.9*inch, 0.9*inch, 0.8*inch, 0.8*inch

    doc = BaseDocTemplate(
        pdf_filename,
        pagesize=letter,
        leftMargin=left, rightMargin=right,
        topMargin=top, bottomMargin=bottom,
    )
    frame = Frame(left, bottom, page_w - left - right, page_h - top - bottom, id="body")

    def on_page(canvas, doc_):
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawString(left, 0.45*inch, "Prepared Automatically — For Internal Review")
        canvas.drawRightString(page_w - right, 0.45*inch, f"Page {doc_.page}")

    doc.addPageTemplates([PageTemplate(id="OneCol", frames=[frame], onPage=on_page)])

    styles = getSampleStyleSheet()
    Title = ParagraphStyle("Title", parent=styles["Heading1"], fontName="Helvetica-Bold",
                           fontSize=18, alignment=TA_CENTER, spaceAfter=4)
    SubTitle = ParagraphStyle("SubTitle", parent=styles["Normal"], fontName="Helvetica-Oblique",
                              fontSize=11, textColor=colors.darkgrey, alignment=TA_CENTER, spaceAfter=2)
    DateLine = ParagraphStyle("DateLine", parent=styles["Normal"], fontName="Helvetica-Oblique",
                              fontSize=9, textColor=colors.grey, alignment=TA_RIGHT, spaceAfter=0)
    H2 = ParagraphStyle("H2", parent=styles["Heading2"], fontName="Helvetica-Bold",
                        fontSize=13, spaceBefore=16, spaceAfter=10, textColor=colors.darkblue)
    Body = ParagraphStyle("Body", parent=styles["Normal"], fontName="Helvetica",
                          fontSize=11, leading=15, spaceAfter=8, leftIndent=10)
    BodyDesc = ParagraphStyle(
        "BodyDesc",
        parent=Body,
        fontSize=10,
        leading=13,
        leftIndent=14,
        spaceAfter=4,
    )

    Small = ParagraphStyle("Small", parent=styles["Normal"], fontName="Helvetica",
                           fontSize=9, leading=12, textColor=colors.grey, spaceAfter=10, leftIndent=10)
    Impact = ParagraphStyle("Impact", parent=styles["Normal"], fontName="Helvetica-Oblique",
                            fontSize=10, leading=14, textColor=colors.black)

    story = []

    # --- Title banner ---
    today = datetime.now(timezone.utc).astimezone().strftime("%B %d, %Y")
    title_para = Paragraph("THE DAILY BRIEF", Title)
    subtitle_para = Paragraph("Strategic Intelligence Summary", SubTitle)
    date_para = Paragraph(today, DateLine)
    banner = Table([[title_para],[subtitle_para],[date_para]], colWidths=[page_w-left-right])
    banner.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), colors.whitesmoke),
        ("BOX", (0,0), (-1,-1), 0.25, colors.lightgrey),
        ("TOPPADDING", (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ]))
    story.append(banner)
    story.append(Spacer(1, 14))
    story.append(HR(width="100%", thickness=1.0, color=colors.lightgrey))
    story.append(Spacer(1, 14))

        # --- Executive Summary ---
    story.append(Paragraph("EXECUTIVE SUMMARY", H2))

    # Break the executive summary into short paragraphs like the other sections
    parts = [s.strip() for s in re.split(r"(?<=[.!?])\s+", exec_summary) if s.strip()]
    if not parts:
        parts = [exec_summary]

    for part in parts:
        story.append(Paragraph(part, Body))

    story.append(Spacer(1, 16))


    # --- Sections ---
        # --- Sections ---
    for title, items, impact in sections:
        story.append(Paragraph(title, H2))

        # How many headlines you want per section
        max_items = 20  # bump to 10–12 if you want more

        for art in items[:max_items]:
            art_title = ensure_english(_safe(art.get("title")))
            art_desc  = ensure_english(_safe(art.get("description")))
            art_src   = ensure_english(_safe(art.get("source")))
            art_url   = _safe(art.get("url"))

            # Skip totally empty rows
            if not art_title and not art_desc:
                continue

            # If NewsAPI didn't give a source but we have a URL, try to derive a domain
            if not art_src and art_url:
                try:
                    from urllib.parse import urlparse
                    dom = urlparse(art_url).netloc
                    if dom:
                        art_src = dom.lstrip("www.")
                except Exception:
                    pass

            # Headline line: Title — Source
            if art_title:
                header = f"<b>{art_title}</b>"
                if art_src:
                    header += f" — {art_src}"
                story.append(Paragraph(header, Body))

            # Description line
            if art_desc:
                story.append(Paragraph(art_desc, BodyDesc))

            # Hyperlink line (always clickable if url exists)
            if art_url:
                story.append(Paragraph(f'<link href="{art_url}">{art_url}</link>', Small))

            story.append(Spacer(1, 6))

        # Strategic impact box (unchanged)
        box = Table([[Paragraph(f"<b>Strategic Impact:</b> {impact}", Impact)]],
                    colWidths=[page_w-left-right])
        box.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#F3F5F7")),
            ("BOX", (0,0), (-1,-1), 0.25, colors.lightgrey),
            ("LEFTPADDING", (0,0), (-1,-1), 8),
            ("RIGHTPADDING", (0,0), (-1,-1), 8),
            ("TOPPADDING", (0,0), (-1,-1), 6),
            ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ]))
        story.append(box)
        story.append(Spacer(1, 16))
        story.append(HR(width="100%", thickness=0.6, color=colors.lightgrey))
        story.append(Spacer(1, 12))
        

    # --- Terrorism ---
    story.append(Paragraph("TERRORISM & COUNTER-TERRORISM", H2))
    if not terrorism_items:
        story.append(Paragraph("No major terrorism alerts detected.", Body))
    else:
        for t in terrorism_items[:8]:
            sev = severity_from_text(t)
            icon, color = SEVERITY_BADGES.get(sev, ("", colors.black))
            head = f"{icon} <b>{ensure_english(_safe(t.get('title')))}</b>"
            src = ensure_english(_safe(t.get("source")))
            loc = ensure_english(_safe(t.get("location_hint") or t.get("date")))
            if src:
                head += f" — {src}"
            if loc:
                head += f" [{loc}]"
            Sev = ParagraphStyle(f"Sev_{sev}", parent=Body, textColor=color, leftIndent=10)
            story.append(Paragraph(head, Sev))
            excerpt = ensure_english(_safe(t.get("excerpt")))
            if excerpt:
                story.append(Paragraph(excerpt, Body))
            url = _safe(t.get("url"))
            if url:
                story.append(Paragraph(f'<link href="{url}">{url}</link>', Small))
            story.append(Spacer(1, 4))

    t_box = Table([[Paragraph("<b>Strategic Impact:</b> Domestic plots prioritized; monitor transnational links, online facilitation, and intel-sharing posture.", Impact)]],
                  colWidths=[page_w-left-right])
    t_box.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#F3F5F7")),
        ("BOX", (0,0), (-1,-1), 0.25, colors.lightgrey),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("RIGHTPADDING", (0,0), (-1,-1), 8),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ]))
    story.append(t_box)
    story.append(Spacer(1, 16))
    story.append(HR(width="100%", thickness=0.6, color=colors.lightgrey))
    story.append(Spacer(1, 12))

    # --- Travel warnings (title + desc + hyperlink) ---
    story.append(Paragraph("TRAVEL WARNINGS", H2))
    if not travel_items:
        story.append(Paragraph("No current State Department travel alerts or warnings in feed.", Body))
    else:
        max_travel_items = 200  # or whatever cap you want
        for w in travel_items[:max_travel_items]:
            title = ensure_english(_safe(w.get("title")))
            raw_desc = _safe(w.get("desc"))
            desc = strip_html_tags(ensure_english(raw_desc))
            link  = _safe(w.get("link"))

            story.append(Paragraph(title, Body))

            if desc:
                story.append(Paragraph(desc, ParagraphStyle("Small", parent=Small)))

            if link:
                story.append(Paragraph(f'<link href="{link}">{link}</link>', Small))

            story.append(Spacer(1, 6))



    # --- Build PDF ---
    try:
        doc.build(story)
    except PermissionError:
        import time
        alt = pdf_filename.replace(".pdf", f"_{int(time.time())}.pdf")
        print(f"[main] File in use, saving as {alt}")
        doc.filename = alt
        doc.build(story)

    print(f"[main] PDF created: {pdf_filename}")




# =========================================================
# Email + Drive + Cleanup
# =========================================================

def send_pdf_via_sendgrid(pdf_filename):
    if not (SENDGRID_API_KEY and EMAIL_FROM and EMAIL_TO):
        print("[sendgrid] Missing env; skipping email.")
        return
    try:
        with open(pdf_filename, "rb") as f:
            data = f.read()
        encoded = base64.b64encode(data).decode()
        message = Mail(
            from_email=EMAIL_FROM,
            to_emails=EMAIL_TO,
            subject=f"The Daily Brief — {datetime.now().strftime('%B %d, %Y')}",
            html_content="<p>Attached is your latest Daily Brief.</p>",
        )
        attachment = Attachment(
            FileContent(encoded),
            FileName(os.path.basename(pdf_filename)),
            FileType("application/pdf"),
            Disposition("attachment"),
        )
        message.attachment = attachment
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        sg.send(message)
        print("[sendgrid] Sent OK")
    except Exception as e:
        print(f"[sendgrid] Send failed: {e}")

def backup_to_drive(pdf_filename):
    """Backs up the generated PDF to Google Drive if credentials exist (PyDrive2)."""
    if not HAVE_PYDRIVE2:
        print("[drive] PyDrive2 not installed; skipping Drive backup.")
        return
    if not os.path.exists("client_secrets.json"):
        print("[drive] client_secrets.json not found; skipping Drive backup.")
        return
    try:
        gauth = GoogleAuth()
        gauth.LoadClientConfigFile("client_secrets.json")
        gauth.LoadCredentialsFile("mycreds.txt")
        if gauth.credentials is None:
            gauth.LocalWebserverAuth()
        elif gauth.access_token_expired:
            gauth.Refresh()
        else:
            gauth.Authorize()
        gauth.SaveCredentialsFile("mycreds.txt")

        drive = GoogleDrive(gauth)
        folder_name = "DailyBrief Reports"
        query = f"title = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed=false"
        fl = drive.ListFile({"q": query}).GetList()
        if not fl:
            folder = drive.CreateFile({"title": folder_name, "mimeType": "application/vnd.google-apps.folder"})
            folder.Upload()
            folder_id = folder["id"]
        else:
            folder_id = fl[0]["id"]

        f = drive.CreateFile({"parents": [{"id": folder_id}], "title": os.path.basename(pdf_filename)})
        f.SetContentFile(pdf_filename)
        f.Upload()
        print("[drive] Uploaded to Google Drive.")
    except Exception as e:
        print(f"[drive] Upload failed: {e}")

def cleanup_old_reports(directory="C:\\DailyBrief", keep=7):
    try:
        pdfs = sorted(
            [f for f in os.listdir(directory) if f.startswith("The_Daily_Brief_") and f.endswith(".pdf")],
            key=lambda x: os.path.getmtime(os.path.join(directory, x)),
            reverse=True,
        )
        for old in pdfs[keep:]:
            os.remove(os.path.join(directory, old))
        print(f"[cleanup] No cleanup needed. {min(len(pdfs), keep)} reports retained.")
    except Exception as e:
        print(f"[cleanup] Error: {e}")

# =========================================================
# Optional integrations (stubs): MISP, GreyNoise, Tearline
# =========================================================

IOC_IPv4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

def extract_iocs_from_text(*parts):
    text = " ".join([p for p in parts if p])
    return list(set(IOC_IPv4_RE.findall(text)))

def fetch_misp_events(days_back=2, tag_filter=("terrorism",)):
    try:
        from pymisp import PyMISP
        import datetime
        url = os.getenv("MISP_URL"); key = os.getenv("MISP_KEY")
        if not (url and key):
            return []
        misp = PyMISP(url, key, ssl=True, debug=False)
        since = (datetime.datetime.utcnow() - datetime.timedelta(days=days_back)).isoformat()
        events = misp.search(controller="events", metadata=True, tags=list(tag_filter), date_from=since).get("response", [])
        out = []
        for ev in events:
            e = ev.get("Event") or {}
            title = e.get("info") or "MISP event"
            link = f"{url.rstrip('/')}/events/view/{e.get('id')}"
            tags = [t["name"] for t in (e.get("Tag") or [])]
            out.append({
                "title": title,
                "source": "MISP",
                "excerpt": " | ".join(tags)[:240],
                "url": link,
                "date": e.get("date") or "",
                "location_hint": "",
            })
        return out
    except Exception as e:
        print(f"[misp] fetch failed: {e}")
        return []

def greynoise_enrich(ip_list):
    try:
        from greynoise import GreyNoise
        api_key = os.getenv("GREYNOISE_API_KEY")
        if not (api_key and ip_list):
            return {}
        gn = GreyNoise(api_key=api_key, integration_name="daily-brief")
        out = {}
        for ip in set(ip_list):
            try:
                data = gn.ip(ip)
                out[ip] = {"classification": data.get("classification", "unknown"), "name": data.get("name") or ""}
            except Exception:
                out[ip] = {"classification": "unknown", "name": ""}
        return out
    except Exception as e:
        print(f"[greynoise] enrich failed: {e}")
        return {}

def fetch_tearline_latest(max_items=20):
    url = "https://www.tearline.mil/"
    try:
        html = requests.get(url, timeout=15).text
        links = re.findall(r'<a[^>]+href="(/[^\"]+)"[^>]*>([^<]{10,120})</a>', html, flags=re.I)
        out, seen = [], set()
        for href, text in links:
            full = f"https://www.tearline.mil{href}"
            title = re.sub(r"\s+", " ", text).strip()
            if not title or full in seen:
                continue
            seen.add(full)
            out.append({"title": title, "source": "NGA Tearline", "excerpt": "", "url": full, "date": "", "location_hint": ""})
            if len(out) >= max_items:
                break
        return out
    except Exception as e:
        print(f"[tearline] fetch failed: {e}")
        return []

# =========================================================
# Tests (run with RUN_TESTS=1)
# =========================================================

def _run_tests():
    print("[tests] starting…")
    # parse_travel_rss minimal RSS
    sample = b"""
    <rss><channel>
      <item><title>A</title><link>http://x/a</link><description>Desc A</description></item>
      <item><title>B</title><link>http://x/b</link><description>Desc B</description></item>
    </channel></rss>"""
    parsed = parse_travel_rss(sample)
    assert len(parsed) == 2 and parsed[0]['title']=="A" and parsed[1]['link']=="http://x/b"

    # summarize_section_paragraphs splitting
    items = [{"title":"T1","description":"D1."},{"title":"T2","description":"D2."}]
    paras = summarize_section_paragraphs(items, max_chars=20)
    assert len(paras) in (1,2)
    # severity heuristic
    sev = severity_from_text({"title":"Police foiled bomb plot","excerpt":""})
    assert sev in ("severe","elevated")
    print("[tests] ok")

# =========================================================
# Main
# =========================================================

if __name__ == "__main__":
    import sys, traceback, time

    # Always work in the script's folder so paths are predictable
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    try:
        print("[main] Fetching feeds...")

        # --- Fetch feeds (each guarded so one failure won't kill the run)
        geopolitics = []
        economics = []
        finance = []
        terrorism = []
        travel = []
        # --- GEOPOLITICS (extended sources, additive only)
        try:
            geopolitics = fetch_news("geopolitics", max_articles=20)

            geopolitics += fetch_bellingcat()

            geopolitics += fetch_html_analysis(
                "CSIS",
                "https://www.csis.org/analysis",
                r'href="(/analysis/[^"]+)".*?>([^<]{40,160})<'
            )

            geopolitics += fetch_html_analysis(
                "Critical Threats Project",
                "https://www.criticalthreats.org/analysis",
                r'href="(/analysis/[^"]+)".*?>([^<]{40,160})<'
            )

            geopolitics += fetch_html_analysis(
                "ACLED Analysis",
                "https://acleddata.com/analysis/",
                r'href="(/analysis/[^"]+)".*?>([^<]{40,160})<'
            )

        except Exception as e:
            print(f"[main] geopolitics fetch failed: {e}")

        # --- ECONOMICS
        try:
            economics = fetch_news("global economy", max_articles=20)
        except Exception as e:
            print(f"[main] economics fetch failed: {e}")

        # --- FINANCE
        try:
            finance = fetch_news("finance and markets", max_articles=20)
        except Exception as e:
            print(f"[main] finance fetch failed: {e}")

        # --- TERRORISM
        try:
            terrorism = fetch_terrorism_alerts()
        except Exception as e:
            print(f"[main] terrorism fetch failed: {e}")

        # Optional: MISP enrichment
        if os.getenv("MISP_URL") and os.getenv("MISP_KEY"):
            try:
                terrorism += fetch_misp_events()
            except Exception as e:
                print(f"[main] misp fetch failed: {e}")

        # --- TRAVEL WARNINGS
        try:
            travel = fetch_travel_warnings()
        except Exception as e:
            print(f"[main] travel warnings fetch failed: {e}")



        print("[main] Building Executive Summary...")
        exec_summary = make_exec_summary_full(geopolitics, economics, finance, terrorism, travel)

        sections = [
            ("GEOPOLITICS", geopolitics, "Alliance dynamics, deterrence posture, and global balance of power."),
            ("ECONOMICS", economics, "Inflation path, energy flows, and trade resilience."),
            ("FINANCE & STRATEGIC EFFECTS", finance, "Market sentiment, sanctions risk, and capital allocation."),
        ]

        date_suffix = datetime.now().strftime("%Y-%m-%d")
        pdf_filename = os.path.join(script_dir, f"The_Daily_Brief_{date_suffix}.pdf")

        print(f"[main] Creating PDF: {pdf_filename}")
        # Optional: IOC enrichment
        ips = []
        for a in terrorism:
            ips += extract_iocs_from_text(a.get("title"), a.get("excerpt"), a.get("url"))
        greynoise_map = greynoise_enrich(ips) if ips else {}

        build_pdf(pdf_filename, exec_summary, sections, terrorism, travel, greynoise_map)
        print("[main] Creating PDF complete.")

        send_pdf_via_gmail(pdf_filename)
        backup_to_drive(pdf_filename)
        cleanup_old_reports()
        print("[main] Done.")
    except SystemExit as e:
        print(f"[FATAL] SystemExit: {e}")
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"[FATAL] {type(e).__name__}: {e}")
        traceback.print_exc()
        sys.exit(1)

send_email(pdf_filename)

