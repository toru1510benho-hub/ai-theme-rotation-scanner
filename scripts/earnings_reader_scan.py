"""財報自動偵測+解讀 —— 獨立模組。

做三件事：
1. 偵測：重用「事件日曆」模組已經在累積的「每日重大訊息」紀錄，比對關鍵字找出我們追蹤的股票
   裡誰最近公告了財務報告(季報/年報)。
2. 抓數字：用證交所/櫃買中心官方的「上市(櫃)公司當季合併損益表」開放資料，抓營收、毛利、
   營業利益、稅前淨利、本期淨利、每股盈餘，程式算出毛利率/營業利益率/淨利率——這些都是
   官方公布的當季彙總數字，不是逐股票去讀財報全文，所以還做不到「管理層語氣分析」「附注
   挖掘」這種深度精讀，只能算「數字面速覽」。
3. 累積比較基準：每次抓到的當季數字存進本地快取，之後才有「跟上一季比」的比較基準——
   第一次執行沒有比較基準是正常的，不是bug，快取會隨著每次執行慢慢累積。
4. AI解讀：只針對「最近有公告財報」的股票才呼叫AI，數字都是程式算好的，AI只負責把數字
   組織成中文解讀，不能自己編數字——跟AI合成建議那個功能用同一套規矩。
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
CACHE_DIR = BASE_DIR.parent / "data" / "cache"
sys.path.insert(0, str(BASE_DIR))
from theme_rotation_scan import THEMES, _get_json_with_retry  # noqa: E402  單一清單/請求邏輯來源
from event_calendar_scan import _load_event_log  # noqa: E402  重用「事件日曆」已經累積的公告紀錄
from ai_synthesis import GEMINI_MODEL, _load_gemini_api_key  # noqa: E402  重用同一套Gemini設定

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("earnings_reader")

TWSE_QUARTERLY_INCOME_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci"
TPEX_QUARTERLY_INCOME_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_ci"
EARNINGS_KEYWORDS = ["財務報告", "財務報表"]  # 公司公告財報時,重大訊息主旨通常會出現這幾個字

FINANCIALS_CACHE_PATH = CACHE_DIR / "earnings_financials_cache.json"
OUTPUT_PATH = DOCS_DIR / "earnings_reader_latest.json"
ANNOUNCEMENT_LOOKBACK_DAYS = 14


def _load_financials_cache() -> dict:
    if not FINANCIALS_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(FINANCIALS_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        logger.warning("財報數字快取檔損毀,視為空快取重建", exc_info=True)
        return {}


def _save_financials_cache(cache: dict) -> None:
    FINANCIALS_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _to_float(raw) -> float | None:
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def fetch_quarterly_income_map(url: str, code_field: str) -> dict[str, dict]:
    """回傳{代號: {year, quarter, revenue, gross_profit, operating_income, net_income, eps,
    gross_margin_pct, operating_margin_pct, net_margin_pct}}。這是官方公布的當季合併損益表彙總數字。
    """
    try:
        rows = _get_json_with_retry(url)
    except Exception:  # noqa: BLE001
        logger.warning("當季損益表抓取失敗(%s)", url, exc_info=True)
        return {}

    result = {}
    for row in rows:
        code = (row.get(code_field) or "").strip()
        revenue = _to_float(row.get("營業收入"))
        gross_profit = _to_float(row.get("營業毛利（毛損）淨額"))
        operating_income = _to_float(row.get("營業利益（損失）"))
        net_income = _to_float(row.get("本期淨利（淨損）"))
        eps = _to_float(row.get("基本每股盈餘（元）"))
        if not code or revenue is None:
            continue
        result[code] = {
            "year": row.get("年度") or row.get("Year"),
            "quarter": row.get("季別") or row.get("Season"),
            "revenue": revenue,
            "gross_profit": gross_profit,
            "operating_income": operating_income,
            "net_income": net_income,
            "eps": eps,
            "gross_margin_pct": round(gross_profit / revenue * 100, 2) if gross_profit is not None and revenue else None,
            "operating_margin_pct": round(operating_income / revenue * 100, 2) if operating_income is not None and revenue else None,
            "net_margin_pct": round(net_income / revenue * 100, 2) if net_income is not None and revenue else None,
        }
    return result


def find_recent_earnings_announcements(codes: set[str], within_days: int = ANNOUNCEMENT_LOOKBACK_DAYS) -> list[dict]:
    """從「事件日曆」模組已經累積的重大訊息紀錄裡,找出最近有公告財務報告的股票。"""
    log = _load_event_log()
    cutoff = (dt.date.today() - dt.timedelta(days=within_days)).isoformat()
    return [
        e for e in log
        if e["code"] in codes
        and e["announce_date"] >= cutoff
        and any(kw in e["subject"] for kw in EARNINGS_KEYWORDS)
    ]


def update_cache_and_get_prior(cache: dict, code: str, current: dict) -> dict | None:
    """把這次抓到的當季數字存進快取,回傳「上一次抓到的、不同季度」的舊數字當比較基準(沒有就回傳None)。"""
    key = f"{current['year']}Q{current['quarter']}"
    company_cache = cache.setdefault(code, {})
    prior_keys = sorted((k for k in company_cache if k != key), reverse=True)
    prior_entry = company_cache[prior_keys[0]] if prior_keys else None
    company_cache[key] = current
    return prior_entry


SYSTEM_PROMPT = """你是使用者的個人財報速覽助手。以下提供的每一個數字(營收、毛利率、營業利益率、
淨利率、EPS、跟上一季的變化)都已經由程式精確計算，你只能原封不動引用，絕對不能自己重新計算或
編造。這是官方公布的當季彙總數字，你沒有讀過財報全文，不能對管理層語氣、附注細節做任何推測或
評論，只能針對這些數字本身做3-4句中文解讀：這一季表現是變好還是變差、EPS有沒有跟著營收同步。
最後一行列出你引用的關鍵數字方便核對。"""


def build_earnings_prompt(entries: list[dict]) -> str:
    lines = []
    for e in entries:
        c = e["current"]
        lines.append(f"### {e['name']}({e['code']}) - {e['theme']}族群")
        lines.append(f"本季({c['year']}年第{c['quarter']}季)：營收{c['revenue']:,.0f}　毛利率{c['gross_margin_pct']}%　營業利益率{c['operating_margin_pct']}%　淨利率{c['net_margin_pct']}%　EPS{c['eps']}")
        if e.get("prior_quarter"):
            p = e["prior_quarter"]
            lines.append(f"上一次記錄({p['year']}年第{p['quarter']}季)：營收{p['revenue']:,.0f}　毛利率{p['gross_margin_pct']}%　EPS{p['eps']}")
            if e.get("revenue_qoq_pct") is not None:
                lines.append(f"營收變化：{e['revenue_qoq_pct']:+.1f}%")
        else:
            lines.append("(尚無上一次記錄可比較,這是本系統第一次抓到這檔的數字)")
        lines.append("")
    return "以下是最近公告財報的股票,附完整數字：\n\n" + "\n".join(lines)


def call_gemini_for_earnings(entries: list[dict]) -> str:
    from google import genai
    from google.genai import types

    api_key = _load_gemini_api_key()
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=30_000, retry_options=types.HttpRetryOptions(attempts=2, initial_delay=2, max_delay=10)),
    )
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=build_earnings_prompt(entries),
        config={"system_instruction": SYSTEM_PROMPT},
    )
    return response.text


def build_report() -> dict:
    all_codes = [(theme, s) for theme, stocks in THEMES.items() for s in stocks]
    name_by_code = {s["code"]: s["name"] for _, s in all_codes}
    theme_by_code = {s["code"]: theme for theme, s in all_codes}
    all_code_set = {s["code"] for _, s in all_codes}

    logger.info("抓取上市/上櫃公司當季損益表...")
    tse_income = fetch_quarterly_income_map(TWSE_QUARTERLY_INCOME_URL, "公司代號")
    tpex_income = fetch_quarterly_income_map(TPEX_QUARTERLY_INCOME_URL, "SecuritiesCompanyCode")

    recent_announcements = find_recent_earnings_announcements(all_code_set)
    announced_codes = {e["code"] for e in recent_announcements}
    if announced_codes:
        logger.info("最近%d天內有%d檔公告財報：%s", ANNOUNCEMENT_LOOKBACK_DAYS, len(announced_codes), announced_codes)
    else:
        logger.info("最近%d天內沒有追蹤股票公告財報(這段時間本來就是財報空窗期,不是bug)", ANNOUNCEMENT_LOOKBACK_DAYS)

    cache = _load_financials_cache()
    results = []
    for code in all_code_set:
        income = tse_income.get(code) or tpex_income.get(code)
        if not income:
            continue
        prior = update_cache_and_get_prior(cache, code, income)
        entry = {
            "code": code,
            "name": name_by_code[code],
            "theme": theme_by_code[code],
            "recently_announced": code in announced_codes,
            "current": income,
            "prior_quarter": prior,
        }
        if prior and prior.get("revenue"):
            entry["revenue_qoq_pct"] = round((income["revenue"] / prior["revenue"] - 1) * 100, 2)
        results.append(entry)
    _save_financials_cache(cache)

    to_interpret = [r for r in results if r["recently_announced"]]
    ai_summary = None
    if to_interpret:
        logger.info("%d檔最近公告財報,呼叫AI產生解讀...", len(to_interpret))
        try:
            ai_summary = call_gemini_for_earnings(to_interpret)
        except Exception:  # noqa: BLE001
            logger.warning("AI解讀呼叫失敗,保留數字但沒有中文解讀", exc_info=True)
            ai_summary = "(AI呼叫失敗,以下只有程式算出的數字,沒有AI解讀文字)"

    return {
        "generated_at": dt.datetime.now().isoformat(),
        "announcement_lookback_days": ANNOUNCEMENT_LOOKBACK_DAYS,
        "recently_announced_count": len(to_interpret),
        "ai_summary": ai_summary,
        "companies": results,
    }


def print_report(report: dict) -> None:
    print(f"\n=== 財報自動偵測+解讀 {report['generated_at']} ===")
    print(f"最近{report['announcement_lookback_days']}天內有{report['recently_announced_count']}檔公告財報\n")

    for c in report["companies"]:
        cur = c["current"]
        flag = " ⭐最近公告" if c["recently_announced"] else ""
        qoq = f" QoQ:{c['revenue_qoq_pct']:+.1f}%" if c.get("revenue_qoq_pct") is not None else " (尚無比較基準)"
        print(f"  {c['name']}({c['code']}) {cur['year']}Q{cur['quarter']} 營收:{cur['revenue']:,.0f} 毛利率:{cur['gross_margin_pct']}% EPS:{cur['eps']}{qoq}{flag}")

    if report["ai_summary"]:
        print(f"\n--- AI解讀 ---\n{report['ai_summary']}")


def main() -> None:
    report = build_report()
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_report(report)
    print(f"\n完整結果已存至 {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
