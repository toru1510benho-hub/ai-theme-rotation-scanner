"""M10(財報時程規劃) —— 獨立模組,純規則計算,不用AI、不用網路爬蟲。

台灣上市櫃公司(一般產業,不含金融業)財報申報有固定的法定截止日,規定在「證券發行人財務報告
編製準則」:
  Q1季報 5/15　Q2半年報 8/14　Q3季報 11/14　年報(隔年)3/31
(來源:2026-09-02用WebSearch核對過多篇財經媒體報導,一致確認這四個日期，本模組的THEMES
清單裡38檔股票全是電子/科技製造業,不是金融業,適用這組期限。)

這是「法定截止日」不是「個股實際公布日預測」——大部分公司會在截止日之前提前公布,這裡給的是
確定、可回溯查證的規則性事實，不是猜測。用途：讓使用者知道「最近一次財報空窗期還有多久」，
也讓M9 AI合成建議在寫建議時能參考「快到財報了,波動可能加大」這類脈絡。
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).parent
DOCS_DIR = BASE_DIR.parent / "docs"
sys.path.insert(0, str(BASE_DIR))
from theme_rotation_scan import THEMES  # noqa: E402  單一清單來源,不重複維護

OUTPUT_PATH = DOCS_DIR / "earnings_calendar_latest.json"

# (月, 日, 標籤, 對應報告期間) —— 一般產業(非金融業)法定申報截止日,一年四個
STATUTORY_DEADLINES = [
    (3, 31, "年報", "上一年度年報"),
    (5, 15, "Q1季報", "當年度第1季"),
    (8, 14, "Q2半年報", "當年度上半年"),
    (11, 14, "Q3季報", "當年度第3季"),
]


def next_deadlines(today: dt.date, count: int = 2) -> list[dict]:
    """回傳未來count個法定截止日(由近到遠),年報的「上一年度」在跨年時要正確處理。"""
    results = []
    year = today.year
    # 往後找,最多找兩年份(8個候選)確保湊得滿count個
    candidates = []
    for y in (year, year + 1):
        for month, day, label, period_desc in STATUTORY_DEADLINES:
            deadline_date = dt.date(y, month, day)
            period_year = y - 1 if label == "年報" else y
            candidates.append((deadline_date, label, period_desc.replace("上一年度", str(period_year)).replace("當年度", str(period_year))))

    candidates.sort(key=lambda c: c[0])
    for deadline_date, label, period_desc in candidates:
        if deadline_date >= today:
            results.append({
                "date": deadline_date.isoformat(),
                "label": label,
                "period": period_desc,
                "days_until": (deadline_date - today).days,
            })
        if len(results) >= count:
            break
    return results


def build_report() -> dict:
    today = dt.date.today()
    upcoming = next_deadlines(today, count=2)

    all_codes = [(theme, s) for theme, stocks in THEMES.items() for s in stocks]
    stocks = [
        {"code": s["code"], "name": s["name"], "theme": theme, "market": s["market"]}
        for theme, s in all_codes
    ]

    return {
        "generated_at": dt.datetime.now().isoformat(),
        "note": "法定申報截止日(一般產業,不含金融業),不是個股實際公布日預測——多數公司會提前公布。",
        "upcoming_deadlines": upcoming,
        "stocks": stocks,
    }


def print_report(report: dict) -> None:
    print(f"\n=== 財報時程規劃 {report['generated_at']} ===")
    print(f"({report['note']})\n")
    for d in report["upcoming_deadlines"]:
        print(f"  {d['label']}({d['period']})　截止日 {d['date']}　還有 {d['days_until']} 天")
    print(f"\n適用範圍：{len(report['stocks'])} 檔股票(全部AI受惠股清單,一般產業適用同一組期限)")


def main() -> None:
    report = build_report()
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_report(report)
    print(f"\n完整結果已存至 {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
