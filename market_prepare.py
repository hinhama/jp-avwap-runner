
"""Fetch JPX listed domestic equities and create deterministic shard CSVs."""
import argparse
import csv
import io
import json
import re
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

PAGE = "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"
KNOWN = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"


def download_listing():
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (compatible; JPX-market-research/1.0)"
    candidates = []

    try:
        resp = session.get(PAGE, timeout=25)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for a in soup.find_all("a", href=True):
            href = urljoin(PAGE, a["href"])
            if href.endswith((".xls", ".xlsx")) and (
                "data_j" in href or "上場銘柄" in a.get_text()
            ):
                candidates.append(href)

    except requests.RequestException as exc:
        print(f"JPX listing page unavailable: {exc}", flush=True)

    candidates.append(KNOWN)
    errors = []

    for url in dict.fromkeys(candidates):
        try:
            r = session.get(url, timeout=45)
            r.raise_for_status()

            if len(r.content) < 50000:
                raise ValueError("unexpectedly small listing")

            df = pd.read_excel(io.BytesIO(r.content), dtype=str)

            if "コード" not in df.columns or "銘柄名" not in df.columns:
                raise ValueError(f"unexpected columns: {list(df.columns)}")

            print(f"JPX listing source: {url}; rows={len(df)}", flush=True)
            return df, url

        except Exception as exc:
            errors.append(f"{url}: {exc}")

    raise RuntimeError(
        "Could not obtain valid JPX listing; "
        "refusing to use stale/unverified symbols. "
        + "; ".join(errors)
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--shards", type=int, default=8)
    args = p.parse_args()

    if args.shards < 1 or args.shards > 20 or args.limit < 0:
        p.error("shards must be 1..20 and limit must be >=0")

    df, url = download_listing()

    market = df.get(
        "市場・商品区分",
        pd.Series("", index=df.index)
    ).fillna("")

    domestic = market.str.contains("内国株式", regex=False)
    df = df[domestic].copy()

    symbols = []
    seen = set()

    for _, row in df.iterrows():
        code = str(row["コード"]).strip().upper()

        if not re.fullmatch(r"[0-9A-Z]{4}", code) or code in seen:
            continue

        seen.add(code)
        symbols.append({
            "code": code,
            "name": str(row["銘柄名"]).strip()
        })

    if len(symbols) < 3000:
        raise RuntimeError(
            f"Only {len(symbols)} domestic equities found; "
            "JPX format/filter may have changed"
        )

    symbols.sort(key=lambda x: x["code"])

    if args.limit:
        symbols = symbols[:args.limit]

    dest = Path("market_shards")
    dest.mkdir(exist_ok=True)

    for i in range(args.shards):
        chunk = symbols[i::args.shards]

        with (
            dest / f"tickers_{i:02}.csv"
        ).open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["code", "name"]
            )
            writer.writeheader()
            writer.writerows(chunk)

    (dest / "source.json").write_text(
        json.dumps({
            "source": url,
            "total": len(symbols),
            "shards": args.shards,
            "market": "JPX domestic equities"
        }, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(
        f"Prepared {len(symbols)} equities "
        f"across {args.shards} shards",
        flush=True
    )


if __name__ == "__main__":
    main()
