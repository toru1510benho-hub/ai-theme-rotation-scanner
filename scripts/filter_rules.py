"""規則式重要性評分:關鍵字命中 + watchlist/動態熱門股比對 -> 重要性分數與利多/利空方向。

這是粗顆粒的關鍵字判讀,目標是「不漏掉明顯重大訊息」而非精準分類。
"""
from __future__ import annotations

# 關鍵字 -> 權重。權重越高代表對股價影響越顯著。
POSITIVE_KEYWORDS = {
    "調高目標價": 5, "上修目標價": 5, "喊上": 4, "上修財測": 5, "上調財測": 5,
    "營收創新高": 4, "獲利創新高": 4, "年增": 2, "月增": 2, "法說會": 3,
    "外資買超": 3, "外資大買": 4, "董事會通過配息": 4, "配息": 2, "現金股利": 2,
    "調升評等": 4, "買進評等": 3, "轉單": 3, "訂單能見度": 3, "供不應求": 3,
    "漲停": 4, "庫藏股": 3, "增資": 2, "擴大投資": 2, "新產能": 2,
}

NEGATIVE_KEYWORDS = {
    "調降目標價": 5, "下修目標價": 5, "下修財測": 5, "財務危機": 6, "跳票": 6,
    "下市": 6, "重大違約": 6, "重大虧損": 5, "虧損": 3, "外資賣超": 3,
    "外資大賣": 4, "調降評等": 4, "賣出評等": 3, "跌停": 4, "裁員": 3,
    "訴訟": 2, "存貨過高": 2, "訂單能見度不佳": 4, "砍單": 4, "延遲出貨": 3,
}

NEUTRAL_WATCH_KEYWORDS = {
    "財報公布": 2, "除權息": 2, "股東會": 1, "併購": 3, "合併": 2, "重訊": 3,
    "法人說明會": 2, "股利分配": 2, "增減資": 2,
}

# 綜合分數達此門檻標記為「重要」
IMPORTANCE_THRESHOLD = 4
# 命中 watchlist 個股或今日熱門股額外加權
WATCHLIST_BONUS = 2
HOT_STOCK_BONUS = 3


def _load_watchlist_index(watchlist: dict) -> dict[str, str]:
    """把 watchlist.json 攤平成 {代碼或名稱: 族群名稱} 方便比對。"""
    index = {}
    for group_name, stocks in watchlist.items():
        if group_name.startswith("_"):
            continue
        for stock in stocks:
            index[stock["code"]] = group_name
            index[stock["name"]] = group_name
    return index


def score_news_item(item: dict, watchlist_index: dict[str, str], hot_stock_index: dict[str, dict]) -> dict:
    """對單一新聞項目評分,回傳附加了 score / direction / matched_keywords / matched_stocks 的新 dict。"""
    text = f"{item.get('title', '')} {item.get('summary', '')}"

    score = 0
    matched_keywords = []
    direction_score = 0  # 正值偏利多,負值偏利空

    for kw, weight in POSITIVE_KEYWORDS.items():
        if kw in text:
            score += weight
            direction_score += weight
            matched_keywords.append(kw)
    for kw, weight in NEGATIVE_KEYWORDS.items():
        if kw in text:
            score += weight
            direction_score -= weight
            matched_keywords.append(kw)
    for kw, weight in NEUTRAL_WATCH_KEYWORDS.items():
        if kw in text:
            score += weight
            matched_keywords.append(kw)

    matched_stocks = set()
    for token, group_name in watchlist_index.items():
        if token and token in text:
            matched_stocks.add(f"{token}({group_name})")
            score += WATCHLIST_BONUS

    for code, info in hot_stock_index.items():
        if code in text or info["name"] in text:
            matched_stocks.add(f"{info['name']}({code})‧今日熱門")
            score += HOT_STOCK_BONUS

    if direction_score > 0:
        direction = "利多"
    elif direction_score < 0:
        direction = "利空"
    else:
        direction = "中性關注"

    result = dict(item)
    result.update(
        {
            "score": score,
            "direction": direction,
            "is_important": score >= IMPORTANCE_THRESHOLD,
            "matched_keywords": matched_keywords,
            "matched_stocks": sorted(matched_stocks),
        }
    )
    return result


def score_all(news_items: list[dict], watchlist: dict, hot_stocks: list[dict]) -> list[dict]:
    watchlist_index = _load_watchlist_index(watchlist)
    hot_stock_index = {s["code"]: s for s in hot_stocks}

    scored = [score_news_item(item, watchlist_index, hot_stock_index) for item in news_items]
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored
