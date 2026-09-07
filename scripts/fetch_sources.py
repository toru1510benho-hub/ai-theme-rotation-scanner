"""抓取各新聞/財經資料來源,統一轉成 dict 格式回傳。
單一來源失效時記錄警告並回傳空清單,不讓整支流程中斷。
"""
from __future__ import annotations

import datetime as dt
import logging

import feedparser
import requests

logger = logging.getLogger("news_monitor.fetch")

REQUEST_TIMEOUT = 10
USER_AGENT = "Mozilla/5.0 (news-monitor personal script)"

RSS_SOURCES = {
    "Yahoo奇摩股市-台股動態": "https://tw.stock.yahoo.com/rss?category=tw-market",
    "Yahoo奇摩股市-最新新聞": "https://tw.stock.yahoo.com/rss?category=news",
    "自由時報財經": "https://news.ltn.com.tw/rss/business.xml",
}

TWSE_VOLUME_RANK_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX20"


def _new_item(title, link, published, summary, source):
    return {
        "title": (title or "").strip(),
        "link": (link or "").strip(),
        "published": published,
        "summary": (summary or "").strip(),
        "source": source,
    }


def fetch_rss_sources() -> list[dict]:
    """抓取所有 RSS 來源,回傳統一格式的新聞項目清單。"""
    items = []
    for source_name, url in RSS_SOURCES.items():
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
            if parsed.bozo and not parsed.entries:
                raise ValueError(f"RSS 解析失敗: {parsed.bozo_exception}")
            for entry in parsed.entries:
                published = getattr(entry, "published", None) or getattr(entry, "updated", None)
                items.append(
                    _new_item(
                        title=entry.get("title"),
                        link=entry.get("link"),
                        published=published,
                        summary=entry.get("summary", ""),
                        source=source_name,
                    )
                )
        except Exception as exc:  # noqa: BLE001 - 單一來源失敗不能讓整支流程掛掉
            logger.warning("來源「%s」抓取失敗,已跳過: %s", source_name, exc)
    return items


def fetch_dynamic_hot_stocks(date: dt.date | None = None) -> list[dict]:
    """抓取證交所當日成交量值前20名,作為「今日熱門」動態清單。

    回傳格式: [{"code": "2330", "name": "台積電", "change_sign": "+" | "-" | "", "change": "3.50"}]
    """
    target_date = date or dt.date.today()
    date_str = target_date.strftime("%Y%m%d")
    try:
        resp = requests.get(
            TWSE_VOLUME_RANK_URL,
            params={"response": "json", "date": date_str},
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("stat") != "OK":
            raise ValueError(f"TWSE 回應狀態異常: {payload.get('stat')}")

        fields = payload["fields"]
        code_idx = fields.index("證券代號")
        name_idx = fields.index("證券名稱")
        sign_idx = fields.index("漲跌(+/-)")
        change_idx = fields.index("漲跌價差")

        hot_stocks = []
        for row in payload.get("data", []):
            sign_html = row[sign_idx] or ""
            if "red" in sign_html:
                sign = "+"
            elif "green" in sign_html:
                sign = "-"
            else:
                sign = ""
            hot_stocks.append(
                {
                    "code": row[code_idx],
                    "name": row[name_idx],
                    "change_sign": sign,
                    "change": row[change_idx],
                }
            )
        return hot_stocks
    except Exception as exc:  # noqa: BLE001
        logger.warning("TWSE 成交量排行抓取失敗,已跳過動態熱門股: %s", exc)
        return []


def fetch_all() -> tuple[list[dict], list[dict]]:
    """回傳 (新聞項目清單, 今日熱門股清單)。"""
    news_items = fetch_rss_sources()
    hot_stocks = fetch_dynamic_hot_stocks()
    return news_items, hot_stocks
