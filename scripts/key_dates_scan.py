"""關鍵日期整合 + 財報窗口歷史股價統計 —— 獨立模組。

兩件事：
1. 把三個已經在算的時間資訊(法定財報截止日、公司重大公告/法說會、美國FOMC會議)加上新抓的
   除權息日，合併成一份按時間排序的「關鍵日期」清單，不用分開看好幾個分頁。
   （嘗試找過股東會日期的官方資料源，但目前查到的資料集內容是2020年的舊資料，不能用就不用，
   不會為了湊功能硬塞過期資訊進去。）
2. 用股票過去實際發生過的股價資料，算出「過去在法定財報截止日窗口前後，股價通常怎麼動」的
   統計數字——這是根據歷史事實算出來的統計傾向，不是預測未來會怎樣，報告裡會清楚標示樣本數
   (算出這個統計用了幾次過去的窗口)，樣本數太少(<3次)的不會強行給出統計數字。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import statistics
import sys
from pathlib import Path

import yfinance as yf

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).parent
DOCS_DIR = BASE_DIR.parent / "docs"
CACHE_DIR = BASE_DIR.parent / "data" / "cache"
sys.path.insert(0, str(BASE_DIR))
from theme_rotation_scan import THEMES, _get_json_with_retry  # noqa: E402
from event_calendar_scan import _load_event_log, FOMC_2026_MEETINGS  # noqa: E402
from earnings_calendar_scan import STATUTORY_DEADLINES  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("key_dates")

EX_DIVIDEND_URL = "https://www.twse.com.tw/rwd/zh/exRight/TWT48U?response=json"
PRICE_HISTORY_YEARS = 3  # 抓3年股價來算過去幾次財報窗口的歷史統計,跟其他模組用的4個月快取分開存
PRICE_HISTORY_CACHE_PATH = CACHE_DIR / "long_price_history_cache.json"
MIN_SAMPLE_SIZE = 3  # 少於3次歷史窗口不給統計數字,樣本太少沒有意義
WINDOW_DAYS = 5  # 截止日前後各抓幾個交易日算窗口報酬率/波動度

OUTPUT_PATH = DOCS_DIR / "key_dates_latest.json"


def _roc_full_to_iso(roc_str: str) -> str | None:
    """'115年09月07日' -> '2026-09-07'。"""
    try:
        year_part, rest = roc_str.split("年", 1)
        month_part, rest = rest.split("月", 1)
        day_part = rest.split("日", 1)[0]
        year = int(year_part) + 1911
        return f"{year:04d}-{int(month_part):02d}-{int(day_part):02d}"
    except (ValueError, IndexError):
        return None


def fetch_ex_dividend_dates(codes: set[str]) -> list[dict]:
    """抓近期除權息預告表,篩出我們追蹤的股票。這份官方預告表本身只涵蓋近期(通常幾週內)已經
    確定日期的個股,不是全年度的完整清單——公司通常在除權息日前不久才會被排進這張表。
    """
    try:
        payload = _get_json_with_retry(EX_DIVIDEND_URL)
    except Exception:  # noqa: BLE001
        logger.warning("除權息預告表抓取失敗", exc_info=True)
        return []

    if payload.get("stat") != "OK":
        return []

    fields = payload["fields"]
    date_idx = fields.index("除權除息日期")
    code_idx = fields.index("股票代號")
    name_idx = fields.index("名稱")
    type_idx = fields.index("除權息")
    cash_dividend_idx = fields.index("現金股利")

    results = []
    for row in payload.get("data", []):
        code = row[code_idx].strip()
        if code not in codes:
            continue
        iso_date = _roc_full_to_iso(row[date_idx])
        if not iso_date:
            continue
        results.append({
            "code": code,
            "name": row[name_idx].strip(),
            "date": iso_date,
            "type": row[type_idx].strip(),  # 權(除權)/息(除息)/權息(兩者皆有)
            "cash_dividend": row[cash_dividend_idx],
        })
    return results


def build_unified_calendar(codes: set[str], name_by_code: dict[str, str]) -> list[dict]:
    """合併法定財報截止日、公司重大公告/法說會、FOMC會議、除權息日,按日期排序回傳。"""
    today = dt.date.today()
    entries = []

    for month, day, label, _period_desc in STATUTORY_DEADLINES:
        for year in (today.year, today.year + 1):
            deadline = dt.date(year, month, day)
            if deadline >= today:
                entries.append({"date": deadline.isoformat(), "type": "財報截止日", "title": label, "code": None})

    for date_str in FOMC_2026_MEETINGS:
        if dt.date.fromisoformat(date_str) >= today:
            entries.append({"date": date_str, "type": "總經事件", "title": "FOMC會議", "code": None})

    log = _load_event_log()
    for e in log:
        if e["code"] in codes and e["announce_date"] >= today.isoformat():
            entries.append({
                "date": e["announce_date"], "type": "公司公告",
                "title": f"{e['name']}：{e['subject'][:30]}", "code": e["code"],
            })

    for ex in fetch_ex_dividend_dates(codes):
        entries.append({
            "date": ex["date"], "type": "除權息",
            "title": f"{ex['name']}（{ex['type']}，現金股利{ex['cash_dividend']}）", "code": ex["code"],
        })

    entries.sort(key=lambda e: e["date"])
    return entries


def _load_long_price_cache() -> dict:
    if not PRICE_HISTORY_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(PRICE_HISTORY_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_long_price_cache(cache: dict) -> None:
    PRICE_HISTORY_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_long_price_history(code: str, market: str, cache: dict) -> list[dict]:
    """抓3年股價(比其他模組用的4個月長,只為了算歷史窗口統計,獨立快取,每天抓一次就好。"""
    today_str = dt.date.today().isoformat()
    cached = cache.get(code)
    if cached and cached.get("as_of") == today_str:
        return cached["rows"]

    symbol = f"{code}.TW" if market == "TSE" else f"{code}.TWO"
    try:
        hist = yf.Ticker(symbol).history(period=f"{PRICE_HISTORY_YEARS}y", auto_adjust=False)
        rows = [
            {"date": idx.strftime("%Y-%m-%d"), "close": round(float(row["Close"]), 2)}
            for idx, row in hist.iterrows()
        ]
    except Exception:  # noqa: BLE001
        logger.warning("長期股價歷史抓取失敗(%s)", symbol, exc_info=True)
        rows = cached["rows"] if cached else []

    if rows:
        cache[code] = {"as_of": today_str, "rows": rows}
    return rows


def compute_earnings_window_stats(history: list[dict]) -> dict | None:
    """算過去每一次法定截止日窗口(前後WINDOW_DAYS個交易日)的報酬率跟每日波動度,再取平均。
    樣本數(算到幾次窗口)小於MIN_SAMPLE_SIZE時回傳None,不給統計意義不足的數字。
    """
    if len(history) < 30:
        return None

    dates = [r["date"] for r in history]
    closes = [r["close"] for r in history]
    date_to_idx = {d: i for i, d in enumerate(dates)}

    earliest = dt.date.fromisoformat(dates[0])
    latest = dt.date.fromisoformat(dates[-1])

    window_returns = []
    window_volatilities = []
    for year in range(earliest.year, latest.year + 1):
        for month, day, _label, _period in STATUTORY_DEADLINES:
            try:
                deadline = dt.date(year, month, day)
            except ValueError:
                continue
            if not (earliest < deadline < latest):
                continue
            deadline_str = deadline.isoformat()
            center_idx = None
            for offset in range(0, 10):
                candidate = (deadline + dt.timedelta(days=offset)).isoformat()
                if candidate in date_to_idx:
                    center_idx = date_to_idx[candidate]
                    break
            if center_idx is None:
                continue

            start_idx = center_idx - WINDOW_DAYS
            end_idx = center_idx + WINDOW_DAYS
            if start_idx < 0 or end_idx >= len(closes):
                continue

            window_return = (closes[end_idx] / closes[start_idx] - 1) * 100
            window_returns.append(window_return)

            daily_returns = [
                (closes[i] / closes[i - 1] - 1) * 100
                for i in range(start_idx + 1, end_idx + 1)
                if closes[i - 1]
            ]
            if len(daily_returns) >= 2:
                window_volatilities.append(statistics.stdev(daily_returns))

    if len(window_returns) < MIN_SAMPLE_SIZE:
        return None

    return {
        "sample_size": len(window_returns),
        "avg_window_return_pct": round(statistics.mean(window_returns), 2),
        "window_return_stdev_pct": round(statistics.stdev(window_returns), 2) if len(window_returns) >= 2 else None,
        "avg_daily_volatility_pct": round(statistics.mean(window_volatilities), 2) if window_volatilities else None,
        "window_days_each_side": WINDOW_DAYS,
    }


def build_report() -> dict:
    all_codes = [(theme, s) for theme, stocks in THEMES.items() for s in stocks]
    codes = {s["code"] for _, s in all_codes}
    name_by_code = {s["code"]: s["name"] for _, s in all_codes}
    theme_by_code = {s["code"]: theme for theme, s in all_codes}
    market_by_code = {s["code"]: s["market"] for _, s in all_codes}

    logger.info("合併關鍵日期(財報截止日/公司公告/FOMC/除權息)...")
    calendar = build_unified_calendar(codes, name_by_code)

    logger.info("計算歷史財報窗口股價統計(抓%d年股價,只在有需要時重抓)...", PRICE_HISTORY_YEARS)
    long_cache = _load_long_price_cache()
    stats_by_code = {}
    for code in codes:
        history = fetch_long_price_history(code, market_by_code[code], long_cache)
        stats = compute_earnings_window_stats(history)
        stats_by_code[code] = {
            "code": code, "name": name_by_code[code], "theme": theme_by_code[code],
            "stats": stats,
        }
    _save_long_price_cache(long_cache)

    return {
        "generated_at": dt.datetime.now().isoformat(),
        "calendar": calendar,
        "earnings_window_stats": list(stats_by_code.values()),
    }


def print_report(report: dict) -> None:
    print(f"\n=== 關鍵日期 + 財報窗口歷史股價統計 {report['generated_at']} ===\n")
    print("近期關鍵日期(由近到遠)：")
    for e in report["calendar"][:25]:
        code_str = f"（{e['code']}）" if e["code"] else ""
        print(f"  {e['date']}  [{e['type']}]{code_str} {e['title']}")

    print("\n財報窗口歷史股價統計(樣本數<3不顯示,不是漏抓)：")
    for s in report["earnings_window_stats"]:
        if s["stats"] is None:
            continue
        st = s["stats"]
        print(
            f"  {s['name']}({s['code']}) 過去{st['sample_size']}次財報截止日窗口(前後{st['window_days_each_side']}個交易日)："
            f"平均報酬{st['avg_window_return_pct']:+.2f}%　平均單日波動度{st['avg_daily_volatility_pct']}%"
        )


def main() -> None:
    report = build_report()
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_report(report)
    print(f"\n完整結果已存至 {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
