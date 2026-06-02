import os
import json
import smtplib
import logging
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

PRICE_THRESHOLD = float(os.getenv("PRICE_THRESHOLD", "70"))
SERPAPI_KEY = os.getenv("SERPAPI_KEY")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
ALERT_EMAIL = os.getenv("ALERT_EMAIL")

SHOES = [
    "Brooks Ghost", "Brooks Glycerin", "Saucony Ride", "Saucony Triumph",
    "Saucony Guide", "Hoka Clifton", "Hoka Rincon", "Hoka Bondi",
    "New Balance 880", "New Balance 1080", "New Balance 2002R",
    "New Balance 1906R", "New Balance 990v5", "New Balance 990v6",
    "ASICS Gel-Cumulus", "ASICS Gel-Nimbus", "Adidas Ultraboost",
    "Saucony ProGrid Triumph 4", "Saucony ProGrid Omni 9",
    "Nike Pegasus", "Nike Vomero", "Nike Invincible Run",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def search_shoe(shoe):
    deals = []
    try:
        resp = requests.get("https://serpapi.com/search", params={
            "engine": "google_shopping",
            "q": f"{shoe} running shoes",
            "api_key": SERPAPI_KEY,
            "gl": "us",
            "hl": "en",
        }, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("shopping_results", []):
            try:
                price = float(str(item.get("price", "")).replace("$", "").replace(",", ""))
                if price <= PRICE_THRESHOLD:
                    deals.append({
                        "shoe": shoe,
                        "title": item.get("title", ""),
                        "price": price,
                        "store": item.get("source", "Unknown"),
                        "url": item.get("link", ""),
                    })
            except (ValueError, TypeError):
                continue

    except Exception as e:
        log.warning("Search failed for '%s': %s", shoe, e)

    return deals


def build_html_email(deals):
    rows = ""
    for d in sorted(deals, key=lambda x: x["price"]):
        rows += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;font-weight:600">{d['shoe']}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee">{d['title'][:60]}{'…' if len(d['title'])>60 else ''}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;color:#16a34a;font-weight:700">${d['price']:.2f}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee">{d['store']}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee">
            <a href="{d['url']}" style="color:#2563eb;text-decoration:none">View Deal →</a>
          </td>
        </tr>"""
    return f"""
    <html><body style="font-family:Arial,sans-serif;background:#f9fafb;padding:24px">
      <div style="max-width:780px;margin:auto;background:#fff;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.08);overflow:hidden">
        <div style="background:#111;padding:24px 32px">
          <h1 style="color:#fff;margin:0;font-size:22px">👟 Shoe Deal Alert</h1>
          <p style="color:#aaa;margin:4px 0 0">{len(deals)} deal(s) found under ${PRICE_THRESHOLD:.0f} — {datetime.now().strftime('%B %d, %Y')}</p>
        </div>
        <div style="padding:24px 32px">
          <table style="width:100%;border-collapse:collapse;font-size:14px">
            <thead>
              <tr style="background:#f3f4f6">
                <th style="padding:10px 12px;text-align:left">Shoe</th>
                <th style="padding:10px 12px;text-align:left">Listing</th>
                <th style="padding:10px 12px;text-align:left">Price</th>
                <th style="padding:10px 12px;text-align:left">Store</th>
                <th style="padding:10px 12px;text-align:left">Link</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
        <div style="padding:16px 32px;background:#f9fafb;font-size:12px;color:#6b7280">
          Threshold: ${PRICE_THRESHOLD:.2f} • Ran at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
        </div>
      </div>
    </body></html>"""


def send_email(deals):
    if not all([GMAIL_USER, GMAIL_APP_PASSWORD, ALERT_EMAIL]):
        log.error("Email secrets not set.")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"👟 {len(deals)} Shoe Deal(s) Under ${PRICE_THRESHOLD:.0f} Found!"
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
    log.info("Starting scan — threshold $%.2f — %d shoes", PRICE_THRESHOLD, len(SHOES))
    all_deals = []

    for shoe in SHOES:
        log.info("Scanning: %s", shoe)
        deals = search_shoe(shoe)
        if deals:
            log.info("  ✓ %d deal(s) for %s", len(deals), shoe)
        else:
            log.info("  – No deals under $%.2f for %s", PRICE_THRESHOLD, shoe)
        all_deals.extend(deals)
        time.sleep(1)

    with open("scan_results.json", "w") as f:
        json.dump({
            "run_at": datetime.utcnow().isoformat(),
            "threshold": PRICE_THRESHOLD,
            "deals_found": len(all_deals),
            "deals": all_deals,
        }, f, indent=2)

    if all_deals:
        send_email(all_deals)
    else:
        log.info("No deals found — no email sent.")


if __name__ == "__main__":
    run()
