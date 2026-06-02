import os
import json
import smtplib
import logging
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

PRICE_THRESHOLD = float(os.getenv("PRICE_THRESHOLD", "70"))
SERPAPI_KEY = os.getenv("SERPAPI_KEY")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
ALERT_EMAIL = os.getenv("ALERT_EMAIL")
HISTORY_FILE = "deal_history.json"

SHOES = [
    "Brooks Ghost", "Brooks Glycerin", "Saucony Ride", "Saucony Triumph",
    "Saucony Guide", "Hoka Clifton", "Hoka Rincon", "Hoka Bondi",
    "New Balance 880", "New Balance 1080", "New Balance 2002R",
    "New Balance 1906R", "New Balance 990v5", "New Balance 990v6",
    "ASICS Gel-Cumulus", "ASICS Gel-Nimbus", "Adidas Ultraboost",
    "Saucony ProGrid Triumph 4", "Saucony ProGrid Omni 9",
    "Nike Pegasus", "Nike Vomero", "Nike Invincible Run",
]

GOOD_STORES = [
    "saucony", "new balance", "brooks", "hoka", "asics", "nike", "adidas",
    "nordstrom rack", "nordstrom", "sierra", "dick's sporting goods", "dicks",
    "foot locker", "finish line", "shoe palace", "asos", "running warehouse",
    "road runner sports", "zappos", "dtlr", "jd sports", "academy",
    "al's sporting goods", "shoebacca",
]

BAD_STORES = [
    "ebay", "aircoseals", "apollostore", "pricescreen", "ravir",
    "geartrade", "poshmark", "mercari", "aliexpress", "alibaba",
    "wish", "temu", "dhgate",
]

BAD_TITLE_TERMS = [
    "women", "woman", "womens", "women's", "ladies", "lady",
    "kid", "kids", "kid's", "boys", "girls", "girl", "youth",
    "grade school", "gs", "toddler", "infant", "baby", "junior",
    "preschool", "ps ", "td ", "big kid",
]

# Minimum model versions to accept (older = reject)
MIN_VERSIONS = {
    "Ghost": 15,
    "Glycerin": 21,
    "Ride": 16,
    "Clifton": 9,
    "Rincon": 3,
    "Pegasus": 39,
    "Vomero": 17,
    "Cumulus": 25,
    "Nimbus": 25,
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE) as f:
            return json.load(f)
    return {}


def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def is_good_store(store: str) -> bool:
    s = store.lower()
    for bad in BAD_STORES:
        if bad in s:
            return False
    for good in GOOD_STORES:
        if good in s:
            return True
    return False  # unknown store — reject


def is_bad_title(title: str) -> bool:
    t = title.lower()
    return any(term in t for term in BAD_TITLE_TERMS)


def has_right_size(title: str) -> bool:
    """Return True if title mentions size 11 men's, or no size at all."""
    t = title.lower()
    # If title mentions a specific size that isn't 11, reject
    import re
    size_matches = re.findall(r'\bsize\s*(\d+\.?\d*)\b', t)
    if size_matches:
        for s in size_matches:
            if float(s) != 11.0:
                return False
    # Also reject half sizes that aren't 11
    explicit = re.findall(r'\b(\d+\.5)\b', t)
    for s in explicit:
        if float(s) != 11.5:
            pass  # 11.5 is close enough, allow
    return True


def is_old_model(title: str) -> bool:
    """Reject listings for outdated model versions."""
    import re
    t = title.lower()
    for model, min_ver in MIN_VERSIONS.items():
        if model.lower() in t:
            nums = re.findall(rf'{model.lower()}\s*(\d+)', t)
            for n in nums:
                if int(n) < min_ver:
                    return True
    return False


def score_deal(deal: dict) -> int:
    """Score a deal 1-10. Higher = better."""
    score = 5
    store = deal["store"].lower()
    price = deal["price"]
    title = deal["title"].lower()

    # Trusted brand stores get bonus
    brand_stores = ["saucony", "brooks", "new balance", "hoka", "asics", "nike", "adidas"]
    if any(b in store for b in brand_stores):
        score += 2

    # Price tiers
    if price < 40:
        score += 3
    elif price < 55:
        score += 2
    elif price < 65:
        score += 1

    # Known good retailers
    if any(s in store for s in ["nordstrom", "zappos", "running warehouse", "road runner"]):
        score += 1

    return min(score, 10)


def is_new_or_cheaper(deal: dict, history: dict) -> bool:
    """Only alert if this deal is new or at least 10% cheaper than last seen."""
    key = f"{deal['shoe']}|{deal['store']}"
    if key not in history:
        return True
    last_price = history[key]["price"]
    return deal["price"] <= last_price * 0.90


def search_shoe(shoe: str) -> list:
    deals = []
    try:
        resp = requests.get("https://serpapi.com/search", params={
            "engine": "google_shopping",
            "q": f"{shoe} men's size 11 running shoes",
            "api_key": SERPAPI_KEY,
            "gl": "us",
            "hl": "en",
        }, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("shopping_results", []):
            try:
                price = float(str(item.get("price", "")).replace("$", "").replace(",", ""))
                title = item.get("title", "")
                store = item.get("source", "")

                if price > PRICE_THRESHOLD:
                    continue
                if not is_good_store(store):
                    log.debug("  Skip bad store: %s", store)
                    continue
                if is_bad_title(title):
                    log.debug("  Skip women/kids: %s", title)
                    continue
                if is_old_model(title):
                    log.debug("  Skip old model: %s", title)
                    continue
                if not has_right_size(title):
                    log.debug("  Skip wrong size: %s", title)
                    continue

                deals.append({
                    "shoe": shoe,
                    "title": title,
                    "price": price,
                    "store": store,
                    "url": item.get("link", ""),
                })
            except (ValueError, TypeError):
                continue

    except Exception as e:
        log.warning("Search failed for '%s': %s", shoe, e)

    return deals


def build_html_email(deals: list) -> str:
    scored = sorted(deals, key=lambda x: (-score_deal(x), x["price"]))
    rows = ""
    for d in scored:
        s = score_deal(d)
        bar = "🔥" * min(s // 3, 3)
        rows += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;font-weight:600">{d['shoe']}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee">{d['title'][:65]}{'…' if len(d['title'])>65 else ''}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;color:#16a34a;font-weight:700">${d['price']:.2f}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee">{d['store']}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;font-size:18px">{bar}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee">
            <a href="{d['url']}" style="color:#2563eb;text-decoration:none">View →</a>
          </td>
        </tr>"""

    return f"""
    <html><body style="font-family:Arial,sans-serif;background:#f9fafb;padding:24px">
      <div style="max-width:820px;margin:auto;background:#fff;border-radius:8px;
                  box-shadow:0 1px 4px rgba(0,0,0,.08);overflow:hidden">
        <div style="background:#111;padding:24px 32px">
          <h1 style="color:#fff;margin:0;font-size:22px">👟 Shoe Deal Alert</h1>
          <p style="color:#aaa;margin:4px 0 0">{len(deals)} deal(s) under ${PRICE_THRESHOLD:.0f} — Men's Size 11 — {datetime.now().strftime('%B %d, %Y')}</p>
        </div>
        <div style="padding:24px 32px">
          <table style="width:100%;border-collapse:collapse;font-size:14px">
            <thead>
              <tr style="background:#f3f4f6">
                <th style="padding:10px 12px;text-align:left">Shoe</th>
                <th style="padding:10px 12px;text-align:left">Listing</th>
                <th style="padding:10px 12px;text-align:left">Price</th>
                <th style="padding:10px 12px;text-align:left">Store</th>
                <th style="padding:10px 12px;text-align:left">Score</th>
                <th style="padding:10px 12px;text-align:left">Link</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
        <div style="padding:16px 32px;background:#f9fafb;font-size:12px;color:#6b7280">
          Filters: Men's size 11 • Trusted stores only • New deals or 10%+ price drops only<br>
          Ran at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
        </div>
      </div>
    </body></html>"""


def send_email(deals: list) -> None:
    if not all([GMAIL_USER, GMAIL_APP_PASSWORD, ALERT_EMAIL]):
        log.error("Email secrets not set.")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"👟 {len(deals)} Shoe Deal(s) Under ${PRICE_THRESHOLD:.0f} — Men's Size 11"
    msg["From"] = GMAIL_USER
    msg["To"] = ALERT_EMAIL
    plain = "\n".join(
        f"{d['shoe']} — ${d['price']:.2f} at {d['store']}: {d['url']}"
        for d in sorted(deals, key=lambda x: x["price"])
    )
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(build_html_email(deals), "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, ALERT_EMAIL, msg.as_string())
    log.info("Email sent with %d deal(s).", len(deals))


def run():
    log.info("Starting scan — threshold $%.2f — %d shoes — Men's Size 11", PRICE_THRESHOLD, len(SHOES))
    history = load_history()
    all_deals = []

    for shoe in SHOES:
        log.info("Scanning: %s", shoe)
        deals = search_shoe(shoe)
        new_deals = [d for d in deals if is_new_or_cheaper(d, history)]

        if new_deals:
            log.info("  ✓ %d new/cheaper deal(s) for %s", len(new_deals), shoe)
        else:
            log.info("  – Nothing new for %s", shoe)

        all_deals.extend(new_deals)
        time.sleep(1)

    # Update history
    for d in all_deals:
        key = f"{d['shoe']}|{d['store']}"
        history[key] = {"price": d["price"], "last_seen": datetime.now(timezone.utc).isoformat()}
    save_history(history)

    with open("scan_results.json", "w") as f:
        json.dump({
            "run_at": datetime.now(timezone.utc).isoformat(),
            "threshold": PRICE_THRESHOLD,
            "deals_found": len(all_deals),
            "deals": all_deals,
        }, f, indent=2)

    if all_deals:
        send_email(all_deals)
    else:
        log.info("No new deals today — no email sent.")


if __name__ == "__main__":
    run()
