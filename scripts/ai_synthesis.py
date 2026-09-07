"""M9(AI最終合成建議) —— 獨立模組。

設計原則(使用者堅持)：程式先把所有數字精算好，AI只負責把數字組織成中文建議敘述，
不負責計算、不能編造沒有提供的資訊——跟ai-trading-war-room的industry-report.ts同一套規矩。

流程：
1. 讀取M1~M8各模組的輸出JSON(缺哪個就跳過,不強制全部都要跑過)。
2. 用M1/M2/M4的標籤做一個簡單、透明的加分規則,選出訊號最集中的幾檔候選股
   (規則是寫死的計分,不是AI選的,可回溯核對)。
3. 把候選股的完整數字(不做任何加工詮釋)丟給Gemini,請它只用繁體中文組織成
   買賣建議敘述，並且要求逐項複述用到的具體數字，方便使用者自行核對。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).parent
DOCS_DIR = BASE_DIR.parent / "docs"
LOCAL_ENV_PATH = BASE_DIR.parent / ".env"  # 本機開發用,不進版控;雲端排程改讀GEMINI_API_KEY環境變數(GitHub Secret)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ai_synthesis")

OUTPUT_PATH = DOCS_DIR / "ai_synthesis_latest.json"
GEMINI_MODEL = "gemini-flash-lite-latest"  # 2026-09-02實測gemini-flash-latest(跟ai-trading-war-room
# 同一個別名)連續4次503/504逾時,換成lite版本秒回,文字組織這種任務lite版本夠用。之後如果
# gemini-flash-latest穩定了,兩個都能用,不用堅持切回去。
TOP_N_CANDIDATES = 8

# 正向標籤加分,負向標籤扣分——規則寫死、可回溯,不是AI主觀判斷
TAG_SCORES = {
    "落後同業": 2,
    "本益比低於族群中位數": 1,
    "基本面佳(殖利率+營收年增,輕量版)": 2,
    "★低位階+基本面佳": 3,
    "已大幅超前,慎防追高": -4,
    "已逼近近20日高點且乖離過大,慎防追高/可分批停利": -3,
}
BUY_ZONE_TAG_BONUS = 3
CONTINUOUS_BUY_TAG_PREFIX = "連續"  # 「連續N日買超」動態文字,用前綴比對


def _load_gemini_api_key() -> str:
    env_key = os.environ.get("GEMINI_API_KEY")
    if env_key:
        return env_key
    if LOCAL_ENV_PATH.exists():
        for line in LOCAL_ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError(f"找不到GEMINI_API_KEY(環境變數未設定,{LOCAL_ENV_PATH}也沒有)")


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        logger.warning("讀取 %s 失敗,視為此模組沒有資料", path, exc_info=True)
        return None


def load_all_modules() -> dict:
    return {
        "theme_rotation": _load_json(DOCS_DIR / "theme_rotation_latest.json"),  # M1+M2
        "intraday": _load_json(DOCS_DIR / "intraday_quotes_latest.json"),  # M3+M7
        "buy_sell_zones": _load_json(DOCS_DIR / "buy_sell_zones_latest.json"),  # M4
        "trapped_pressure": _load_json(DOCS_DIR / "trapped_pressure_latest.json"),  # M5
        "event_calendar": _load_json(DOCS_DIR / "event_calendar_latest.json"),  # M6
        "earnings_calendar": _load_json(DOCS_DIR / "earnings_calendar_latest.json"),  # M10
        "news": _load_json(DOCS_DIR / "news_scan_latest.json"),  # M8
    }


def _score_stock(tags: list[str]) -> int:
    score = 0
    for tag in tags:
        if tag in TAG_SCORES:
            score += TAG_SCORES[tag]
        elif tag.startswith(CONTINUOUS_BUY_TAG_PREFIX):
            score += 1
    return score


def select_candidates(modules: dict) -> list[dict]:
    """回傳分數最高的TOP_N_CANDIDATES檔,附上跨模組的完整原始數字。"""
    theme_rotation = modules.get("theme_rotation")
    if not theme_rotation:
        logger.warning("沒有theme_rotation_latest.json(M1/M2),無法選出候選股,請先跑theme_rotation_scan.py")
        return []

    buy_zone_by_code = {}
    if modules.get("buy_sell_zones"):
        for entries in modules["buy_sell_zones"]["themes"].values():
            for e in entries:
                buy_zone_by_code[e["code"]] = e

    trapped_by_code = {}
    if modules.get("trapped_pressure"):
        for entries in modules["trapped_pressure"]["themes"].values():
            for e in entries:
                trapped_by_code[e["code"]] = e

    news_by_code: dict[str, list[dict]] = {}
    if modules.get("news"):
        for n in modules["news"]["news"]:
            for matched in n["matched_stocks"]:
                for entries in theme_rotation["themes"].values():
                    for s in entries["stocks"]:
                        if s["name"] in matched or s["code"] in matched:
                            news_by_code.setdefault(s["code"], []).append(
                                {"title": n["title"], "direction": n["direction"]}
                            )

    scored = []
    for theme, data in theme_rotation["themes"].items():
        for s in data["stocks"]:
            code = s["code"]
            tags = list(s.get("tags", []))
            score = _score_stock(tags)

            zone_entry = buy_zone_by_code.get(code)
            if zone_entry and any("買進區間內" in t for t in zone_entry.get("tags", [])):
                score += BUY_ZONE_TAG_BONUS
                tags = tags + zone_entry["tags"]

            scored.append({
                "code": code,
                "name": s["name"],
                "theme": theme,
                "score": score,
                "rise_60d_pct": s.get("rise_60d_pct"),
                "pe": s.get("pe"),
                "dividend_yield": s.get("dividend_yield"),
                "revenue_yoy_pct": s.get("revenue_yoy_pct"),
                "flow_windows_value": s.get("flow_windows_value"),
                "flow_streak_days": s.get("flow_streak_days"),
                "tags": tags,
                "live_price": zone_entry.get("live_price") if zone_entry else None,  # M3盤中報價,才是真正的「現在股價」;buy_sell_zone.latest_close其實是昨收,命名容易誤會
                "buy_sell_zone": zone_entry.get("zone") if zone_entry else None,
                "trapped_pressure": trapped_by_code.get(code, {}).get("zone"),
                "related_news": news_by_code.get(code, []),
            })

    scored.sort(key=lambda r: r["score"], reverse=True)
    return [r for r in scored if r["score"] > 0][:TOP_N_CANDIDATES]


SYSTEM_PROMPT = """你是使用者的個人AI受惠股資金輪動助手。使用者的策略是「買黑不買紅」：
在已經確認資金/題材熱度的族群裡,找出還沒漲、位階低、基本面尚可的落後股,搭配法人資金流向
判斷進場時機。

嚴格規則(必須遵守)：
1. 以下提供給你的每一個數字(漲幅、本益比、殖利率、買賣超金額、買賣區間價位、套牢區價位)
   都已經由程式精確計算過，你只能原封不動引用，絕對不能自己重新計算或估算。
2. 不能編造任何沒有在資料中出現的消息、事件或數字。如果某個欄位是null或沒有提供,就不要提及。
3. 針對每一檔股票，用繁體中文寫3-5句話：先講「為什麼是候選」(引用具體標籤/數字)，
   再給一個明確但保守的操作建議(例如：可留意回檔到買進區間、或距離套牢區還有多少空間)。
   不要給「保證獲利」之類的話術，這只是輔助判斷,最終決定權在使用者。
4. 每一檔的敘述最後,用一行清楚列出你引用的關鍵數字(格式:「引用數字：60日漲幅X%、本益比X、...」),
   方便使用者自己核對。"""


def build_user_prompt(candidates: list[dict], earnings_calendar: dict | None = None) -> str:
    lines = []
    if earnings_calendar and earnings_calendar.get("upcoming_deadlines"):
        deadline_lines = "；".join(
            f"{d['label']}({d['period']})截止日{d['date']}，還有{d['days_until']}天"
            for d in earnings_calendar["upcoming_deadlines"]
        )
        lines.append(f"財報時程脈絡(法定申報截止日,非個股實際公布日預測)：{deadline_lines}")
        lines.append("如果某檔候選股即將進入財報空窗期結束(截止日快到了),可以在建議裡提醒使用者留意波動可能加大;不確定的話不要硬加這句。\n")
    lines.append("以下是今天篩出的候選股,附完整數字：\n")
    for c in candidates:
        lines.append(f"### {c['name']}({c['code']}) - {c['theme']}族群")
        lines.append(f"標籤：{' | '.join(c['tags']) if c['tags'] else '無'}")
        lines.append(f"60日漲幅：{c['rise_60d_pct']}%　本益比：{c['pe']}　股息殖利率：{c['dividend_yield']}%")
        lines.append(f"月營收年增：{c['revenue_yoy_pct']}%")
        lines.append(f"資金流向(元)：{c['flow_windows_value']}　連續買超天數：{c['flow_streak_days']}")
        if c["buy_sell_zone"]:
            z = c["buy_sell_zone"]
            lines.append(f"買進區間：{z['buy_zone_low']}~{z['buy_zone_high']}　停損參考：{z['stop_loss_price']}")
        if c["trapped_pressure"] and c["trapped_pressure"].get("trapped_zone_price"):
            tp = c["trapped_pressure"]
            lines.append(f"套牢區參考價：{tp['trapped_zone_price']}　距離：{tp['distance_pct']}%")
        if c["related_news"]:
            news_titles = "；".join(f"{n['title']}({n['direction']})" for n in c["related_news"][:3])
            lines.append(f"相關新聞：{news_titles}")
        lines.append("")
    return "\n".join(lines)


def call_gemini(candidates: list[dict], earnings_calendar: dict | None = None) -> str:
    from google import genai
    from google.genai import types

    api_key = _load_gemini_api_key()
    # SDK預設對503會重試很多次、拖很久(實測連續兩次都拖了6~9分鐘才放棄)。
    # 縮短重試次數/延遲,失敗就快點失敗,由使用者自己決定要不要手動再跑一次,
    # 不要一直卡在一次呼叫裡面。
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=30_000,  # 毫秒
            retry_options=types.HttpRetryOptions(attempts=2, initial_delay=2, max_delay=10),
        ),
    )
    user_prompt = build_user_prompt(candidates, earnings_calendar)

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user_prompt,
        config={"system_instruction": SYSTEM_PROMPT},
    )
    return response.text


def build_report() -> dict:
    modules = load_all_modules()
    candidates = select_candidates(modules)

    if not candidates:
        return {
            "generated_at": dt.datetime.now().isoformat(),
            "candidates": [],
            "ai_summary": "沒有符合條件的候選股(或M1/M2還沒跑過),無法產生建議。",
        }

    logger.info("挑出 %d 檔候選股,呼叫Gemini產生建議...", len(candidates))
    try:
        ai_summary = call_gemini(candidates, modules.get("earnings_calendar"))
    except Exception:  # noqa: BLE001
        logger.warning("Gemini呼叫失敗,回傳候選股清單但沒有AI敘述", exc_info=True)
        ai_summary = "(Gemini呼叫失敗,以下只有程式算出的候選股數字,沒有AI建議文字)"

    return {
        "generated_at": dt.datetime.now().isoformat(),
        "candidates": candidates,
        "ai_summary": ai_summary,
    }


def main() -> None:
    report = build_report()
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== AI合成買賣建議 {report['generated_at']} ===\n")
    print(f"候選股(依訊號強度排序,共{len(report['candidates'])}檔)：")
    for c in report["candidates"]:
        print(f"  {c['name']}({c['code']}) 分數{c['score']}  {c['theme']}")
    print(f"\n{report['ai_summary']}")
    print(f"\n完整結果已存至 {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
