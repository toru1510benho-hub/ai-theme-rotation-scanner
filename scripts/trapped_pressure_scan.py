"""M5(套牢賣壓代理指標) —— 獨立模組,純程式邏輯,不用AI。

真正的「持股成本分佈」沒有公開資料(需要券商全市場交易明細才算得出來),這裡做的是一個
可計算的代理指標:找出過去60個交易日內「量能異常大、且發生在相對高檔」的日子,
用那些日子的成交量加權平均價當作「套牢區」——這是很多人在相對高點大量進場的價位帶,
股價回檔後這個價位帶容易變成反彈時的賣壓(解套賣壓)。

規則(完全用程式算,不猜測):
1. 近60個交易日,篩出量能排前30%、且收盤價落在該期間價格區間前40%高檔的日子。
2. 那些日子的成交量加權平均收盤價 = 套牢區參考價。
3. 現價距離套牢區的百分比,越近代表越可能碰到解套賣壓;現價已經站上套牢區則代表賣壓已消化。

沿用theme_rotation_scan.py的THEMES清單與股價歷史快取(同一份yfinance資料,不重複抓取)。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).parent
DOCS_DIR = BASE_DIR.parent / "docs"
sys.path.insert(0, str(BASE_DIR))
from theme_rotation_scan import (  # noqa: E402  單一清單/快取來源,不重複維護
    THEMES,
    _load_stock_history_cache,
    _save_stock_history_cache,
    fetch_stock_history_cached,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("trapped_pressure")

OUTPUT_PATH = DOCS_DIR / "trapped_pressure_latest.json"

LOOKBACK_DAYS = 60
VOLUME_TOP_PCT = 0.3  # 量能前30%
PRICE_HIGH_PCT = 0.4  # 收盤價落在區間前40%高檔
NEAR_ZONE_THRESHOLD_PCT = 5  # 現價在套牢區下方5%以內,視為「接近套牢區」


def compute_trapped_zone(history: list[dict]) -> dict | None:
    """history是{date, close, volume}由舊到新排序的清單。至少要有20筆才計算,不足60筆就用現有的。"""
    if len(history) < 20:
        return None

    window = history[-LOOKBACK_DAYS:]
    closes = [r["close"] for r in window]
    volumes = [r["volume"] for r in window]
    latest_close = closes[-1]

    price_min, price_max = min(closes), max(closes)
    price_range = price_max - price_min
    price_high_cutoff = price_max - price_range * PRICE_HIGH_PCT if price_range > 0 else price_max

    sorted_volumes = sorted(volumes, reverse=True)
    volume_cutoff_idx = max(0, int(len(sorted_volumes) * VOLUME_TOP_PCT) - 1)
    volume_cutoff = sorted_volumes[volume_cutoff_idx]

    qualifying = [
        (r["close"], r["volume"])
        for r in window
        if r["volume"] >= volume_cutoff and r["close"] >= price_high_cutoff
    ]

    if not qualifying:
        return {
            "trapped_zone_price": None,
            "latest_close": round(latest_close, 2),
            "distance_pct": None,
            "note": "近期無明顯高檔爆量日,無套牢區參考價",
        }

    total_volume = sum(v for _, v in qualifying)
    trapped_zone_price = sum(c * v for c, v in qualifying) / total_volume
    distance_pct = (trapped_zone_price / latest_close - 1) * 100

    return {
        "trapped_zone_price": round(trapped_zone_price, 2),
        "latest_close": round(latest_close, 2),
        "distance_pct": round(distance_pct, 2),
        "qualifying_days": len(qualifying),
        "note": None,
    }


def _tag_for_zone(zone: dict) -> list[str]:
    if zone.get("trapped_zone_price") is None:
        return []
    distance = zone["distance_pct"]
    if distance <= 0:
        return ["已站上套牢區,賣壓大致消化"]
    if distance <= NEAR_ZONE_THRESHOLD_PCT:
        return [f"接近套牢區(距離{distance:+.1f}%),反彈到此處慎防解套賣壓"]
    return [f"距離套牢區還有{distance:+.1f}%,暫不是主要壓力"]


def build_report() -> dict:
    stock_history_cache = _load_stock_history_cache()

    theme_summary = {}
    for theme, stocks in THEMES.items():
        entries = []
        for s in stocks:
            code, name, market = s["code"], s["name"], s["market"]
            logger.info("計算套牢賣壓代理指標 %s(%s)...", name, code)
            history = fetch_stock_history_cached(code, market, stock_history_cache)
            zone = compute_trapped_zone(history)
            if zone is None:
                entries.append({"code": code, "name": name, "market": market, "zone": None, "tags": []})
                continue
            entries.append({"code": code, "name": name, "market": market, "zone": zone, "tags": _tag_for_zone(zone)})

        theme_summary[theme] = entries

    _save_stock_history_cache(stock_history_cache)
    return {"generated_at": dt.datetime.now().isoformat(), "themes": theme_summary}


def print_report(report: dict) -> None:
    print(f"\n=== 套牢賣壓代理指標 {report['generated_at']} ===")
    print("(近60日高檔爆量日的量能加權均價,不是真實持股成本分佈,只是可計算的代理指標)\n")

    for theme, entries in report["themes"].items():
        print(f"--- {theme} ---")
        for e in entries:
            if e["zone"] is None:
                print(f"    {e['name']}({e['code']}) 歷史資料不足,無法計算")
                continue
            z = e["zone"]
            tag_str = " | ".join(e["tags"]) if e["tags"] else "-"
            if z["trapped_zone_price"] is None:
                print(f"    {e['name']}({e['code']}) 現價:{z['latest_close']} {z['note']}")
            else:
                print(
                    f"    {e['name']}({e['code']}) 現價:{z['latest_close']} "
                    f"套牢區參考價:{z['trapped_zone_price']} 距離:{z['distance_pct']:+.1f}% [{tag_str}]"
                )
        print()


def main() -> None:
    report = build_report()
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_report(report)
    print(f"完整結果已存至 {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
