import os
import json
import smtplib
import logging
import time
import random
import re
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from bs4 import BeautifulSoup

PRICE_THRESHOLD = float(os.getenv("PRICE_THRESHOLD", "70"))

SHOES = [
    "Brooks Ghost", "Brooks Glycerin", "Saucony Ride", "Saucony Triumph",
    "Saucony Guide", "Hoka Clifton", "Hoka Rincon", "Hoka Bondi",
    "New Balance 880", "New Balance 1080", "New Balance 2002R",
    "New Balance 1906R", "New Balance 990v5", "New Balance 990v6",
    "ASICS Gel-Cumulus", "ASICS Gel-Nimbus", "Adidas Ultraboost",
    "Saucony ProGrid Triumph 4", "Saucony ProGrid Omni 9",
    "Nike Pegasus", "Nike Vomero", "Nike Invincible Run",
]

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
ALERT_EMAIL = os.getenv("ALERT_EMAIL")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def search_google_shopping(shoe):
    query = f"{shoe} running shoes sale"
    url = f"https://www.google.com/search?q={requests.utils.quote(query)}&tbm=shop&hl=en&gl=us"
    deals = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for card in soup.select(".sh-dgr__grid-result, .KZmu8e, .i0X6df"):
            try:
                title_el = card.select_one(".tAxDx, .Xjkr3b, .BvQan")
                price_el = card.select_one(".a8Pemb, .kHxwFf, .OFFNJ")
                store_el = card.select_one(".aULzUe, .IuHnof, .LbUacb")
                link_el = card.select_one("a[href]")
                if not title_el or not price_el:
                    continue
                title = title_el.get_text(strip=True)
                price_raw = price_el.get_text(strip=True)
                store = store_el.get_text(strip=True) if store_el else "Unknown"
                href = link_el["href"] if link_el else ""
                price_str = re.sub(r"[^\d.]", "", price_raw.split("–")[0])
                if not price_str:
                    continue
                price = float(price_str)
                if price <= PRICE_THRESHOLD:
                    deals.append({
                        "shoe": shoe, "title": title, "price": price,
                        "store": store,
                        "url": f"https://www.google.com{href}" if href.startswith("/") else href,
                    })
            except (ValueError, AttributeError):
                continue
    except requests.RequestException as e:
        log.warning("Google request failed for '%s': %s", shoe, e)
    return deals


def search_ebay(shoe):
    query = f"{shoe} running shoes"
    url = (
        f"https://www.ebay.com/sch/i.html?_nkw={requests.utils.quote(query)}"
        f"&_sop=15&LH_BIN=1&_udhi={int(PRICE_THRESHOLD)}"
    )
    deals = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for item in soup.select(".s-item")[:10]:
            try:
                title_el = item.select_one(".s-item__title")
                price_el = item.select_one(".s-item__price")
                link_el = item.select_one(".s-item__link")
                if not title_el or not price_el or not link_el:
                    continue
                title = title_el.get_text(strip=True)
                if "Shop on eBay" in title:
                    continue
                price_raw = price_el.get_text(strip=True)
                price_str = re.sub(r"[^\d.]", "", price_raw.split(" to ")[0])
                if not price_str:
                    continue
                price = float(price_str)
                if price <= PRICE_THRESHOLD:
                    deals.append({
                        "shoe": shoe, "title": title, "price": price,
                        "store": "eBay", "url": link_el["href"],
                    })
            except (ValueError, AttributeError):
                continue
    except requests.RequestException as e:
        log.warning("eBay request failed for '%s': %s", shoe, e)
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
