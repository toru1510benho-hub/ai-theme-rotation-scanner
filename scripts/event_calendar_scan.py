"""M6(法說會/重大訊息/總經事件日曆) —— 獨立模組。

兩個部分：
1. 個股重大訊息(含法說會公告)：TWSE OpenAPI `t187ap04_L`(上市公司每日重大訊息)是「當天」的
   全市場公告,不是「未來排程表」,所以用累積式做法——每天跑一次,把當天發生、跟我們追蹤股票
   有關的公告記錄到本地log(`event_log.json`),久了就能看出「這檔最近公告過什麼」。
   只涵蓋上市(TSE)公司;TPEx公司這個資料集沒有涵蓋,是已知限制。
2. 總經事件(FOMC等)：TWSE沒有官方總經日曆,這裡用**手動維護的半靜態清單**,不做即時爬蟲
   (爬經濟日曆網站不穩定、容易被擋，投報比低)。2026年FOMC日期已用WebSearch核對聯準會官網
   (federalreserve.gov)，之後如果要延伸到2027年或其他總經事件，一樣要先查證再寫進來，
   不要憑印象猜日期——這種資料錯了比沒有更糟。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).parent
DOCS_DIR = BASE_DIR.parent / "docs"
CACHE_DIR = BASE_DIR.parent / "data" / "cache"
sys.path.insert(0, str(BASE_DIR))
from theme_rotation_scan import THEMES, USER_AGENT, REQUEST_TIMEOUT  # noqa: E402  單一清單來源

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("event_calendar")

MATERIAL_INFO_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"
EVENT_LOG_PATH = CACHE_DIR / "event_log.json"
OUTPUT_PATH = DOCS_DIR / "event_calendar_latest.json"

CONFERENCE_KEYWORDS = ["法說", "法人說明會", "Conference", "conference", "Summit", "說明會", "論壇", "Tour"]

# 2026年FOMC會議日期,來源:federalreserve.gov官方行事曆(2026-09-02用WebSearch核對)。
# 決策公布時間是美東下午2點,換算台灣時間通常是隔天凌晨,這裡記錄的是美國會議「最後一天」的日期。
FOMC_2026_MEETINGS = [
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]


def _roc_today() -> str:
    today = dt.date.today()
    return f"{today.year - 1911}{today.month:02d}{today.day:02d}"


def _roc_compact_to_iso(roc_str: str) -> str | None:
    """'1150901' -> '2026-09-01'。長度不對(髒資料)就回傳None,呼叫端自行處理。"""
    if not roc_str or len(roc_str) not in (6, 7):
        return None
    try:
        year = int(roc_str[:-4]) + 1911
        month = int(roc_str[-4:-2])
        day = int(roc_str[-2:])
        return f"{year:04d}-{month:02d}-{day:02d}"
    except ValueError:
        return None


def fetch_today_material_info() -> list[dict]:
    try:
        resp = requests.get(MATERIAL_INFO_URL, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception:  # noqa: BLE001
        logger.warning("重大訊息抓取失敗", exc_info=True)
        return []


def _load_event_log() -> list[dict]:
    if not EVENT_LOG_PATH.exists():
        return []
    try:
        return json.loads(EVENT_LOG_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        logger.warning("事件log損毀,視為空紀錄重建", exc_info=True)
        return []


def _save_event_log(log: list[dict]) -> None:
    EVENT_LOG_PATH.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def update_event_log(tracked_codes: set[str]) -> list[dict]:
    """抓今天的重大訊息,篩出我們追蹤的股票,去重後累加進log,回傳完整log。"""
    log = _load_event_log()
    seen_keys = {(e["code"], e["announce_date"], e["subject"]) for e in log}

    raw = fetch_today_material_info()
    new_count = 0
    for row in raw:
        code = row.get("公司代號", "").strip()
        if code not in tracked_codes:
            continue
        subject = row.get("主旨 ", "") or row.get("主旨", "")
        announce_date_roc = row.get("發言日期", "")
        announce_date = _roc_compact_to_iso(announce_date_roc)
        if announce_date is None:
            continue  # 日期格式異常的髒資料,跳過不記錄
        key = (code, announce_date, subject)
        if key in seen_keys:
            continue
        is_conference = any(kw in subject for kw in CONFERENCE_KEYWORDS)
        log.append({
            "code": code,
            "name": row.get("公司名稱", ""),
            "announce_date": announce_date,
            "subject": subject.strip(),
            "is_conference": is_conference,
        })
        seen_keys.add(key)
        new_count += 1

    if new_count:
        logger.info("新增 %d 筆重大訊息到event_log", new_count)
    _save_event_log(log)
    return log


def upcoming_fomc_meetings(within_days: int = 30) -> list[dict]:
    today = dt.date.today()
    result = []
    for date_str in FOMC_2026_MEETINGS:
        meeting_date = dt.date.fromisoformat(date_str)
        days_until = (meeting_date - today).days
        if 0 <= days_until <= within_days:
            result.append({"date": date_str, "days_until": days_until})
    return result


def build_report() -> dict:
    all_codes = {s["code"] for stocks in THEMES.values() for s in stocks}
    log = update_event_log(all_codes)

    recent_cutoff = (dt.date.today() - dt.timedelta(days=14)).isoformat()
    recent_events = [e for e in log if e["announce_date"] >= recent_cutoff]
    recent_events.sort(key=lambda e: e["announce_date"], reverse=True)

    return {
        "generated_at": dt.datetime.now().isoformat(),
        "upcoming_fomc": upcoming_fomc_meetings(),
        "recent_company_events_14d": recent_events,
        "note": "個股重大訊息只涵蓋上市(TSE)公司,TPEx公司這個資料集沒有涵蓋。",
    }


def print_report(report: dict) -> None:
    print(f"\n=== 事件日曆 {report['generated_at']} ===\n")

    print("未來30天內的FOMC會議:")
    if report["upcoming_fomc"]:
        for m in report["upcoming_fomc"]:
            print(f"  {m['date']}（{m['days_until']}天後）")
    else:
        print("  (30天內沒有排定的FOMC會議)")

    print(f"\n近14天個股重大訊息({report['note']})：")
    if not report["recent_company_events_14d"]:
        print("  (目前累積的log裡沒有近14天的紀錄——這個模組需要每天跑才會累積出歷史,剛開始用時資料會比較少)")
    for e in report["recent_company_events_14d"]:
        tag = " [法說會/法人會議相關]" if e["is_conference"] else ""
        print(f"  {e['announce_date']} {e['name']}({e['code']}){tag}: {e['subject'][:60]}")


def main() -> None:
    report = build_report()
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_report(report)
    print(f"\n完整結果已存至 {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
