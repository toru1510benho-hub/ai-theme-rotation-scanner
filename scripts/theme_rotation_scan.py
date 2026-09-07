"""AI受惠股 資金輪動掃描 —— 「買黑不買紅」選股輔助工具。

核心邏輯（對應使用者的操作哲學）：
1. 把AI受惠股分成幾個子族群(CPO/矽光子、記憶體、被動元件...)。
2. 找出「族群裡同業已經漲、但這檔還沒漲」的落後股(黑馬候選) —— 同業漲代表題材/資金已經確認，
   這檔還沒漲不是基本面有問題，補漲機率高、風險相對低。
3. 疊加本益比(低位階)、三大法人今日買賣超(資金流向)兩個濾網，排除「便宜是因為爛」的假黑馬。
4. 族群層級加總買賣超金額，抓出「資金正在流入哪個族群」，越早換到有資金流入的族群越好。

資金流向/PE 用證交所(TWSE)、櫃買中心(TPEx)官方 OpenAPI，避免 FinMind 個股/欄位層級資料品質問題
(見 platform_finmind_data_quality 記憶)。股價歷史改用yfinance抓(auto_adjust=False取得原始收盤價,
已比對過跟TWSE官方STOCK_DAY逐日吻合)——2026-09-02測試時把STOCK_DAY那個IP測到觸發WAF安全性封鎖,
改用yfinance後不再依賴STOCK_DAY,順便讓TPEx股票也能算60日動能(以前TPEx沒有歷史資料)。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import sys
import time
from pathlib import Path

import requests
import yfinance as yf

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")  # logging預設輸出到stderr,一併修正Windows主控台亂碼

BASE_DIR = Path(__file__).parent
DOCS_DIR = BASE_DIR.parent / "docs"
CACHE_DIR = BASE_DIR.parent / "data" / "cache"
sys.path.insert(0, str(BASE_DIR))  # technical_signals/fundamentals已vendor進scripts/,不再依賴新聞監控/

from technical_signals import fetch_institutional_flows  # noqa: E402
import fundamentals as fundamentals_module  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("theme_rotation")

REQUEST_TIMEOUT = 10
USER_AGENT = "Mozilla/5.0 (theme-rotation personal script)"

TWSE_PE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
TPEX_PE_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"
TPEX_3INSTI_URL = "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading"

OUTPUT_PATH = DOCS_DIR / "theme_rotation_latest.json"
FLOW_CACHE_PATH = CACHE_DIR / "flow_history_cache.json"
STOCK_HISTORY_CACHE_PATH = CACHE_DIR / "stock_history_cache.json"
STOCK_HISTORY_MONTHS = 4
FLOW_WINDOWS = (1, 3, 5, 7, 10)  # 交易日,不是日曆天;10個交易日≈2週
MAX_LOOKBACK_DAYS = 30  # 日曆天上限,避免連假期間找不到10個交易日時無限迴圈

# ── AI受惠股族群分類，2026-09-02使用者已補充個股(代號經TWSE/TPEx官方PE清單即時核對) ──
# market: TSE=上市 / TPEX=上櫃(兩者股價歷史都用yfinance抓,TSE/TPEX待遇一致)
THEMES: dict[str, list[dict]] = {
    "CPO/矽光子": [
        {"code": "4979", "name": "華星光", "market": "TPEX"},
        {"code": "3363", "name": "上詮", "market": "TPEX"},
        {"code": "4977", "name": "眾達-KY", "market": "TSE"},
        {"code": "3234", "name": "光環", "market": "TPEX"},
        {"code": "3081", "name": "聯亞", "market": "TPEX"},
        {"code": "3450", "name": "聯鈞", "market": "TSE"},
        {"code": "3163", "name": "波若威", "market": "TPEX"},
        {"code": "6442", "name": "光聖", "market": "TSE"},
        {"code": "3008", "name": "大立光", "market": "TSE"},
    ],
    "記憶體": [
        {"code": "2408", "name": "南亞科", "market": "TSE"},
        {"code": "3260", "name": "威剛", "market": "TPEX"},
        {"code": "2337", "name": "旺宏", "market": "TSE"},
        {"code": "8299", "name": "群聯", "market": "TPEX"},
        {"code": "3006", "name": "晶豪科", "market": "TSE"},
        {"code": "4967", "name": "十銓", "market": "TSE"},
        {"code": "2344", "name": "華邦電", "market": "TSE"},
    ],
    "晶圓代工": [
        {"code": "2303", "name": "聯電", "market": "TSE"},
        {"code": "6770", "name": "力積電", "market": "TSE"},
    ],
    "被動元件": [
        {"code": "2327", "name": "國巨", "market": "TSE"},
        {"code": "2492", "name": "華新科", "market": "TSE"},
        {"code": "3026", "name": "禾伸堂", "market": "TSE"},
        {"code": "2375", "name": "智寶", "market": "TSE"},
        {"code": "2486", "name": "一詮", "market": "TSE"},
    ],
    "AI伺服器組裝": [
        {"code": "2382", "name": "廣達", "market": "TSE"},
        {"code": "6669", "name": "緯穎", "market": "TSE"},
        {"code": "2356", "name": "英業達", "market": "TSE"},
        {"code": "3231", "name": "緯創", "market": "TSE"},
        {"code": "2059", "name": "川湖", "market": "TSE"},
    ],
    "散熱": [
        {"code": "3017", "name": "奇鋐", "market": "TSE"},
        {"code": "3324", "name": "雙鴻", "market": "TPEX"},
        {"code": "3653", "name": "健策", "market": "TSE"},
    ],
    "ABF載板/PCB": [
        {"code": "3037", "name": "欣興", "market": "TSE"},
        {"code": "8046", "name": "南電", "market": "TSE"},
        {"code": "6274", "name": "台燿", "market": "TPEX"},
        {"code": "2368", "name": "金像電", "market": "TSE"},
        {"code": "6213", "name": "聯茂", "market": "TSE"},
    ],
    "電源供應": [
        {"code": "2308", "name": "台達電", "market": "TSE"},
        {"code": "2301", "name": "光寶科", "market": "TSE"},
    ],
}


def _roc_today() -> str:
    today = dt.date.today()
    return f"{today.year - 1911}{today.month:02d}{today.day:02d}"


def _get_json_with_retry(url: str, retries: int = 2, backoff: float = 2.0) -> list | dict:
    """TPEx開放平台偶爾會回傳520(暫時性伺服器錯誤),重試個1~2次通常就會過。"""
    last_exc = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff)
    raise last_exc


def fetch_twse_pe_map() -> dict[str, dict]:
    """全上市股票本益比+股息殖利率,一次抓全部再挑需要的代號。"""
    try:
        rows = _get_json_with_retry(TWSE_PE_URL)
        result = {}
        for row in rows:
            code = row.get("Code", "").strip()
            entry = {}
            try:
                if row.get("PEratio"):
                    entry["pe"] = float(row["PEratio"])
                if row.get("DividendYield"):
                    entry["dividend_yield"] = float(row["DividendYield"])
            except ValueError:
                pass
            if code and entry:
                result[code] = entry
        return result
    except Exception:  # noqa: BLE001
        logger.warning("TWSE 本益比抓取失敗", exc_info=True)
        return {}


def fetch_tpex_pe_map() -> dict[str, dict]:
    try:
        rows = _get_json_with_retry(TPEX_PE_URL)
        result = {}
        for row in rows:
            code = row.get("SecuritiesCompanyCode", "").strip()
            entry = {}
            try:
                if row.get("PriceEarningRatio"):
                    entry["pe"] = float(row["PriceEarningRatio"])
                if row.get("YieldRatio"):
                    entry["dividend_yield"] = float(row["YieldRatio"])
            except ValueError:
                pass
            if code and entry:
                result[code] = entry
        return result
    except Exception:  # noqa: BLE001
        logger.warning("TPEx 本益比抓取失敗", exc_info=True)
        return {}


def fetch_revenue_yoy_map(codes: set[str]) -> dict[str, float]:
    """月營收年增率,沿用新聞監控/fundamentals.py(只涵蓋上市公司)。"""
    try:
        throwaway_cache: dict = {}
        data = fundamentals_module.get_fundamentals(codes, throwaway_cache)
        return {code: v["revenue_yoy_pct"] for code, v in data.items() if v.get("revenue_yoy_pct") is not None}
    except Exception:  # noqa: BLE001
        logger.warning("月營收年增抓取失敗", exc_info=True)
        return {}


def fetch_tpex_flow_map() -> dict[str, dict]:
    """TPEx今日三大法人買賣超(股數)。欄位名是英文且不太一致,用寬鬆比對抓 Difference 欄位。"""
    try:
        rows = _get_json_with_retry(TPEX_3INSTI_URL)
        result = {}
        for row in rows:
            code = row.get("SecuritiesCompanyCode", "").strip()
            foreign_key = next((k for k in row if "ForeignInvestorsIncludeMainlandAreaInvestors-Difference" in k), None)
            trust_key = "SecuritiesInvestmentTrustCompanies-Difference"
            try:
                foreign_net = int(row.get(foreign_key, "0").replace(",", "")) if foreign_key else 0
                trust_net = int(row.get(trust_key, "0").replace(",", ""))
            except ValueError:
                continue
            result[code] = {"foreign_net": foreign_net, "trust_net": trust_net}
        return result
    except Exception:  # noqa: BLE001
        logger.warning("TPEx 三大法人抓取失敗", exc_info=True)
        return {}


def fetch_latest_institutional_flows(codes: set[str], max_days_back: int = 5) -> dict[str, dict]:
    """T86在當天收盤後一段時間才會公布,盤中執行時「今日」通常還沒有資料。
    往前找最近一個有公布資料的交易日(略過假日/尚未公布的當天),避免整份報告的資金流向都是N/A。
    """
    for delta in range(max_days_back):
        target_date = dt.date.today() - dt.timedelta(days=delta)
        flows = fetch_institutional_flows(codes, date=target_date)
        if flows:
            if delta > 0:
                logger.info("今日三大法人資料尚未公布,改用 %s 的資料", target_date.isoformat())
            return flows
    return {}


def _rise_pct(closes: list[float], days: int) -> float | None:
    if len(closes) < days + 1:
        return None
    return (closes[-1] / closes[-1 - days] - 1) * 100


def _load_stock_history_cache() -> dict:
    if not STOCK_HISTORY_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(STOCK_HISTORY_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        logger.warning("股價歷史快取檔損毀,視為空快取重建", exc_info=True)
        return {}


def _save_stock_history_cache(cache: dict) -> None:
    STOCK_HISTORY_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_stock_history_cached(code: str, market: str, cache: dict, months: int = STOCK_HISTORY_MONTHS) -> list[dict]:
    """股價歷史改用yfinance抓(TSE/TPEx都適用),不再用TWSE STOCK_DAY —— 2026-09-02當天測試把
    STOCK_DAY那個IP測到觸發WAF安全性封鎖(不是普通限流,curl直接被導去「因為安全考量」頁面)。
    已比對過:yfinance用auto_adjust=False(拿原始收盤價,不是還原權息後的調整價)算出來的收盤價,
    跟TWSE官方STOCK_DAY對66個交易日100%吻合,可以放心當替代資料源;TPEx也一併解決(以前沒有
    TPEx歷史資料的限制,現在TSE/TPEx待遇一致)。
    逐日落地快取,當天抓過就不重抓(這支腳本設計成一天跑一次,不需要盤中重新整理歷史K線)。
    """
    today_str = dt.date.today().isoformat()
    cached_entry = cache.get(code)
    if cached_entry and cached_entry.get("as_of") == today_str and cached_entry.get("rows"):
        return cached_entry["rows"]

    symbol = f"{code}.TW" if market == "TSE" else f"{code}.TWO"
    try:
        hist = yf.Ticker(symbol).history(period=f"{months}mo", auto_adjust=False)
        rows = [
            {"date": idx.strftime("%Y-%m-%d"), "close": round(float(row["Close"]), 2), "volume": int(row["Volume"])}
            for idx, row in hist.iterrows()
            if row["Close"] == row["Close"]  # 濾掉yfinance偶爾夾帶的非交易日空值列(NaN != NaN)
        ]
    except Exception:  # noqa: BLE001
        logger.warning("yfinance股價歷史抓取失敗(%s)", symbol, exc_info=True)
        rows = []

    if rows:
        cache[code] = {"as_of": today_str, "rows": rows}
        return rows
    if cached_entry:
        return cached_entry.get("rows", [])  # 這次抓失敗,退回用舊快取(可能是昨天的),總比沒有好
    return []


def _load_flow_cache() -> dict[str, dict[str, dict]]:
    if not FLOW_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(FLOW_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        logger.warning("資金流向快取檔損毀,視為空快取重建", exc_info=True)
        return {}


def _save_flow_cache(cache: dict) -> None:
    FLOW_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_flow_history(codes: set[str], trading_days_needed: int = max(FLOW_WINDOWS)) -> list[str]:
    """確保本地快取裡有最近trading_days_needed個交易日、且涵蓋所有codes的三大法人買賣超資料。
    已經快取過的交易日不會重打API;族群清單新增個股時,舊快取缺這些代號會自動補抓當天資料覆蓋回去。
    回傳可用交易日日期字串清單,由新到舊排序。
    """
    cache = _load_flow_cache()
    available_dates: list[str] = []
    checked = 0
    cursor = dt.date.today()

    while len(available_dates) < trading_days_needed and checked < MAX_LOOKBACK_DAYS:
        date_str = cursor.isoformat()
        cached_day = cache.get(date_str)
        missing_codes = codes - set(cached_day.keys()) if cached_day else codes

        if cached_day and not missing_codes:
            available_dates.append(date_str)
        else:
            flows = fetch_institutional_flows(codes, date=cursor)
            time.sleep(0.8)  # 對TWSE客氣一點;找不到10個交易日時會連續往前試好幾天,不要無間隔連打
            if flows:
                cache[date_str] = {**(cached_day or {}), **flows}
                available_dates.append(date_str)
                logger.info("資金流向快取新增/補齊 %s(%d檔)", date_str, len(cache[date_str]))

        cursor -= dt.timedelta(days=1)
        checked += 1

    _save_flow_cache(cache)
    return available_dates


def compute_flow_windows(codes: set[str], available_dates: list[str]) -> dict[str, dict]:
    """回傳 {code: {"1d":淨股數,"3d":...,"5d":...,"7d":...,"10d":...,"streak_days":連續買超天數}}。
    可用交易日不足某個視窗天數時該視窗回傳None,不用不足的天數硬湊(避免用3天的量冒充5天)。
    """
    cache = _load_flow_cache()
    result: dict[str, dict] = {}
    for code in codes:
        daily_net = []
        for date_str in available_dates:
            day_flow = cache.get(date_str, {}).get(code)
            daily_net.append(
                None if day_flow is None else (day_flow.get("foreign_net") or 0) + (day_flow.get("trust_net") or 0)
            )

        windows = {}
        for w in FLOW_WINDOWS:
            window_values = daily_net[:w]
            windows[f"{w}d"] = (
                None if (len(window_values) < w or any(v is None for v in window_values)) else sum(window_values)
            )

        streak = 0
        for v in daily_net:
            if v is not None and v > 0:
                streak += 1
            else:
                break
        windows["streak_days"] = streak
        result[code] = windows
    return result


def collect_all_stocks() -> dict[str, dict]:
    """回傳 {code: {name, market, theme, close, change_pct, rise_5d_pct, rise_60d_pct, pe, foreign_net, trust_net}}。"""
    all_codes = [(theme, s) for theme, stocks in THEMES.items() for s in stocks]
    tse_codes = {s["code"] for _, s in all_codes if s["market"] == "TSE"}
    tpex_codes = {s["code"] for _, s in all_codes if s["market"] == "TPEX"}

    logger.info("抓取本益比/三大法人資料...")
    twse_pe = fetch_twse_pe_map()
    tpex_pe = fetch_tpex_pe_map()
    tpex_flow = fetch_tpex_flow_map()
    tse_flow = fetch_latest_institutional_flows(tse_codes)

    logger.info("補齊資金流向多日快取(1/3/5/7/10個交易日)...")
    available_dates = ensure_flow_history(tse_codes)
    flow_windows_map = compute_flow_windows(tse_codes, available_dates)

    revenue_yoy_map = fetch_revenue_yoy_map(tse_codes)

    stock_history_cache = _load_stock_history_cache()
    records: dict[str, dict] = {}
    for theme, s in all_codes:
        code, name, market = s["code"], s["name"], s["market"]
        record = {"code": code, "name": name, "market": market, "theme": theme}

        logger.info("抓取 %s(%s) 股價歷史(yfinance)...", name, code)
        history = fetch_stock_history_cached(code, market, stock_history_cache)
        closes = [r["close"] for r in history]
        record["close"] = closes[-1] if closes else None
        record["rise_5d_pct"] = _rise_pct(closes, 5)
        record["rise_60d_pct"] = _rise_pct(closes, 60)

        if market == "TSE":
            pe_info = twse_pe.get(code, {})
            record["pe"] = pe_info.get("pe")
            record["dividend_yield"] = pe_info.get("dividend_yield")
            record["revenue_yoy_pct"] = revenue_yoy_map.get(code)
            flow = tse_flow.get(code, {})
            record["foreign_net"] = flow.get("foreign_net")
            record["trust_net"] = flow.get("trust_net")
            record["flow_windows"] = flow_windows_map.get(code, {})
        else:  # TPEX;股價歷史跟TSE待遇一致,但資金流向/PE維持只有今日快照(T86沒有TPEx歷史窗口)
            pe_info = tpex_pe.get(code, {})
            record["pe"] = pe_info.get("pe")
            record["dividend_yield"] = pe_info.get("dividend_yield")
            record["revenue_yoy_pct"] = None  # 月營收開放資料只涵蓋上市公司,TPEx暫無
            flow = tpex_flow.get(code, {})
            record["foreign_net"] = flow.get("foreign_net")
            record["trust_net"] = flow.get("trust_net")

        records[code] = record

    _save_stock_history_cache(stock_history_cache)
    return records


def _money_flow_value(record: dict) -> float | None:
    """買賣超股數 * 收盤價,粗估買賣超金額(元),正值代表法人今日淨買超。
    foreign_net/trust_net 兩者都沒抓到時回傳None(資料缺漏),不能當作0(真的沒有買賣超)處理。
    """
    fn, tn, close = record.get("foreign_net"), record.get("trust_net"), record.get("close")
    if close is None or (fn is None and tn is None):
        return None
    net_shares = (fn or 0) + (tn or 0)
    return net_shares * close


def _flow_window_value(record: dict, window_key: str) -> float | None:
    """window_key例如"5d"。把flow_windows裡的累積淨股數換算成金額(用今日收盤價估,不是每天各自的收盤價)。"""
    close = record.get("close")
    shares = (record.get("flow_windows") or {}).get(window_key)
    if close is None or shares is None:
        return None
    return shares * close


def _primary_flow_value(record: dict) -> tuple[float | None, str]:
    """判斷資金流向優先用5日累積(比單日穩定,較不受單日雜訊影響干擾);
    TPEx股票或快取還不夠5天時退回今日單日買賣超。回傳(金額, 說明用的視窗標籤)。
    """
    five_day = _flow_window_value(record, "5d")
    if five_day is not None:
        return five_day, "近5日"
    return _money_flow_value(record), "今日"


def build_report(records: dict[str, dict]) -> dict:
    theme_summary = {}
    for theme, stocks in THEMES.items():
        theme_records = [records[s["code"]] for s in stocks if s["code"] in records]

        momentum_values = [r["rise_60d_pct"] for r in theme_records if r.get("rise_60d_pct") is not None]
        avg_momentum = sum(momentum_values) / len(momentum_values) if momentum_values else None

        primary_flows = [_primary_flow_value(r)[0] for r in theme_records]
        primary_flows = [v for v in primary_flows if v is not None]
        total_flow = sum(primary_flows) if primary_flows else None

        pe_values = [r["pe"] for r in theme_records if r.get("pe")]
        median_pe = sorted(pe_values)[len(pe_values) // 2] if pe_values else None

        candidates = []
        for r in theme_records:
            flow, flow_basis = _primary_flow_value(r)
            streak_days = (r.get("flow_windows") or {}).get("streak_days", 0)
            is_laggard = (
                r.get("rise_60d_pct") is not None
                and avg_momentum is not None
                and r["rise_60d_pct"] < avg_momentum
            )
            is_cheap = r.get("pe") is not None and median_pe is not None and r["pe"] <= median_pe
            is_flowing_in = flow is not None and flow > 0
            # 輕量版基本面分數(C項,加分項非硬篩選):股息殖利率≥3% + 月營收年增為正,兩者現有資料都有
            is_quality = (
                r.get("dividend_yield") is not None
                and r["dividend_yield"] >= 3
                and r.get("revenue_yoy_pct") is not None
                and r["revenue_yoy_pct"] > 0
            )

            tags = []
            if is_laggard:
                tags.append("落後同業")
            if is_cheap:
                tags.append("本益比低於族群中位數")
            if is_flowing_in:
                tags.append(f"法人{flow_basis}買超")
            if streak_days >= 3:
                tags.append(f"連續{streak_days}日買超")
            if is_quality:
                tags.append("基本面佳(殖利率+營收年增,輕量版)")
            if is_laggard and is_quality:
                tags.append("★低位階+基本面佳")
            if r.get("rise_60d_pct") is not None and avg_momentum is not None and r["rise_60d_pct"] > avg_momentum * 1.5 and avg_momentum > 0:
                tags.append("已大幅超前,慎防追高")

            flow_windows_value = {
                w: _flow_window_value(r, f"{w}d") for w in FLOW_WINDOWS
            }
            candidates.append({
                **r,
                "money_flow_value": _money_flow_value(r),
                "primary_flow_value": flow,
                "primary_flow_basis": flow_basis,
                "flow_windows_value": flow_windows_value,
                "flow_streak_days": streak_days,
                "tags": tags,
            })

        candidates.sort(
            key=lambda r: (r.get("rise_60d_pct") if r.get("rise_60d_pct") is not None else 999)
        )

        theme_summary[theme] = {
            "avg_momentum_60d_pct": avg_momentum,
            "total_money_flow_value": total_flow,
            "median_pe": median_pe,
            "stocks": candidates,
        }

    theme_ranking = sorted(
        theme_summary.items(),
        key=lambda kv: (kv[1]["total_money_flow_value"] or float("-inf")),
        reverse=True,
    )

    return {
        "generated_at": dt.datetime.now().isoformat(),
        "theme_ranking_by_money_flow": [name for name, _ in theme_ranking],
        "themes": theme_summary,
    }


def print_report(report: dict) -> None:
    print(f"\n=== AI受惠股 資金輪動掃描 {report['generated_at']} ===\n")
    print("族群資金流向排名(優先用近5日累積買賣超金額估算,快取不足5天時退回今日單日,由高到低):")
    for i, name in enumerate(report["theme_ranking_by_money_flow"], 1):
        flow = report["themes"][name]["total_money_flow_value"]
        flow_str = f"{flow:,.0f} 元" if flow is not None else "資料不足"
        momentum = report["themes"][name]["avg_momentum_60d_pct"]
        momentum_str = f"{momentum:+.1f}%" if momentum is not None else "N/A"
        print(f"  {i}. {name}  淨流向:{flow_str}  族群近60日平均漲幅:{momentum_str}")

    for theme, data in report["themes"].items():
        print(f"\n--- {theme} (族群近60日均漲幅 {data['avg_momentum_60d_pct'] if data['avg_momentum_60d_pct'] is not None else 'N/A'}, 中位數本益比 {data['median_pe']}) ---")
        for s in data["stocks"]:
            rise = f"{s['rise_60d_pct']:+.1f}%" if s.get("rise_60d_pct") is not None else "N/A(資料不足)"
            pe = s.get("pe") if s.get("pe") is not None else "N/A"
            fw = s.get("flow_windows_value", {})

            def _fmt(w):
                v = fw.get(w)
                return f"{v:+,.0f}" if v is not None else "N/A"

            flow_line = f"1日:{_fmt(1)} 3日:{_fmt(3)} 5日:{_fmt(5)} 7日:{_fmt(7)} 10日:{_fmt(10)}"
            tag_str = " | ".join(s["tags"]) if s["tags"] else "-"
            print(f"    {s['name']}({s['code']}) 60日漲幅:{rise} PE:{pe} 買賣超金額[{flow_line}] [{tag_str}]")


def main() -> None:
    records = collect_all_stocks()
    report = build_report(records)
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_report(report)
    print(f"\n完整結果已存至 {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
