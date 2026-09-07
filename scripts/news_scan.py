"""M8(個股新聞) —— 獨立模組,直接沿用新聞監控/的RSS抓取+關鍵字評分邏輯。

新聞監控/filter_rules.py的score_all()吃的watchlist格式是{族群名稱: [{code,name},...]},
剛好跟theme_rotation_scan.py的THEMES是同一種格式,不用轉換直接傳進去用,不重複造輪子。
只保留「有命中我們追蹤股票」的新聞,不是全市場新聞倒出來。
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
sys.path.insert(0, str(BASE_DIR))  # fetch_sources/filter_rules已vendor進scripts/,不再依賴新聞監控/

from fetch_sources import fetch_all  # noqa: E402
from filter_rules import score_all  # noqa: E402
from theme_rotation_scan import THEMES  # noqa: E402  單一清單來源,格式與watchlist相容

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("news_scan")

OUTPUT_PATH = DOCS_DIR / "news_scan_latest.json"


def build_report() -> dict:
    logger.info("抓取新聞來源...")
    news_items, hot_stocks = fetch_all()
    logger.info("抓到 %d 則新聞、%d 檔今日熱門股", len(news_items), len(hot_stocks))

    scored = score_all(news_items, THEMES, hot_stocks)
    relevant = [n for n in scored if n["matched_stocks"]]

    return {
        "generated_at": dt.datetime.now().isoformat(),
        "total_news_scanned": len(news_items),
        "relevant_count": len(relevant),
        "news": relevant,
    }


def print_report(report: dict) -> None:
    print(f"\n=== 個股新聞掃描 {report['generated_at']} ===")
    print(f"共掃描{report['total_news_scanned']}則新聞,{report['relevant_count']}則跟追蹤股票有關\n")

    for n in report["news"]:
        stocks_str = "、".join(n["matched_stocks"])
        kw_str = "、".join(n["matched_keywords"]) if n["matched_keywords"] else "-"
        print(f"[{n['direction']} 分數{n['score']}] {n['title']}")
        print(f"    相關個股:{stocks_str}  關鍵字:{kw_str}  來源:{n['source']}")
        print(f"    {n['link']}\n")


def main() -> None:
    report = build_report()
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_report(report)
    print(f"完整結果已存至 {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
