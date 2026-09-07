"""基本面訊號:月營收年增/月增,取自證交所開放資料(欄位裡已經算好百分比,不用自己重算)。"""
from __future__ import annotations

import logging

import requests

from fetch_sources import REQUEST_TIMEOUT, USER_AGENT

logger = logging.getLogger("news_monitor.fundamentals")

MONTHLY_REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"


def _fetch_all_monthly_revenue() -> list[dict]:
    try:
        resp = requests.get(MONTHLY_REVENUE_URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("月營收開放資料抓取失敗,已跳過基本面訊號: %s", exc)
        return []


def get_fundamentals(codes: set[str], cache: dict) -> dict[str, dict]:
    """回傳 {code: {revenue_month, revenue_yoy_pct, revenue_mom_pct}}。
    cache 是 run.py 傳入、當日有效的快取 dict,函式內就地更新 cache["fundamentals"]。
    """
    # 空結果(抓失敗)不寫入快取,讓下一次 hourly run 重試而不是整天卡住。
    raw = cache.get("fundamentals")
    if not raw:
        raw = _fetch_all_monthly_revenue()
        if raw:
            cache["fundamentals"] = raw

    result = {}
    for entry in raw:
        code = entry.get("公司代號", "").strip()
        if code not in codes:
            continue
        try:
            result[code] = {
                "revenue_month": entry.get("資料年月"),
                "revenue_yoy_pct": float(entry["營業收入-去年同月增減(%)"]),
                "revenue_mom_pct": float(entry["營業收入-上月比較增減(%)"]),
            }
        except (KeyError, ValueError, TypeError):
            continue
    return result
