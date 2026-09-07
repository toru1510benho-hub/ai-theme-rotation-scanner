"""M4(昨日訊號→今日買賣區間規則) —— 獨立模組,純程式邏輯,不用AI、不接新資料源。

用「昨天收盤(最近一個已結算交易日)」的價格歷史算出兩個東西：
1. 買進區間：回檔到月線(MA20)附近、但短期均線仍在月線之上(沒有轉空)的價位帶。
2. 停損/停利參考價：近20日低點(跌破=停損)、乖離率過大(=過熱,慎防追高/分批停利)。

這些都是「昨日就能算好的規則」，跟盤中報價無關；如果`intraday_quotes_latest.json`存在
(M3剛跑過),會拿現在的即時價格去對照這些區間，標出「現在正處於買進區間」之類的即時狀態——
但即使M3沒跑過，這支腳本仍可以獨立算出區間本身，不依賴M3。

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
logger = logging.getLogger("buy_sell_zones")

OUTPUT_PATH = DOCS_DIR / "buy_sell_zones_latest.json"
INTRADAY_QUOTES_PATH = DOCS_DIR / "intraday_quotes_latest.json"

MA_SHORT = 5
MA_LONG = 20
BUY_ZONE_TOLERANCE = 0.03  # 買進區間以月線(MA20)為中心,上下3%容忍度
STOP_LOSS_BUFFER = 0.02  # 近20日低點再往下2%才算真的跌破(避免假跌破雜訊)
OVERHEATED_BIAS_PCT = 15  # 乖離率超過這個數字視為過熱,慎防追高
ZONE_EDGE_SLACK = 0.02  # 買進區間邊界再放寬2%容忍度,避免像台達電只差0.1%就被判定「不在區間內」
BIG_DROP_PCT_THRESHOLD = -5.0  # 單日跌幅達到這個數字視為「急殺」


def _load_intraday_quotes() -> dict[str, dict]:
    """讀M3的輸出(如果有的話)。沒有就回傳空dict,不影響M4自己算區間。"""
    if not INTRADAY_QUOTES_PATH.exists():
        return {}
    try:
        report = json.loads(INTRADAY_QUOTES_PATH.read_text(encoding="utf-8"))
        quotes = {}
        for theme_data in report.get("themes", {}).values():
            for s in theme_data.get("stocks", []):
                quotes[s["code"]] = s
        return quotes
    except Exception:  # noqa: BLE001
        logger.warning("讀取M3盤中報價失敗,買賣區間仍會照算,只是不會有即時價格對照", exc_info=True)
        return {}


def compute_zone(closes: list[float]) -> dict | None:
    """回傳單一個股的買賣區間規則。closes由舊到新排序,至少要有20筆才能算。"""
    if len(closes) < MA_LONG:
        return None

    ma20 = sum(closes[-MA_LONG:]) / MA_LONG
    ma5 = sum(closes[-MA_SHORT:]) / MA_SHORT
    latest_close = closes[-1]
    recent_low_20d = min(closes[-MA_LONG:])
    recent_high_20d = max(closes[-MA_LONG:])
    bias_from_ma20_pct = (latest_close / ma20 - 1) * 100
    is_short_term_uptrend = ma5 > ma20  # 短期均線在月線之上,才算「回檔」而不是「趨勢轉空」

    return {
        "latest_close": round(latest_close, 2),
        "ma5": round(ma5, 2),
        "ma20": round(ma20, 2),
        "bias_from_ma20_pct": round(bias_from_ma20_pct, 2),
        "is_short_term_uptrend": is_short_term_uptrend,
        "buy_zone_low": round(ma20 * (1 - BUY_ZONE_TOLERANCE), 2),
        "buy_zone_high": round(ma20 * (1 + BUY_ZONE_TOLERANCE), 2),
        "stop_loss_price": round(recent_low_20d * (1 - STOP_LOSS_BUFFER), 2),
        "recent_low_20d": round(recent_low_20d, 2),
        "recent_high_20d": round(recent_high_20d, 2),
        "is_overheated": bias_from_ma20_pct > OVERHEATED_BIAS_PCT,
    }


def _evaluate_live_status(zone: dict, live_price: float | None, change_pct: float | None = None) -> list[str]:
    """拿即時價格(如果有)對照區間規則,產生現在的狀態標籤。純規則判斷,不用AI。
    change_pct是今天的單日漲跌幅(來自M3),用來判斷「單日急殺+接近支撐」這種低接情境。
    """
    if live_price is None:
        return []

    tags = []
    # 邊界加ZONE_EDGE_SLACK容忍度,避免像台達電只差0.1%就被死板判定成「不在區間內」
    zone_low_with_slack = zone["buy_zone_low"] * (1 - ZONE_EDGE_SLACK)

    if live_price <= zone["stop_loss_price"]:
        tags.append("跌破停損參考價,建議出場")
    elif zone["is_short_term_uptrend"] and zone_low_with_slack <= live_price <= zone["buy_zone_high"]:
        tags.append("現在在買進區間內(回檔到月線附近,短均仍在月線之上)")
    elif not zone["is_short_term_uptrend"] and live_price <= zone["ma20"]:
        tags.append("短均已跌破月線,回檔≠買點,先觀望")

    if zone["is_overheated"] and live_price >= zone["recent_high_20d"] * 0.98:
        tags.append("已逼近近20日高點且乖離過大,慎防追高/可分批停利")

    # 單日急殺就值得留意(使用者明確要求:只要急殺都可以看,趨勢未破/接近支撐是加分項不是硬條件)
    if change_pct is not None and change_pct <= BIG_DROP_PCT_THRESHOLD:
        bonuses = []
        if zone["is_short_term_uptrend"]:
            bonuses.append("趨勢未破")
        if live_price > zone["stop_loss_price"]:
            bonuses.append("未破支撐")

        if bonuses:
            tags.append(f"單日急殺{change_pct:+.1f}%({'+'.join(bonuses)}),低接候選")
        else:
            tags.append(f"單日急殺{change_pct:+.1f}%,但已破支撐/趨勢偏弱,留意但風險較高")

    return tags


def build_report() -> dict:
    live_quotes = _load_intraday_quotes()
    stock_history_cache = _load_stock_history_cache()

    theme_summary = {}
    for theme, stocks in THEMES.items():
        entries = []
        for s in stocks:
            code, name, market = s["code"], s["name"], s["market"]
            logger.info("計算買賣區間 %s(%s)...", name, code)
            history = fetch_stock_history_cached(code, market, stock_history_cache)
            closes = [r["close"] for r in history]
            zone = compute_zone(closes)
            if zone is None:
                entries.append({"code": code, "name": name, "market": market, "zone": None, "tags": []})
                continue

            live = live_quotes.get(code, {})
            live_price = live.get("price")
            change_pct = live.get("change_pct")
            tags = _evaluate_live_status(zone, live_price, change_pct)
            entries.append({
                "code": code,
                "name": name,
                "market": market,
                "live_price": live_price,
                "change_pct": change_pct,
                "zone": zone,
                "tags": tags,
            })

        theme_summary[theme] = entries

    _save_stock_history_cache(stock_history_cache)
    return {
        "generated_at": dt.datetime.now().isoformat(),
        "has_live_quotes": bool(live_quotes),
        "themes": theme_summary,
    }


def print_report(report: dict) -> None:
    print(f"\n=== 買賣區間規則 {report['generated_at']} ===")
    if not report["has_live_quotes"]:
        print("(找不到M3的即時報價快照,以下只有區間本身,沒有『現在價格對照』——先跑 intraday_quotes_scan.py 會更完整)")

    for theme, entries in report["themes"].items():
        print(f"\n--- {theme} ---")
        for e in entries:
            if e["zone"] is None:
                print(f"    {e['name']}({e['code']}) 歷史資料不足,無法計算")
                continue
            z = e["zone"]
            live = f"{e['live_price']}" if e.get("live_price") is not None else "N/A"
            trend = "短均>月線" if z["is_short_term_uptrend"] else "短均<月線"
            tag_str = " | ".join(e["tags"]) if e["tags"] else "-"
            print(
                f"    {e['name']}({e['code']}) 現價:{live} 昨收:{z['latest_close']} "
                f"月線:{z['ma20']}({trend}) 乖離:{z['bias_from_ma20_pct']:+.1f}% "
                f"買進區間:[{z['buy_zone_low']}~{z['buy_zone_high']}] 停損參考:{z['stop_loss_price']} "
                f"[{tag_str}]"
            )


def main() -> None:
    report = build_report()
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_report(report)
    print(f"\n完整結果已存至 {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
