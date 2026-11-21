
"""Fetch latest earnings disclosures for tickers.csv.

TEST MODE:
- Does not modify avwap.py or manual disclosure records.
- Writes output/tdnet_auto_candidates.csv only.
"""
import csv
import os
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / os.environ.get("TDNET_OUTPUT_FILE", "output/tdnet_auto_candidates.csv")
TODAY = datetime.now(ZoneInfo("Asia/Tokyo")).date()

FIELDS = [
    "code", "disclosure_date", "disclosure_time",
    "title", "source_url", "status"
]


def get_codes():
    with (ROOT / os.environ.get("TDNET_TICKERS_FILE", "tickers.csv")).open(
        encoding="utf-8-sig", newline=""
    ) as f:
        return list(dict.fromkeys(
            row["code"].strip()
            for row in csv.DictReader(f)
            if len(row.get("code", "").strip()) == 4 and row.get("code", "").strip().isalnum()
        ))


def parse_datetime(value):
    if not value:
        return None
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(
            text.replace("Z", "+00:00")
        )
        if dt.tzinfo:
            dt = dt.astimezone(ZoneInfo("Asia/Tokyo"))
        return dt
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def fetch_one(code):
    
    url = (
        "https://webapi.yanoshin.jp/webapi/"
        f"tdnet/list/{code}.json?limit=1000"
    )
    request = Request(url, headers={
        "User-Agent": "jp-avwap-research/0.1",
        "Accept": "application/json"
    })

    with urlopen(request, timeout=30) as response:
        raw = response.read(5_000_001)
        if len(raw) > 5_000_000:
            raise ValueError("API response exceeds 5 MB")
        payload = json.loads(raw.decode("utf-8-sig"))

    
    items = payload.get("items", [])
    earnings_count = sum(
        "決算短信" in str(
            item.get("Tdnet", {}).get("title") or ""
        )
        for item in items
    )
    print(
        f"{code}: API items={len(items)}, "
        f"earnings titles={earnings_count}",
        flush=True )
    candidates = []

    for item in payload.get("items", []):
        disclosure = item.get("Tdnet", {})
        title = str(disclosure.get("title") or "")
        company_code = str(
            disclosure.get("company_code") or ""
        ).strip()

        if company_code not in (code, code + "0"):
            continue
        if "決算短信" not in title:
            continue
        if re.search(
            r"訂正|修正|補足|説明資料|サマリー",
            title
        ):
            continue

        dt = parse_datetime(disclosure.get("pubdate"))
        if dt is None or dt.date() > TODAY:
            continue

        # Reject old releases for automatic adoption.
        if (TODAY - dt.date()).days > 125:
            continue

        source_url = str(
            disclosure.get("document_url") or ""
        ).strip()
        if not source_url.startswith("https://"):
            continue

        candidates.append({
            "code": code,
            "disclosure_date": dt.date().isoformat(),
            "disclosure_time": dt.strftime("%H:%M"),
            "title": title,
            "source_url": source_url,
            "status": "candidate_unverified"
        })

    if not candidates:
        return {
            "code": code,
            "disclosure_date": "",
            "disclosure_time": "",
            "title": "",
            "source_url": "",
            "status": "no_recent_earnings"
        }

    return max(
        candidates,
        key=lambda x: (
            x["disclosure_date"],
            x["disclosure_time"]
        )
    )


def main():
    codes = get_codes()
    results = []

    for i, code in enumerate(codes, 1):
        try:
            row = fetch_one(code)
        except Exception as exc:
            print(
                f"{code}: {type(exc).__name__}: {exc}",
                flush=True
            )
            row = {
                "code": code,
                "disclosure_date": "",
                "disclosure_time": "",
                "title": "",
                "source_url": "",
                "status": "fetch_error"
            }

        results.append(row)
        print(
            f"[{i}/{len(codes)}] "
            f"{code}: {row['status']}",
            flush=True
        )
        time.sleep(float(os.environ.get("TDNET_SLEEP", "1")))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT.open(
        "w", encoding="utf-8-sig", newline=""
    ) as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(results)

    print("Saved:", OUTPUT)
    print(
        "Candidates:",
        sum(
            r["status"] == "candidate_unverified"
            for r in results
        )
    )


if __name__ == "__main__":
    main()
