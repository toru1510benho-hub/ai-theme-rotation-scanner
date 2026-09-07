"""M3(盤中即時報價輪動) + M7(美股連動) —— 獨立模組。

跟theme_rotation_scan.py(M1資金流向+M2位階/本益比)完全獨立運作、互不依賴：
- 只用yfinance(Yahoo Finance),完全不碰TWSE/TPEx官方API,不會撞到STOCK_DAY/T86那批限流。
- 這支腳本失敗不影響M1/M2,反之亦然。
- 沿用theme_rotation_scan.py裡的THEMES清單(單一個股清單來源,不重複維護兩份)。

核心用途:
1. 盤中看「現在」誰漲誰跌,抓出族群裡「同業已經動、這檔還沒動」的盤中落後股。
2. 美股連動(輝達/費半/那斯達克...)——這些AI受惠股很大程度跟著美股隔夜表現走,
   開盤前/盤中先看美股風向,幫助判斷今天台股的漲跌是不是「補漲」還是「補跌」。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import sys
from pathlib import Path

import yfinance as yf

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).parent
DOCS_DIR = BASE_DIR.parent / "docs"
sys.path.insert(0, str(BASE_DIR))
from theme_rotation_scan import THEMES  # noqa: E402  單一清單來源,不重複維護

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("intraday_quotes")

OUTPUT_PATH = DOCS_DIR / "intraday_quotes_latest.json"

# 美股連動觀察標的:輝達/超微(AI晶片)、台積電ADR、費城半導體指數、大盤指數
US_BENCHMARKS = {
    "NVDA": "輝達",
    "AMD": "超微",
    "TSM": "台積電ADR",
    "^SOX": "費城半導體指數",
    "^GSPC": "S&P500",
    "^IXIC": "那斯達克綜合",
}


def _yahoo_symbol(code: str, market: str) -> str:
    return f"{code}.TW" if market == "TSE" else f"{code}.TWO"


def _fetch_one_quote(symbol: str) -> dict:
    """單檔抓取,個別失敗不拋錯、不影響其他檔——盤中輪動這種東西,少一檔資料總比整批掛掉好。"""
    try:
        info = yf.Ticker(symbol).fast_info
        price = info.get("lastPrice")
        prev_close = info.get("previousClose")
        change_pct = round((price / prev_close - 1) * 100, 2) if (price and prev_close) else None
        return {"price": price, "prev_close": prev_close, "change_pct": change_pct}
    except Exception as exc:  # noqa: BLE001
        logger.warning("報價抓取失敗(%s): %s", symbol, exc)
        return {"price": None, "prev_close": None, "change_pct": None}


def fetch_intraday_quotes() -> dict[str, dict]:
    all_stocks = [(theme, s) for theme, stocks in THEMES.items() for s in stocks]
    result = {}
    for theme, s in all_stocks:
        code, name, market = s["code"], s["name"], s["market"]
        symbol = _yahoo_symbol(code, market)
        quote = _fetch_one_quote(symbol)
        result[code] = {"code": code, "name": name, "theme": theme, "market": market, "symbol": symbol, **quote}
    return result


def fetch_us_benchmarks() -> dict[str, dict]:
    result = {}
    for symbol, name in US_BENCHMARKS.items():
        quote = _fetch_one_quote(symbol)
        result[symbol] = {"name": name, **quote}
    return result


def build_report() -> dict:
    logger.info("抓取台股即時報價...")
    quotes = fetch_intraday_quotes()
    logger.info("抓取美股連動指標...")
    us = fetch_us_benchmarks()

    theme_summary = {}
    for theme, stocks in THEMES.items():
        theme_quotes = [quotes[s["code"]] for s in stocks if s["code"] in quotes]
        changes = [q["change_pct"] for q in theme_quotes if q.get("change_pct") is not None]
        avg_change = sum(changes) / len(changes) if changes else None

        candidates = []
        for q in theme_quotes:
            tags = []
            if (
                q.get("change_pct") is not None
                and avg_change is not None
                and q["change_pct"] < avg_change - 0.5  # 明顯落後族群平均,不是雜訊等級的小差距
            ):
                tags.append("盤中落後族群")
            if (
                q.get("change_pct") is not None
                and avg_change is not None
                and avg_change > 0
                and q["change_pct"] > avg_change * 1.5
            ):
                tags.append("盤中領漲,慎防追高")
            candidates.append({**q, "tags": tags})

        candidates.sort(key=lambda q: q.get("change_pct") if q.get("change_pct") is not None else 999)
        theme_summary[theme] = {"avg_change_pct": avg_change, "stocks": candidates}

    theme_ranking = sorted(
        theme_summary.items(),
        key=lambda kv: (kv[1]["avg_change_pct"] if kv[1]["avg_change_pct"] is not None else float("-inf")),
        reverse=True,
    )

    return {
        "generated_at": dt.datetime.now().isoformat(),
        "us_benchmarks": us,
        "theme_ranking_by_change": [name for name, _ in theme_ranking],
        "themes": theme_summary,
    }


def print_report(report: dict) -> None:
    print(f"\n=== 盤中即時報價輪動 + 美股連動 {report['generated_at']} ===\n")

    print("美股連動(隔夜/即時):")
    for symbol, q in report["us_benchmarks"].items():
        chg = f"{q['change_pct']:+.2f}%" if q.get("change_pct") is not None else "N/A"
        price = q.get("price")
        print(f"  {q['name']}({symbol})  {price}  {chg}")

    print("\n族群盤中漲跌幅排名(由高到低):")
    for i, name in enumerate(report["theme_ranking_by_change"], 1):
        avg = report["themes"][name]["avg_change_pct"]
        avg_str = f"{avg:+.2f}%" if avg is not None else "N/A"
        print(f"  {i}. {name}  族群平均:{avg_str}")

    for theme, data in report["themes"].items():
        avg = data["avg_change_pct"]
        avg_str = f"{avg:+.2f}%" if avg is not None else "N/A"
        print(f"\n--- {theme} (族群平均漲跌 {avg_str}) ---")
        for s in data["stocks"]:
            chg = f"{s['change_pct']:+.2f}%" if s.get("change_pct") is not None else "N/A"
            tag_str = " | ".join(s["tags"]) if s["tags"] else "-"
            print(f"    {s['name']}({s['code']}) {s.get('price')} {chg} [{tag_str}]")


def main() -> None:
    report = build_report()
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_report(report)
    print(f"\n完整結果已存至 {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
