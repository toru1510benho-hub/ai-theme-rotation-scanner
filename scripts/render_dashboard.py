"""把M1~M9的JSON結果渲染成單一靜態HTML儀表板。沿用新聞監控/render_dashboard.py的配色系統
(跟台股監控儀表板.html、策略回測實驗誌.html同一套視覺語言,不重新發明)。

只負責「讀JSON、排版成HTML」，不重新計算任何數字——所有數字都是各模組腳本已經算好的。
任何一個模組的JSON不存在就整個區塊顯示「尚未執行」，不讓缺資料擋住其他區塊的呈現。
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).parent
DOCS_DIR = BASE_DIR.parent / "docs"

from news_style import STYLE  # noqa: E402  vendor自新聞監控/render_dashboard.py,改名避免跟本檔案同名自我匯入衝突

OUTPUT_PATH = DOCS_DIR / "index.html"

EXTRA_STYLE = """
.overview { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 40px; }
.stock-card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 20px; margin-bottom: 12px; box-shadow: var(--shadow); }
.stock-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
.stock-name { font-size: 15px; font-weight: 700; margin: 0; }
.stock-code { font-family: var(--mono); font-size: 12px; color: var(--text-muted); }
.stock-score { margin-left: auto; font-family: var(--mono); font-size: 16px; font-weight: 700; padding: 2px 10px; border-radius: 8px; }
.metric-row { display: flex; gap: 18px; flex-wrap: wrap; font-size: 12.5px; color: var(--text-secondary); margin-bottom: 8px; font-family: var(--mono); }
.metric-row b { color: var(--text-primary); font-weight: 700; }
.tag-list { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
.tag { font-size: 10.5px; padding: 3px 9px; border-radius: 999px; background: var(--surface-2); border: 1px solid var(--border); color: var(--text-secondary); white-space: nowrap; }
.tag-star { background: var(--gain-soft); color: var(--gain); border-color: transparent; font-weight: 700; }
.ai-narrative { font-size: 13px; color: var(--text-secondary); line-height: 1.7; white-space: pre-wrap; margin-top: 6px; padding-top: 10px; border-top: 1px dashed var(--border); }
.theme-rank-row { display: flex; align-items: center; gap: 12px; padding: 10px 16px; border-bottom: 1px solid var(--border); font-size: 13px; }
.theme-rank-row:last-child { border-bottom: none; }
.theme-rank-num { font-family: var(--mono); color: var(--text-muted); min-width: 22px; }
.theme-rank-name { font-weight: 700; flex: 1; }
.theme-rank-flow { font-family: var(--mono); font-variant-numeric: tabular-nums; }
.theme-group-title { font-size: 14px; font-weight: 700; margin: 28px 0 12px; padding-bottom: 6px; border-bottom: 1px solid var(--border); color: var(--text-primary); }
.section-note { font-size: 12px; color: var(--text-muted); margin-bottom: 14px; }

/* 使用者要求:固定暗色系(護眼) + 標題放大。放在STYLE後面,同specificity靠後蓋掉預設淺色, 不改動
   共用的 新聞監控/render_dashboard.py，只影響這支資金輪動儀表板。 */
:root {
  --bg: #0D1117; --surface: #161B22; --surface-2: #1C2128; --border: #30363D;
  --text-primary: #E6EDF3; --text-secondary: #9CA9B7; --text-muted: #6E7A87;
  --accent: #6C93E0; --accent-soft: #1F2937;
  --gain: #E0665A; --gain-soft: #3A211D;
  --loss: #22A870; --loss-soft: #142B21;
  --warn: #C08A2E; --warn-soft: #332510;
  --grid: #21262D;
  --shadow: 0 1px 2px rgba(0,0,0,0.5), 0 8px 24px rgba(0,0,0,0.4);
}
.topbar .title { font-size: 28px; font-weight: 800; letter-spacing: 0.01em; }

.filter-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 28px; padding: 14px 16px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); position: sticky; top: 58px; z-index: 15; }
.filter-label { font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.06em; margin-right: 4px; }
.filter-chip { font-size: 12px; padding: 5px 12px; border-radius: 999px; background: var(--surface-2); border: 1px solid var(--border); color: var(--text-secondary); cursor: pointer; user-select: none; transition: all 0.12s; white-space: nowrap; }
.filter-chip:hover { border-color: var(--accent); color: var(--text-primary); }
.filter-chip.active { background: var(--accent-soft); color: var(--accent); border-color: var(--accent); font-weight: 700; }
.filter-chip .count { opacity: 0.65; margin-left: 3px; }
.filter-clear { font-size: 11px; color: var(--text-muted); cursor: pointer; text-decoration: underline; margin-left: auto; }
.filter-clear:hover { color: var(--text-primary); }
.pill-warn { background: var(--warn-soft); color: var(--warn); }
"""

# 標籤分類規則(比對子字串,不是完全比對):因為很多標籤帶動態數字(例如「連續4日買超」「單日急殺-6.2%」),
# 沒辦法直接拿標籤全文當篩選項,要先歸類成固定的幾種篩選類別。這份規則是唯一的分類依據,
# Python算好類別後直接寫進 data-categories 屬性,JS只做純文字比對,不重複實作規則。
TAG_CATEGORIES = [
    ("underdog", "落後同業", lambda tags: any("落後同業" in t for t in tags)),
    ("cheap_pe", "本益比低", lambda tags: any("本益比低" in t for t in tags)),
    ("institutional_buy", "法人買超", lambda tags: any("買超" in t for t in tags)),
    ("big_drop", "單日急殺", lambda tags: any(t.startswith("單日急殺") for t in tags)),
    ("in_buy_zone", "買進區間內", lambda tags: any("買進區間內" in t for t in tags)),
    ("overheated", "慎防追高", lambda tags: any("慎防追高" in t for t in tags)),
    ("good_fundamentals", "基本面佳", lambda tags: any("基本面佳" in t for t in tags)),
    ("star", "★低位階+基本面佳", lambda tags: any("★" in t for t in tags)),
    ("stop_loss", "跌破停損", lambda tags: any("跌破停損" in t for t in tags)),
    ("trapped_near", "接近套牢區", lambda tags: any("接近套牢區" in t or "解套賣壓" in t for t in tags)),
]

FILTER_JS = """
<script>
(function () {
  var chips = document.querySelectorAll('.filter-chip[data-cat]');
  var clearBtn = document.querySelector('.filter-clear');
  var selected = new Set();

  function applyFilter() {
    document.querySelectorAll('[data-categories]').forEach(function (card) {
      var cats = (card.getAttribute('data-categories') || '').split(' ').filter(Boolean);
      var visible = selected.size === 0 || cats.some(function (c) { return selected.has(c); });
      card.style.display = visible ? '' : 'none';
    });
    document.querySelectorAll('.theme-group').forEach(function (group) {
      var anyVisible = Array.prototype.some.call(
        group.querySelectorAll('[data-categories]'),
        function (card) { return card.style.display !== 'none'; }
      );
      group.style.display = anyVisible ? '' : 'none';
    });
  }

  chips.forEach(function (chip) {
    chip.addEventListener('click', function () {
      var cat = chip.getAttribute('data-cat');
      if (selected.has(cat)) { selected.delete(cat); chip.classList.remove('active'); }
      else { selected.add(cat); chip.classList.add('active'); }
      applyFilter();
    });
  });

  if (clearBtn) {
    clearBtn.addEventListener('click', function () {
      selected.clear();
      chips.forEach(function (c) { c.classList.remove('active'); });
      applyFilter();
    });
  }
})();
</script>
"""


def _load(name: str) -> dict | None:
    path = DOCS_DIR / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _fmt_pct(v) -> str:
    return f"{v:+.1f}%" if isinstance(v, (int, float)) else "N/A"


def _fmt_num(v) -> str:
    if v is None:
        return "N/A"
    if isinstance(v, float):
        return f"{v:,.2f}" if abs(v) < 1000 else f"{v:,.0f}"
    return str(v)


def _pill_class_for_change(v) -> str:
    if not isinstance(v, (int, float)):
        return "pill-muted"
    return "pill-gain" if v > 0 else ("pill-loss" if v < 0 else "pill-muted")


def _tag_html(tag: str) -> str:
    cls = "tag tag-star" if tag.startswith("★") else "tag"
    return f'<span class="{cls}">{html.escape(tag)}</span>'


def _compute_categories(tags: list[str], counter: Counter) -> str:
    """回傳這張卡片命中的篩選類別(空白分隔,寫進data-categories屬性),同時累加全域計數
    (用來決定篩選列要顯示哪些按鈕、按鈕上要標幾檔)。"""
    matched = [key for key, _label, matcher in TAG_CATEGORIES if matcher(tags)]
    counter.update(matched)
    return " ".join(matched)


def render_filter_bar(counter: Counter) -> str:
    chips = "".join(
        f'<span class="filter-chip" data-cat="{key}">{html.escape(label)}<span class="count">{counter[key]}</span></span>'
        for key, label, _matcher in TAG_CATEGORIES
        if counter[key] > 0
    )
    if not chips:
        return ""
    return f"""
    <div class="filter-bar">
      <span class="filter-label">篩選標籤</span>
      {chips}
      <span class="filter-clear">清除篩選</span>
    </div>
    """


def render_overview(modules: dict) -> str:
    theme_rotation = modules.get("theme_rotation")
    ai = modules.get("ai_synthesis")
    events = modules.get("event_calendar")
    news = modules.get("news")

    cards = []
    if theme_rotation and theme_rotation.get("theme_ranking_by_money_flow"):
        top_theme = theme_rotation["theme_ranking_by_money_flow"][0]
        cards.append(("資金流向最強族群", top_theme))
    if ai and ai.get("candidates"):
        cards.append(("今日候選股數", f"{len(ai['candidates'])} 檔"))
    if events and events.get("upcoming_fomc"):
        m = events["upcoming_fomc"][0]
        cards.append(("下次FOMC會議", f"{m['date']}（{m['days_until']}天後）"))
    if news:
        cards.append(("相關新聞則數", f"{news.get('relevant_count', 0)} 則"))

    if not cards:
        return ""
    items = "".join(
        f'<div class="ov-card"><div class="eyebrow">{html.escape(label)}</div>'
        f'<div class="headline" style="font-size:18px;">{html.escape(str(value))}</div></div>'
        for label, value in cards
    )
    return f'<div class="overview">{items}</div>'


def _parse_narrative_by_stock(narrative: str, candidates: list[dict]) -> dict[str, str]:
    """把Gemini寫的敘述文字(用"### 股票名(代碼) - 族群"當每檔的段落標題)拆回對應每一檔候選股。
    這是必要的:如果整段敘述當成一個不會被篩選的大區塊放在候選股清單最後面，篩選標籤只會
    藏掉上面的個股卡片，下面這段還是會把每一檔的文字原封不動印出來，看起來就像篩選沒作用。
    拆開後每檔的敘述段落跟在自己的卡片裡，篩選時才會一起被藏起來/顯示出來。
    """
    chunks: dict[str, str] = {}
    if not narrative:
        return chunks
    parts = re.split(r"(?m)^###\s+", narrative)
    for part in parts[1:]:  # parts[0]是第一個###之前的開場白,不屬於任何一檔
        heading, _, body = part.partition("\n")
        for c in candidates:
            if c["code"] in heading or c["name"] in heading:
                chunks[c["code"]] = body.strip()
                break
    return chunks


def render_ai_synthesis(ai: dict | None, counter: Counter) -> str:
    if not ai or not ai.get("candidates"):
        return '<section class="track" id="ai"><h2>M9 · AI合成建議</h2><div class="empty-note">尚未執行 ai_synthesis.py，或當天沒有符合條件的候選股。</div></section>'

    narrative_by_code = _parse_narrative_by_stock(ai.get("ai_summary", ""), ai["candidates"])

    cards = []
    for c in ai["candidates"]:
        tags = c.get("tags", [])
        tags_html = "".join(_tag_html(t) for t in tags)
        categories = _compute_categories(tags, counter)
        zone = c.get("buy_sell_zone")
        zone_html = ""
        if zone:
            zone_html = (
                f'<div class="metric-row">'
                f'<span>買進區間 <b>{zone["buy_zone_low"]}~{zone["buy_zone_high"]}</b></span>'
                f'<span>停損參考 <b>{zone["stop_loss_price"]}</b></span>'
                f'</div>'
            )
        own_narrative = narrative_by_code.get(c["code"])
        narrative_line_html = f'<div class="ai-narrative">{html.escape(own_narrative)}</div>' if own_narrative else ""
        cards.append(f"""
        <div class="stock-card" data-categories="{categories}">
          <div class="stock-head">
            <h3 class="stock-name">{html.escape(c['name'])}</h3>
            <span class="stock-code">{html.escape(c['code'])} · {html.escape(c['theme'])}</span>
            <span class="stock-score pill-accent">分數 {c['score']}</span>
          </div>
          <div class="tag-list">{tags_html}</div>
          <div class="metric-row">
            <span>{'目前股價' if c.get('live_price') is not None else '昨收'} <b>{_fmt_num(c.get('live_price')) if c.get('live_price') is not None else (_fmt_num(zone.get('latest_close')) if zone else 'N/A')}</b></span>
            <span>60日漲幅 <b>{_fmt_pct(c.get('rise_60d_pct'))}</b></span>
            <span>本益比 <b>{_fmt_num(c.get('pe'))}</b></span>
            <span>殖利率 <b>{_fmt_num(c.get('dividend_yield'))}%</b></span>
            <span>營收年增 <b>{_fmt_pct(c.get('revenue_yoy_pct'))}</b></span>
          </div>
          {zone_html}
          {narrative_line_html}
        </div>
        """)

    # 保底:萬一Gemini這次沒照"### 標題"格式寫(格式跑掉配對不到任何一檔),不要整段文字憑空消失,
    # 顯示在候選股清單最後面(此區塊不受篩選標籤影響,因為它不屬於特定一檔股票)。
    fallback_html = ""
    if ai.get("ai_summary") and not narrative_by_code:
        fallback_html = f'<div class="stock-card ai-narrative">{html.escape(ai["ai_summary"])}</div>'

    return f"""
    <section class="track" id="ai">
      <h2>M9 · AI合成建議(候選股依訊號強度排序)</h2>
      <div class="disclaimer">分數是寫死的規則計算(落後同業/低本益比/連續買超/基本面/買進區間疊加),不是AI主觀判斷；
      AI只負責把這些數字組織成中文敘述,不能自己編數字。僅供輔助研判,非投資建議。</div>
      {''.join(cards)}
      {fallback_html}
    </section>
    """


def render_theme_ranking(theme_rotation: dict | None) -> str:
    if not theme_rotation:
        return '<section class="track" id="flow"><h2>M1 · 族群資金流向排名</h2><div class="empty-note">尚未執行 theme_rotation_scan.py。</div></section>'

    rows = []
    for i, name in enumerate(theme_rotation["theme_ranking_by_money_flow"], 1):
        data = theme_rotation["themes"][name]
        flow = data.get("total_money_flow_value")
        momentum = data.get("avg_momentum_60d_pct")
        flow_str = f"{flow:+,.0f} 元" if flow is not None else "資料不足"
        momentum_str = _fmt_pct(momentum)
        pill = _pill_class_for_change(flow)
        rows.append(f"""
        <div class="theme-rank-row">
          <span class="theme-rank-num">#{i}</span>
          <span class="theme-rank-name">{html.escape(name)}</span>
          <span class="pill {pill}">{momentum_str} (60日)</span>
          <span class="theme-rank-flow">{flow_str}</span>
        </div>
        """)

    return f"""
    <section class="track" id="flow">
      <h2>M1 · 族群資金流向排名(近5日累積買賣超金額估算,由高到低)</h2>
      <div class="stock-card" style="padding:4px 4px;">{''.join(rows)}</div>
    </section>
    """


def render_stock_groups(
    theme_rotation: dict | None, buy_zones: dict | None, trapped: dict | None, counter: Counter
) -> str:
    if not theme_rotation:
        return ""

    zone_by_code, trapped_by_code = {}, {}
    if buy_zones:
        for entries in buy_zones["themes"].values():
            for e in entries:
                zone_by_code[e["code"]] = e
    if trapped:
        for entries in trapped["themes"].values():
            for e in entries:
                trapped_by_code[e["code"]] = e

    sections = []
    for theme, data in theme_rotation["themes"].items():
        cards = []
        for s in data["stocks"]:
            code = s["code"]
            tags = list(s.get("tags", []))
            zone_e = zone_by_code.get(code)
            if zone_e:
                tags += zone_e.get("tags", [])
            trapped_e = trapped_by_code.get(code)
            if trapped_e:
                tags += trapped_e.get("tags", [])

            tags_html = "".join(_tag_html(t) for t in tags) or '<span class="tag">-</span>'
            categories = _compute_categories(tags, counter)

            zone_line = ""
            if zone_e and zone_e.get("zone"):
                z = zone_e["zone"]
                zone_line = f'<span>買進區間 <b>{z["buy_zone_low"]}~{z["buy_zone_high"]}</b></span><span>停損 <b>{z["stop_loss_price"]}</b></span>'

            cards.append(f"""
            <div class="stock-card" data-categories="{categories}">
              <div class="stock-head">
                <h3 class="stock-name">{html.escape(s['name'])}</h3>
                <span class="stock-code">{html.escape(code)} · {html.escape(s.get('market',''))}</span>
                <span class="pill {_pill_class_for_change(s.get('rise_60d_pct'))}">60日 {_fmt_pct(s.get('rise_60d_pct'))}</span>
              </div>
              <div class="tag-list">{tags_html}</div>
              <div class="metric-row">
                <span>本益比 <b>{_fmt_num(s.get('pe'))}</b></span>
                <span>殖利率 <b>{_fmt_num(s.get('dividend_yield'))}%</b></span>
                {zone_line}
              </div>
            </div>
            """)
        sections.append(
            f'<div class="theme-group"><div class="theme-group-title">{html.escape(theme)}</div>{"".join(cards)}</div>'
        )

    return f"""
    <section class="track" id="stocks">
      <h2>M1+M2+M4+M5 · 個股總覽(依族群分類)</h2>
      {''.join(sections)}
    </section>
    """


def render_intraday(intraday: dict | None) -> str:
    if not intraday:
        return '<section class="track" id="intraday"><h2>M3+M7 · 盤中即時報價 / 美股連動</h2><div class="empty-note">尚未執行 intraday_quotes_scan.py。</div></section>'

    us_rows = "".join(
        f'<div class="theme-rank-row"><span class="theme-rank-name">{html.escape(q["name"])}({sym})</span>'
        f'<span class="pill {_pill_class_for_change(q.get("change_pct"))}">{_fmt_pct(q.get("change_pct"))}</span>'
        f'<span class="theme-rank-flow">{_fmt_num(q.get("price"))}</span></div>'
        for sym, q in intraday["us_benchmarks"].items()
    )

    theme_rows = "".join(
        f'<div class="theme-rank-row"><span class="theme-rank-num">#{i+1}</span>'
        f'<span class="theme-rank-name">{html.escape(name)}</span>'
        f'<span class="pill {_pill_class_for_change(intraday["themes"][name]["avg_change_pct"])}">'
        f'{_fmt_pct(intraday["themes"][name]["avg_change_pct"])}</span></div>'
        for i, name in enumerate(intraday["theme_ranking_by_change"])
    )

    return f"""
    <section class="track" id="intraday">
      <h2>M3+M7 · 盤中即時報價輪動 / 美股連動</h2>
      <div class="theme-group-title">美股連動(隔夜/即時)</div>
      <div class="stock-card" style="padding:4px 4px;">{us_rows}</div>
      <div class="theme-group-title">族群盤中漲跌幅排名</div>
      <div class="stock-card" style="padding:4px 4px;">{theme_rows}</div>
    </section>
    """


def render_events(events: dict | None) -> str:
    if not events:
        return '<section class="track" id="events"><h2>M6 · 事件日曆</h2><div class="empty-note">尚未執行 event_calendar_scan.py。</div></section>'

    fomc_html = "".join(
        f'<div class="theme-rank-row"><span class="theme-rank-name">FOMC會議</span><span class="pill pill-accent">{m["date"]}（{m["days_until"]}天後）</span></div>'
        for m in events["upcoming_fomc"]
    ) or '<div class="empty-note">30天內沒有排定的FOMC會議</div>'

    recent = events.get("recent_company_events_14d", [])[:20]
    events_html = "".join(
        f'<div class="stock-card" style="padding:10px 16px;">'
        f'<div class="metric-row"><b>{html.escape(e["announce_date"])}</b> {html.escape(e["name"])}({html.escape(e["code"])})'
        f'{" <span class=\'tag tag-star\'>法說會相關</span>" if e["is_conference"] else ""}</div>'
        f'<div class="section-note">{html.escape(e["subject"][:80])}</div></div>'
        for e in recent
    ) or '<div class="empty-note">尚無累積紀錄,這個模組需要每天跑才會累積出歷史</div>'

    return f"""
    <section class="track" id="events">
      <h2>M6 · 事件日曆</h2>
      <div class="theme-group-title">未來30天FOMC會議</div>
      <div class="stock-card" style="padding:4px 4px;">{fomc_html}</div>
      <div class="theme-group-title">近14天個股重大訊息(僅上市公司)</div>
      {events_html}
    </section>
    """


def render_news(news: dict | None) -> str:
    if not news or not news.get("news"):
        return '<section class="track" id="news"><h2>M8 · 相關新聞</h2><div class="empty-note">尚未執行 news_scan.py，或目前沒有相關新聞。</div></section>'

    direction_pill = {"利多": "pill-gain", "利空": "pill-loss", "中性關注": "pill-accent"}
    items = "".join(f"""
    <div class="news-card">
      <div class="news-head">
        <span class="pill {direction_pill.get(n['direction'], 'pill-muted')}">{html.escape(n['direction'])}</span>
        <span class="news-title"><a href="{html.escape(n['link'])}" target="_blank">{html.escape(n['title'])}</a></span>
      </div>
      <div class="news-meta">{html.escape(n['source'])}</div>
      <div class="news-stocks">相關個股：<b>{html.escape('、'.join(n['matched_stocks']))}</b></div>
    </div>
    """ for n in news["news"])

    return f"""
    <section class="track" id="news">
      <h2>M8 · 相關新聞(共{news['relevant_count']}則)</h2>
      {items}
    </section>
    """


def render_earnings_calendar(earnings: dict | None) -> str:
    if not earnings:
        return '<section class="track" id="earnings"><h2>M10 · 財報時程規劃</h2><div class="empty-note">尚未執行 earnings_calendar_scan.py。</div></section>'

    rows = "".join(
        f'<div class="theme-rank-row"><span class="theme-rank-name">{html.escape(d["label"])}（{html.escape(d["period"])}）</span>'
        f'<span class="pill pill-accent">{d["date"]}</span>'
        f'<span class="theme-rank-flow">還有 {d["days_until"]} 天</span></div>'
        for d in earnings["upcoming_deadlines"]
    )

    return f"""
    <section class="track" id="earnings">
      <h2>M10 · 財報時程規劃</h2>
      <div class="disclaimer">{html.escape(earnings['note'])} 適用範圍：本站追蹤的{len(earnings['stocks'])}檔AI受惠股
      (CPO/矽光子、記憶體、晶圓代工、被動元件、AI伺服器組裝、散熱、ABF載板/PCB、電源供應，全為一般產業，非金融業)。</div>
      <div class="stock-card" style="padding:4px 4px;">{rows}</div>
      <div class="section-note" style="margin-top:14px;">個股實際公布日通常會早於法定截止日，接近截止日時可留意公司公告；
      這份時程也會提供給M9 AI合成建議參考，財報空窗期即將結束時波動可能加大。</div>
    </section>
    """


def render_earnings_reader(earnings: dict | None) -> str:
    if not earnings:
        return '<section class="track" id="earnings-reader"><h2>財報數字速覽</h2><div class="empty-note">尚未執行 earnings_reader_scan.py。</div></section>'

    announced_count = earnings.get("recently_announced_count", 0)
    status_note = (
        f"最近{earnings['announcement_lookback_days']}天內有{announced_count}檔公告新財報"
        if announced_count
        else f"最近{earnings['announcement_lookback_days']}天內沒有追蹤股票公告新財報(財報空窗期,不是沒抓到資料)"
    )

    rows = []
    for c in earnings["companies"]:
        cur = c["current"]
        qoq = f"{c['revenue_qoq_pct']:+.1f}%" if c.get("revenue_qoq_pct") is not None else "尚無比較基準"
        tag = '<span class="tag tag-star">最近公告</span>' if c["recently_announced"] else ""
        rows.append(f"""
        <div class="stock-card">
          <div class="stock-head">
            <h3 class="stock-name">{html.escape(c['name'])}</h3>
            <span class="stock-code">{html.escape(c['code'])} · {html.escape(c['theme'])}</span>
            {tag}
          </div>
          <div class="metric-row">
            <span>{cur['year']}年第{cur['quarter']}季營收 <b>{cur['revenue']:,.0f}</b>(千元)</span>
            <span>毛利率 <b>{cur['gross_margin_pct']}%</b></span>
            <span>營業利益率 <b>{cur['operating_margin_pct']}%</b></span>
            <span>EPS <b>{cur['eps']}</b></span>
            <span>季增率 <b>{qoq}</b></span>
          </div>
        </div>
        """)

    ai_html = ""
    if earnings.get("ai_summary"):
        ai_html = f'<div class="stock-card ai-narrative" style="margin-top:16px;">{html.escape(earnings["ai_summary"])}</div>'

    return f"""
    <section class="track" id="earnings-reader">
      <h2>財報數字速覽</h2>
      <div class="disclaimer">數字來自證交所/櫃買中心官方當季合併損益表，程式直接算出毛利率/營業利益率/季增率，
      不是逐股票讀財報全文，暫時做不到管理層語氣分析、附注挖掘這種深度精讀。{status_note}。</div>
      {ai_html}
      {''.join(rows)}
    </section>
    """


def render_key_dates(key_dates: dict | None) -> str:
    if not key_dates:
        return '<section class="track" id="key-dates"><h2>關鍵日期總覽</h2><div class="empty-note">尚未執行 key_dates_scan.py。</div></section>'

    type_pill = {"財報截止日": "pill-accent", "總經事件": "pill-warn", "公司公告": "pill-muted", "除權息": "pill-gain"}
    cal_rows = "".join(
        f'<div class="theme-rank-row"><span class="pill {type_pill.get(e["type"], "pill-muted")}">{html.escape(e["type"])}</span>'
        f'<span class="theme-rank-name">{html.escape(e["title"])}</span>'
        f'<span class="theme-rank-flow">{e["date"]}</span></div>'
        for e in key_dates["calendar"][:30]
    )

    stats_rows = []
    for s in key_dates["earnings_window_stats"]:
        st = s["stats"]
        if st is None:
            continue
        pill = _pill_class_for_change(st["avg_window_return_pct"])
        stats_rows.append(
            f'<div class="theme-rank-row"><span class="theme-rank-name">{html.escape(s["name"])}({s["code"]})</span>'
            f'<span class="pill {pill}">平均{st["avg_window_return_pct"]:+.2f}%</span>'
            f'<span class="theme-rank-flow">波動度{st["avg_daily_volatility_pct"]}%　樣本{st["sample_size"]}次</span></div>'
        )
    stats_rows.sort()  # 按股票名稱字串排序,方便查找(不是按數值排序,避免看起來像是排名推薦)

    return f"""
    <section class="track" id="key-dates">
      <h2>關鍵日期總覽</h2>
      <div class="disclaimer">整合財報法定截止日、公司重大公告/法說會、美國FOMC會議、除權息預告。
      除權息預告表只涵蓋官方已確定日期的近期個股，不是全年度完整清單。</div>
      <div class="theme-group-title">近期關鍵日期(由近到遠)</div>
      <div class="stock-card" style="padding:4px 4px;">{cal_rows}</div>

      <div class="theme-group-title">財報窗口歷史股價統計</div>
      <div class="disclaimer">用過去實際發生的股價資料，算出「過去在法定財報截止日前後{key_dates['earnings_window_stats'][0]['stats']['window_days_each_side'] if key_dates['earnings_window_stats'] and key_dates['earnings_window_stats'][0].get('stats') else 5}個交易日」股價平均怎麼動——
      這是歷史統計傾向，不是對未來的預測，也沒有排除同期間大盤/族群整體漲跌趨勢的影響(例如AI族群這幾年整體上漲，會讓統計數字偏正)，
      樣本數(過去有幾次窗口可統計)少於3次的股票不顯示。</div>
      <div class="stock-card" style="padding:4px 4px;">{''.join(stats_rows)}</div>
    </section>
    """


def render() -> str:
    modules = {
        "theme_rotation": _load("theme_rotation_latest.json"),
        "intraday": _load("intraday_quotes_latest.json"),
        "buy_zones": _load("buy_sell_zones_latest.json"),
        "trapped": _load("trapped_pressure_latest.json"),
        "event_calendar": _load("event_calendar_latest.json"),
        "earnings_calendar": _load("earnings_calendar_latest.json"),
        "earnings_reader": _load("earnings_reader_latest.json"),
        "key_dates": _load("key_dates_latest.json"),
        "news": _load("news_scan_latest.json"),
        "ai_synthesis": _load("ai_synthesis_latest.json"),
    }

    now_str = dt.datetime.now().strftime("%Y/%m/%d %H:%M")
    category_counter: Counter = Counter()
    overview_html = render_overview(modules)
    # 先產出兩個含卡片的區塊(過程中把類別計數累加進category_counter),篩選列要等計數統計完才能畫
    ai_html = render_ai_synthesis(modules["ai_synthesis"], category_counter)
    stock_groups_html = render_stock_groups(
        modules["theme_rotation"], modules["buy_zones"], modules["trapped"], category_counter
    )
    filter_bar_html = render_filter_bar(category_counter)
    flow_ranking_html = render_theme_ranking(modules["theme_rotation"])
    key_dates_html = render_key_dates(modules["key_dates"])
    intraday_html = render_intraday(modules["intraday"])
    events_html = render_events(modules["event_calendar"])
    earnings_html = render_earnings_calendar(modules["earnings_calendar"])
    earnings_reader_html = render_earnings_reader(modules["earnings_reader"])
    news_html = render_news(modules["news"])

    return f"""<meta charset="utf-8">
<title>AI受惠股資金輪動戰情室</title>
<style>{STYLE}{EXTRA_STYLE}</style>
<div class="page">
  <div class="topbar">
    <span class="title">AI受惠股資金輪動戰情室</span>
    <nav style="display:flex; gap:16px; flex-wrap:wrap;">
      <a class="navlink" href="#ai" style="font-size:12px; color:var(--text-secondary); text-decoration:none; letter-spacing:0.04em; text-transform:uppercase;">AI建議</a>
      <a class="navlink" href="#flow" style="font-size:12px; color:var(--text-secondary); text-decoration:none; letter-spacing:0.04em; text-transform:uppercase;">資金流向</a>
      <a class="navlink" href="#key-dates" style="font-size:12px; color:var(--text-secondary); text-decoration:none; letter-spacing:0.04em; text-transform:uppercase;">關鍵日期</a>
      <a class="navlink" href="#stocks" style="font-size:12px; color:var(--text-secondary); text-decoration:none; letter-spacing:0.04em; text-transform:uppercase;">個股總覽</a>
      <a class="navlink" href="#intraday" style="font-size:12px; color:var(--text-secondary); text-decoration:none; letter-spacing:0.04em; text-transform:uppercase;">盤中/美股</a>
      <a class="navlink" href="#events" style="font-size:12px; color:var(--text-secondary); text-decoration:none; letter-spacing:0.04em; text-transform:uppercase;">事件日曆</a>
      <a class="navlink" href="#earnings" style="font-size:12px; color:var(--text-secondary); text-decoration:none; letter-spacing:0.04em; text-transform:uppercase;">財報時程</a>
      <a class="navlink" href="#earnings-reader" style="font-size:12px; color:var(--text-secondary); text-decoration:none; letter-spacing:0.04em; text-transform:uppercase;">財報數字</a>
      <a class="navlink" href="#news" style="font-size:12px; color:var(--text-secondary); text-decoration:none; letter-spacing:0.04em; text-transform:uppercase;">新聞</a>
    </nav>
    <span class="updated">最後更新 {now_str}</span>
  </div>
  <div class="wrap">
    {filter_bar_html}
    {overview_html}
    {ai_html}
    {flow_ranking_html}
    {key_dates_html}
    {stock_groups_html}
    {intraday_html}
    {events_html}
    {earnings_html}
    {earnings_reader_html}
    {news_html}
  </div>
  <footer>買黑不買紅策略輔助工具。資料來源:證交所(STOCK_DAY/T86/月營收)、櫃買中心、yfinance(Yahoo Finance)、Gemini AI。
  所有數字皆由程式精算,AI僅負責文字組織，僅供輔助研判，非投資建議。</footer>
</div>
{FILTER_JS}
"""


def main() -> None:
    html_content = render()
    OUTPUT_PATH.write_text(html_content, encoding="utf-8")
    print(f"儀表板已產出：{OUTPUT_PATH}")


if __name__ == "__main__":
    main()
