"""Japanese equities Y/E/S anchored VWAP approximations from adjusted daily OHLCV."""
import csv
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

ROOT = Path(file).resolve().parent
OUT = ROOT / os.environ.get('AVWAP_OUTPUT_DIR', 'output_market')
OUT.mkdir(parents=True, exist_ok=True)
TODAY = datetime.now(ZoneInfo('Asia/Tokyo')).date()
START = (TODAY - timedelta(days=550)).isoformat()

def load_rows(path):
# Accept blank/preamble lines before the CSV header (GitHub editor may add one).
with (ROOT / path).open(encoding='utf-8-sig', newline='') as f:
lines = list(csv.reader(f))
for pos, row in enumerate(lines):
normalized = [cell.strip().lstrip('\ufeff') for cell in row]
if 'code' in normalized:
keys = normalized
rows = []
for values in lines[pos + 1:]:
if not any(cell.strip() for cell in values):
continue
item = dict(zip(keys, values))
if not item.get('code', '').strip():
continue
rows.append(item)
return rows
raise ValueError(f'{path}: CSV header with code column not found')

def avwap(df, anchor):
section = df.loc[df.index >= pd.Timestamp(anchor)]
section = section.loc[section['Volume'] > 0]
if section.empty or section['Volume'].sum() <= 0:
return None
typical = (section['High'] + section['Low'] + section['Close']) / 3
return float((typical * section['Volume']).sum() / section['Volume'].sum())

def last_earnings_anchor(code, dates, index):
known = sorted([pd.Timestamp(x) for x in dates.get(code, []) if x and pd.Timestamp(x) <= index[-1]])
if not known:
return None
announcement = known[-1]
# Conservative convention: next trading session, even if announcement was intraday.
following = index[index > announcement]
return following[0] if len(following) else None

def auto_earnings_date(code, last_price_date):
"""Only accept historical earnings entries with a reported EPS value.

Yahoo may omit Japanese company events; in that case do not guess.  
"""  
try:  
    events = yf.Ticker(f'{code}.T').get_earnings_dates(limit=24)  
    if events is None or events.empty:  
        return None, 'yahoo_missing'  
    actual_cols = [c for c in events.columns if str(c).lower().replace(' ', '') in  
                   ('reportedeps', 'epsactual')]  
    if not actual_cols:  
        return None, 'yahoo_unverified'  
    actual_col = actual_cols[0]  
    candidates = []  
    for timestamp, row in events.iterrows():  
        if pd.isna(row[actual_col]):  
            continue  
        event_time = pd.Timestamp(timestamp)  
        if event_time.tzinfo is not None:  
            event_time = event_time.tz_convert('Asia/Tokyo').tz_localize(None)  
        event_day = event_time.normalize()  
        if event_day <= last_price_date:  
            candidates.append(event_day)  
    if not candidates:  
        return None, 'yahoo_unverified'  
    return max(candidates), 'yahoo_reported_eps'  
except Exception as exc:  
    print(f'  {code} earnings lookup unavailable: {type(exc).__name__}: {exc}', flush=True)  
    return None, 'yahoo_error'

def swing_anchor(df):
# Deterministic proxy: most recent lowest Low in trailing 120 sessions,
# excluding the latest session. Not a verified discretionary swing low.
lookback = df.iloc[-121:-1]
if lookback.empty:
return None
return lookback['Low'].idxmin()

def load_tdnet_verified():
"""Official TDnet disclosure dates transcribed/verified by user; no scraping."""
path = ROOT / 'tdnet_disclosures.csv'
if not path.exists():
return {}
records = {}
for row in load_rows('tdnet_disclosures.csv'):
code = (row.get('code') or '').strip()
date = (row.get('disclosure_date') or '').strip()
time_str = (row.get('disclosure_time') or '').strip()
title = (row.get('title') or '').strip()
url = (row.get('source_url') or '').strip()
if not code or not date:
continue
try:
day = pd.Timestamp(date).normalize()
if not (len(code) == 4 and code.isalnum()) or day.date() > TODAY:
continue
# Only actual earnings releases; exclude corrections and forecasts.
if '決算短信' not in title or any(x in title for x in ('訂正', '業績予想', '決算説明')):
continue
if time_str and (len(time_str) != 5 or time_str[2] != ':'):
continue
if not url.startswith('https://'):
continue
records.setdefault(code, []).append((day, time_str, url))
except (ValueError, TypeError):
continue
return records

def load_tdnet_auto():
"""Load unverified TDnet API candidates for testing."""
path = ROOT / os.environ.get('TDNET_CANDIDATES_FILE', 'output/tdnet_auto_candidates.csv')
if not path.exists():
print('TDnet auto CSV missing; using existing sources')
return {}

records = {}  
for row in load_rows(os.environ.get('TDNET_CANDIDATES_FILE', 'output/tdnet_auto_candidates.csv')):  
    code = (row.get('code') or '').strip()  
    date = (row.get('disclosure_date') or '').strip()  
    tm = (row.get('disclosure_time') or '').strip()  
    title = (row.get('title') or '').strip()  
    url = (row.get('source_url') or '').strip()  

    if row.get('status') != 'candidate_unverified':  
        continue  
    if not (len(code) == 4 and code.isalnum()) or not date:  
        continue  
    if '決算短信' not in title:  
        continue  
    if any(x in title for x in  
           ('訂正', '修正', '補足', '説明資料', 'サマリー')):  
        continue  
    if not url.startswith('https://'):  
        continue  

    try:  
        day = pd.Timestamp(date).normalize()  
        if day.date() > TODAY:  
            continue  
        if (TODAY - day.date()).days > 125:  
            continue  
        if len(tm) != 5 or tm[2] != ':':  
            continue  
        datetime.strptime(tm, '%H:%M')  
        records.setdefault(code, []).append(  
            (day, tm, url)  
        )  
    except (ValueError, TypeError):  
        continue  

return records

def official_anchor(code, tdnet, index):
if code not in tdnet:
return None
last = index[-1]
candidates = [(day, tm, url) for day, tm, url in tdnet[code]
if day <= last and (last - day).days <= 125]
if not candidates:
return None
day, tm, url = max(candidates, key=lambda x: (x[0], x[1]))
# Always use the NEXT session, so intraday disclosure cannot mix pre/post prices.
following = index[index > day]
if not len(following):
return None
return day, following[0], url

def latest_tdnet_disclosure(code, tdnet, tdnet_auto, last):
"""Find latest TDnet disclosure, including one awaiting price data."""
records = []

for source, dataset in (  
    ('tdnet_verified_manual', tdnet),  
    ('tdnet_api_candidate_unverified', tdnet_auto),  
):  
    for item in dataset.get(code, []):  
        day, tm, url = item  
        day = pd.Timestamp(day).normalize()  
        if day <= last:  
            records.append((day, tm, url, source))  

if not records:  
    return None  

# On the same date, manually verified records take priority.  
  
return max(  
    records,  
    key=lambda x: (  
        x[0],  
        x[3] == 'tdnet_verified_manual',  
        x[1]  
    )  
)

def main():
tickers = load_rows(os.environ.get('AVWAP_TICKERS_FILE', 'tickers_50.csv'))
earnings = {}
for row in load_rows('earnings.csv'):
if row.get('code') and row.get('announcement_date'):
earnings.setdefault(row['code'].strip(), []).append(row['announcement_date'].strip())
tdnet = load_tdnet_verified()
tdnet_auto = load_tdnet_auto()
results = []
for i, item in enumerate(tickers, 1):
code = item['code'].strip()
name = item.get('name', '').strip()
result = {'code': code, 'name': name, 'asof_jst': TODAY.isoformat(), 'status': 'error', 'error': ''}
try:
if not (len(code) == 4 and code.isalnum()):
raise ValueError('code must be four alphanumeric characters')
df = yf.download(f'{code}.T', start=START, end=(TODAY + timedelta(days=1)).isoformat(),
auto_adjust=True, actions=False, progress=False, threads=False, timeout=25)
if df.empty:
raise ValueError('no price data')
if isinstance(df.columns, pd.MultiIndex):
df.columns = df.columns.get_level_values(0)
df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
df = df[['High', 'Low', 'Close', 'Volume']].dropna().sort_index()
df = df[df['Volume'] > 0]
if len(df) < 30:
raise ValueError('fewer than 30 trading sessions')
last = df.index[-1]
price = float(df['Close'].iloc[-1])
year_anchor = df.index[df.index.year == last.year][0]
manual_dates = earnings.get(code, [])
manual_ea = last_earnings_anchor(code, earnings, df.index)
auto_day, auto_source = (None, 'tdnet_preferred') if (tdnet.get(code) or tdnet_auto.get(code)) else auto_earnings_date(code, last)
auto_ea = None
if auto_day is not None:
later = df.index[df.index > auto_day]
if len(later):
auto_ea = later[0]
if manual_ea is not None and (auto_ea is None or manual_ea >= auto_ea):
ea, e_source = manual_ea, 'earnings_csv_manual'
e_announcement = max(pd.Timestamp(x) for x in manual_dates if x and pd.Timestamp(x) < manual_ea)
elif auto_ea is not None:
ea, e_source, e_announcement = auto_ea, auto_source, auto_day
else:
ea, e_source, e_announcement = None, auto_source, None

e_url = ''  
        latest = latest_tdnet_disclosure(  
            code, tdnet, tdnet_auto, last  
        )  

        if latest is not None:  
            disclosure_day, disclosure_time, e_url, e_source = latest  
            e_announcement = disclosure_day  

            # Only sessions strictly after the disclosure day.  
            later = df.index[df.index > disclosure_day]  

            if len(later):  
                ea = later[0]  
            else:  
                ea = None  
                e_source += '_awaiting_price'  

        elif e_announcement is not None:  
            if (last - e_announcement).days > 125:  
                ea = None  
                e_announcement = None  
                e_source = 'stale_unverified'  

        sa = swing_anchor(df)  
        result.update({'status': 'ok', 'price_date': last.date().isoformat(),  
                       'close_adjusted': round(price, 4), 'y_anchor': year_anchor.date().isoformat(),  
                       'e_anchor': ea.date().isoformat() if ea is not None else '',  
                       'e_announcement_date': e_announcement.date().isoformat() if e_announcement is not None else '',  
                       'e_source': e_source, 'e_source_url': e_url,  
                       's_anchor': sa.date().isoformat() if sa is not None else '',  
                       's_method': 'trailing_120_sessions_min_low_excl_latest',  
                       'data_basis': 'auto_adjusted_daily_typical_price_volume'})  
        for key, anchor in [('y', year_anchor), ('e', ea), ('s', sa)]:  
            value = avwap(df, anchor) if anchor is not None else None  
            result[f'{key}_avwap'] = round(value, 4) if value is not None else ''  
            result[f'{key}_gap_pct'] = round((price / value - 1) * 100, 2) if value else ''  
        gaps = [result[f'{k}_gap_pct'] for k in ('y', 'e', 's')]  
        result['above_all_three'] = 'YES' if all(isinstance(g, (float, int)) and g > 0 for g in gaps) else ('UNKNOWN' if any(g == '' for g in gaps) else 'NO')  
        result['stale_sessions'] = (TODAY - last.date()).days  
    except Exception as exc:  
        result['error'] = str(exc)[:240]  
    results.append(result)  
    print(f'[{i}/{len(tickers)}] {code}: {result["status"]}', flush=True)  
    time.sleep(float(os.environ.get('AVWAP_SLEEP', '1.5')))  
columns = ['code','name','asof_jst','status','error','price_date','stale_sessions','close_adjusted',  
           'y_anchor','y_avwap','y_gap_pct','e_announcement_date','e_source','e_source_url','e_anchor','e_avwap','e_gap_pct',  
           's_anchor','s_avwap','s_gap_pct','above_all_three','s_method','data_basis']  
with (OUT / 'avwap_latest.csv').open('w', encoding='utf-8-sig', newline='') as f:  
    writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')  
    writer.writeheader()  
    writer.writerows(results)  
(OUT / 'run_summary.json').write_text(json.dumps({  
    'run_date_jst': TODAY.isoformat(), 'total': len(results),  
    'ok': sum(x['status'] == 'ok' for x in results),  
    'errors': sum(x['status'] != 'ok' for x in results),  
    'notes': 'TDnet manually verified disclosures take priority. Yahoo is fallback and stale dates (>125 days) are rejected. S is an objective proxy.'  
}, ensure_ascii=False, indent=2), encoding='utf-8')  
if not any(x['status'] == 'ok' for x in results):  
    raise SystemExit('All symbols failed; see output CSV')

if name == 'main':
main()
