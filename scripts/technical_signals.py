"""技術面訊號:個股日K(MA5/MA20/量能比)+ 三大法人買賣超,取自證交所公開資料。

STOCK_DAY 是官方盤後才會更新的資料,盤中執行時「今日」可能還沒出現,
所以這裡算出來的均線/量能是以最近一個已結算交易日為準,不是即時報價。
"""
from __future__ import annotations

import datetime as dt
import logging

import requests

from fetch_sources import REQUEST_TIMEOUT, USER_AGENT

logger = logging.getLogger("news_monitor.technical")

STOCK_DAY_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"

MA_SHORT = 5
MA_LONG = 20
VOLUME_SURGE_RATIO = 1.5


def _roc_to_iso(roc_date: str) -> str:
    """'115/08/05' -> '2026-08-05'"""
    y, m, d = roc_date.split("/")
    return f"{int(y) + 1911:04d}-{int(m):02d}-{int(d):02d}"


def _fetch_stock_day_month(code: str, year: int, month: int) -> list[dict]:
    date_str = f"{year:04d}{month:02d}01"
    try:
        resp = requests.get(
            STOCK_DAY_URL,
            params={"response": "json", "date": date_str, "stockNo": code},
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("stat") != "OK":
            return []
        fields = payload["fields"]
        date_idx = fields.index("日期")
        volume_idx = fields.index("成交股數")
        close_idx = fields.index("收盤價")
        rows = []
        for row in payload.get("data", []):
            try:
                rows.append(
                    {
                        "date": _roc_to_iso(row[date_idx]),
                        "volume": int(row[volume_idx].replace(",", "")),
                        "close": float(row[close_idx].replace(",", "")),
                    }
                )
            except ValueError:
                continue  # 收盤價為 "--"(當日無成交)等非數字資料,跳過
        return rows
    except Exception as exc:  # noqa: BLE001
        logger.warning("STOCK_DAY 抓取失敗(%s, %d-%02d): %s", code, year, month, exc)
        return []


def _fetch_stock_day_history(code: str, months: int = 4) -> list[dict]:
    """抓當月+前(months-1)個月。預設4個月(約60個交易日),供rise_60d_pct等波段動能指標使用。
    月分越多請求數越多,一般監控流程不要調大這個預設值,避免拖慢每小時排程/增加被證交所限流風險;
    年線(MA240)這種需要長期歷史的用途改用 fetch_annual_line_signal(),不要動這個預設。
    """
    cursor = dt.date.today().replace(day=1)
    rows = []
    for _ in range(months):
        rows += _fetch_stock_day_month(code, cursor.year, cursor.month)
        cursor = (cursor - dt.timedelta(days=1)).replace(day=1)
    dedup = {r["date"]: r for r in rows}
    return sorted(dedup.values(), key=lambda r: r["date"])


MA_ANNUAL = 240
MA_QUARTER = 60


def fetch_annual_line_signal(code: str) -> dict:
    """抓約13個月歷史,算年線(MA240)+季線(MA60)乖離率,供「年線績優股」策略篩選用。
    只在使用者從XQ帶回候選股名單、需要交叉驗證年線/季線位置時才呼叫,不要放進每小時排程的批次流程
    (13個月的請求量對很多檔股票一起跑會太重,有被證交所限流的風險,呼叫時建議每檔間隔1~2秒)。
    只涵蓋上市(TSE)股票,上櫃(TPEx)股票查不到資料會回傳available=False。
    """
    history = _fetch_stock_day_history(code, months=13)
    closes = [r["close"] for r in history]
    if len(closes) < MA_ANNUAL:
        result = {"available": False, "trading_days": len(closes)}
        if len(closes) >= MA_QUARTER:
            ma60 = sum(closes[-MA_QUARTER:]) / MA_QUARTER
            latest_close = closes[-1]
            result.update({
                "ma60": round(ma60, 2),
                "below_ma60": latest_close < ma60,
                "ma60_bias_pct": round((latest_close / ma60 - 1) * 100, 2),
            })
        return result

    ma240 = sum(closes[-MA_ANNUAL:]) / MA_ANNUAL
    ma60 = sum(closes[-MA_QUARTER:]) / MA_QUARTER
    latest_close = closes[-1]
    bias_pct = (latest_close / ma240 - 1) * 100
    ma60_bias_pct = (latest_close / ma60 - 1) * 100
    return {
        "available": True,
        "latest_close": latest_close,
        "latest_date": history[-1]["date"],
        "ma240": round(ma240, 2),
        "bias_pct": round(bias_pct, 2),
        "near_annual_line": abs(bias_pct) <= 5,
        "ma60": round(ma60, 2),
        "below_ma60": latest_close < ma60,
        "ma60_bias_pct": round(ma60_bias_pct, 2),
    }


def _compute_ma_signals(history: list[dict]) -> dict:
    if not history:
        return {}
    closes = [r["close"] for r in history]
    volumes = [r["volume"] for r in history]
    latest_close = closes[-1]
    signal = {"latest_close": latest_close, "latest_date": history[-1]["date"]}

    if len(closes) >= MA_SHORT:
        ma5 = sum(closes[-MA_SHORT:]) / MA_SHORT
        signal["ma5"] = ma5
        signal["above_ma5"] = latest_close > ma5
    if len(closes) >= MA_LONG:
        ma20 = sum(closes[-MA_LONG:]) / MA_LONG
        signal["ma20"] = ma20
        signal["above_ma20"] = latest_close > ma20

    if len(volumes) >= MA_SHORT + 1:
        prev5_avg_volume = sum(volumes[-(MA_SHORT + 1):-1]) / MA_SHORT
        if prev5_avg_volume > 0:
            ratio = volumes[-1] / prev5_avg_volume
            signal["volume_ratio"] = ratio
            signal["volume_surge"] = ratio >= VOLUME_SURGE_RATIO

    # 供波段動能策略路由(strategy_router.py)使用的報酬率指標
    if len(closes) >= 6:
        signal["return_5d_pct"] = (latest_close / closes[-6] - 1) * 100
    if len(closes) >= 11:
        signal["return_10d_pct"] = (latest_close / closes[-11] - 1) * 100
    if len(closes) >= 61:
        signal["rise_60d_pct"] = (latest_close / closes[-61] - 1) * 100

    macd = _compute_macd(closes)
    if macd:
        signal.update(macd)
    return signal


def _ema_series(values: list[float], period: int) -> list[float]:
    k = 2 / (period + 1)
    ema = [values[0]]
    for v in values[1:]:
        ema.append(v * k + ema[-1] * (1 - k))
    return ema


def _compute_macd(closes: list[float]) -> dict:
    """DIF(12,26)/MACD9訊號線/OSC柱狀圖,供反彈股「族群止跌」判斷使用。
    只有約60~80天收盤價可用,EMA26/9在資料開頭還沒完全收斂,但足夠判斷近期柱狀圖翻轉方向。
    """
    if len(closes) < 35:
        return {}
    ema12 = _ema_series(closes, 12)
    ema26 = _ema_series(closes, 26)
    dif = [a - b for a, b in zip(ema12, ema26)]
    signal_line = _ema_series(dif, 9)
    osc = [d - s for d, s in zip(dif, signal_line)]
    return {
        "macd_dif": dif[-1],
        "macd_signal": signal_line[-1],
        "macd_osc": osc[-1],
        "macd_osc_prev": osc[-2],
        "macd_osc_turning_up": osc[-1] > osc[-2] and osc[-2] <= osc[-3],
    }


def fetch_institutional_flows(codes: set[str], date: dt.date | None = None) -> dict[str, dict]:
    """抓當日三大法人買賣超(全市場一次性查詢),回傳 {code: {foreign_net, trust_net}}。"""
    target_date = date or dt.date.today()
    date_str = target_date.strftime("%Y%m%d")
    try:
        resp = requests.get(
            T86_URL,
            params={"response": "json", "date": date_str, "selectType": "ALL"},
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("stat") != "OK":
            raise ValueError(f"T86 回應狀態異常: {payload.get('stat')}")

        fields = payload["fields"]
        code_idx = fields.index("證券代號")
        foreign_idx = fields.index("外陸資買賣超股數(不含外資自營商)")
        trust_idx = fields.index("投信買賣超股數")

        result = {}
        for row in payload.get("data", []):
            code = row[code_idx].strip()
            if code not in codes:
                continue
            try:
                result[code] = {
                    "foreign_net": int(row[foreign_idx].replace(",", "")),
                    "trust_net": int(row[trust_idx].replace(",", "")),
                }
            except ValueError:
                continue
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("T86 三大法人抓取失敗,已跳過: %s", exc)
        return {}


def get_signals(codes: set[str], cache: dict) -> dict[str, dict]:
    """回傳 {code: {ma5, ma20, above_ma5, above_ma20, volume_ratio, volume_surge, foreign_net, trust_net}}。
    cache 是 run.py 傳入、當日有效的快取 dict,函式內會就地更新 cache。
    """
    # 只有抓成功(非空)的結果才寫入快取,抓失敗/官方資料還沒公布時(空結果)
    # 下一次 hourly run 會重試,而不是整天卡在空結果。
    stock_day_cache = cache.setdefault("stock_day", {})
    for code in codes:
        if not stock_day_cache.get(code):
            history = _fetch_stock_day_history(code)
            if history:
                stock_day_cache[code] = history

    t86_cache = cache.get("t86")
    if not t86_cache:
        t86_cache = fetch_institutional_flows(codes)
        if t86_cache:
            cache["t86"] = t86_cache

    signals = {}
    for code in codes:
        signal = _compute_ma_signals(stock_day_cache.get(code, []))
        flow = t86_cache.get(code, {})
        signal.update(flow)
        signals[code] = signal
    return signals
