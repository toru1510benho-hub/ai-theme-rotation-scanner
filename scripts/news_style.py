"""把評分後的新聞清單渲染成靜態 HTML 儀表板,沿用 策略回測實驗誌.html 的配色與卡片風格。"""
from __future__ import annotations

import datetime as dt
import html

STYLE = """
:root {
  --bg: #EEF1F4; --surface: #FFFFFF; --surface-2: #F4F6F9; --border: #DCE2E9;
  --text-primary: #16202B; --text-secondary: #47576B; --text-muted: #7C8898;
  --accent: #2A4FA0; --accent-soft: #E4EAFA;
  --gain: #C1392B; --gain-soft: #F8E4E1;
  --loss: #1E8F5B; --loss-soft: #DFF1E7;
  --warn: #A6720F; --warn-soft: #F6E8CE;
  --grid: #E1E7ED;
  --shadow: 0 1px 2px rgba(20,30,45,0.07), 0 6px 16px rgba(20,30,45,0.06);
  --radius: 12px;
  --mono: 'Cascadia Code', Consolas, 'SF Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
  --sans: 'Segoe UI', 'Microsoft JhengHei', -apple-system, BlinkMacSystemFont, sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #12171C; --surface: #182029; --surface-2: #1D2731; --border: #2A3542;
    --text-primary: #E7ECF2; --text-secondary: #AAB5C3; --text-muted: #71808F;
    --accent: #6C93E0; --accent-soft: #22314F;
    --gain: #E0665A; --gain-soft: #38221F;
    --loss: #1F9A68; --loss-soft: #163229;
    --warn: #B87F20; --warn-soft: #382A12;
    --grid: #263140;
    --shadow: 0 1px 2px rgba(0,0,0,0.4), 0 6px 20px rgba(0,0,0,0.35);
  }
}
:root[data-theme="dark"] {
  --bg: #12171C; --surface: #182029; --surface-2: #1D2731; --border: #2A3542;
  --text-primary: #E7ECF2; --text-secondary: #AAB5C3; --text-muted: #71808F;
  --accent: #6C93E0; --accent-soft: #22314F;
  --gain: #E0665A; --gain-soft: #38221F;
  --loss: #1F9A68; --loss-soft: #163229;
  --warn: #B87F20; --warn-soft: #382A12;
  --grid: #263140;
  --shadow: 0 1px 2px rgba(0,0,0,0.4), 0 6px 20px rgba(0,0,0,0.35);
}
:root[data-theme="light"] {
  --bg: #EEF1F4; --surface: #FFFFFF; --surface-2: #F4F6F9; --border: #DCE2E9;
  --text-primary: #16202B; --text-secondary: #47576B; --text-muted: #7C8898;
  --accent: #2A4FA0; --accent-soft: #E4EAFA;
  --gain: #C1392B; --gain-soft: #F8E4E1;
  --loss: #1E8F5B; --loss-soft: #DFF1E7;
  --warn: #A6720F; --warn-soft: #F6E8CE;
  --grid: #E1E7ED;
  --shadow: 0 1px 2px rgba(20,30,45,0.07), 0 6px 16px rgba(20,30,45,0.06);
}
* { box-sizing: border-box; }
body { margin: 0; }
.page { background: var(--bg); color: var(--text-primary); font-family: var(--sans); line-height: 1.6; min-height: 100vh; }
h1, h2, h3, .eyebrow, .badge, .navlink { font-family: var(--mono); }
.topbar {
  position: sticky; top: 0; z-index: 20; background: color-mix(in srgb, var(--surface) 92%, transparent);
  backdrop-filter: blur(8px); border-bottom: 1px solid var(--border);
  padding: 14px clamp(16px, 4vw, 40px); display: flex; align-items: baseline; gap: 20px; flex-wrap: wrap;
}
.topbar .title { font-size: 15px; font-weight: 700; letter-spacing: 0.02em; }
.updated { font-size: 11px; color: var(--text-muted); font-family: var(--mono); letter-spacing: 0.03em; margin-left: auto; }
.wrap { max-width: 1080px; margin: 0 auto; padding: clamp(20px, 4vw, 44px) clamp(16px, 4vw, 40px) 80px; }
.overview { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 40px; }
.ov-card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 18px 20px; box-shadow: var(--shadow); }
.ov-card .eyebrow { font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--text-muted); margin-bottom: 8px; }
.ov-card .headline { font-family: var(--mono); font-size: 26px; font-weight: 700; margin: 4px 0; font-variant-numeric: tabular-nums; }
.pill { display: inline-block; font-size: 10.5px; font-weight: 700; letter-spacing: 0.05em; padding: 3px 9px; border-radius: 999px; text-transform: uppercase; white-space: nowrap; }
.pill-gain { background: var(--gain-soft); color: var(--gain); }
.pill-loss { background: var(--loss-soft); color: var(--loss); }
.pill-accent { background: var(--accent-soft); color: var(--accent); }
.pill-muted { background: var(--surface-2); color: var(--text-muted); border: 1px solid var(--border); }
section.track { margin-bottom: 48px; }
.track > h2 { font-size: 13px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--text-muted); border-bottom: 1px solid var(--border); padding-bottom: 10px; margin: 0 0 20px; }
.news-card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 20px; margin-bottom: 12px; box-shadow: var(--shadow); }
.news-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 6px; }
.news-title { font-size: 14.5px; font-weight: 700; margin: 0; }
.news-title a { color: var(--text-primary); text-decoration: none; }
.news-title a:hover { color: var(--accent); }
.news-meta { font-size: 11.5px; color: var(--text-muted); font-family: var(--mono); margin-bottom: 8px; }
.news-stocks { font-size: 12px; color: var(--text-secondary); }
.news-stocks b { color: var(--text-primary); }
.empty-note { color: var(--text-muted); font-size: 13px; padding: 12px 0; }
footer { max-width: 1080px; margin: 0 auto; padding: 0 clamp(16px, 4vw, 40px) 60px; color: var(--text-muted); font-size: 12px; border-top: 1px solid var(--border); padding-top: 18px; }
"""

DIRECTION_PILL = {"利多": "pill-gain", "利空": "pill-loss", "中性關注": "pill-accent"}


def _news_card_html(item: dict) -> str:
    title = html.escape(item["title"])
    link = html.escape(item["link"])
    source = html.escape(item["source"])
    published = html.escape(item.get("published") or "")
    pill_class = DIRECTION_PILL.get(item["direction"], "pill-muted")
    keywords = "、".join(item["matched_keywords"]) or "—"
    stocks = "、".join(item["matched_stocks"]) or "—"
    return f"""
    <div class="news-card">
      <div class="news-head">
        <span class="pill {pill_class}">{item['direction']}</span>
        <span class="pill pill-muted">分數 {item['score']}</span>
        <h3 class="news-title"><a href="{link}" target="_blank" rel="noopener">{title}</a></h3>
      </div>
      <div class="news-meta">{source} ・ {published}</div>
      <div class="news-stocks"><b>關聯股票:</b> {stocks} ・ <b>命中關鍵字:</b> {keywords}</div>
    </div>
    """


def render_overview_and_news_sections(scored_news: list[dict], hot_stocks: list[dict], watchlist: dict) -> str:
    """回傳新聞總覽卡片 + 重要新聞/一般新聞區塊的 HTML(不含外層 <title>/<style>/<div class="page">)。"""
    important = [n for n in scored_news if n["is_important"]]
    general = [n for n in scored_news if not n["is_important"]]

    hot_stock_line = "、".join(f"{s['name']}({s['code']}){s['change_sign']}{s['change']}" for s in hot_stocks[:10]) or "無資料"
    group_names = "、".join(k for k in watchlist if not k.startswith("_"))

    important_html = "".join(_news_card_html(n) for n in important) or '<div class="empty-note">本次沒有偵測到重要新聞</div>'
    general_html = "".join(_news_card_html(n) for n in general[:30]) or '<div class="empty-note">暫無一般新聞</div>'

    return f"""
    <section class="overview">
      <div class="ov-card">
        <div class="eyebrow">重要新聞</div>
        <div class="headline" style="color:var(--gain)">{len(important)} 則</div>
      </div>
      <div class="ov-card">
        <div class="eyebrow">一般新聞</div>
        <div class="headline">{len(general)} 則</div>
      </div>
      <div class="ov-card">
        <div class="eyebrow">追蹤族群</div>
        <div class="headline" style="font-size:15px">{group_names or '未設定'}</div>
      </div>
      <div class="ov-card">
        <div class="eyebrow">今日成交量前十熱門股</div>
        <div class="headline" style="font-size:13px; line-height:1.7">{hot_stock_line}</div>
      </div>
    </section>

    <section class="track" id="important">
      <h2>重要新聞</h2>
      {important_html}
    </section>

    <section class="track" id="general">
      <h2>一般新聞(最多顯示30則)</h2>
      {general_html}
    </section>
    """


def render(scored_news: list[dict], hot_stocks: list[dict], watchlist: dict) -> str:
    now_str = dt.datetime.now().strftime("%Y/%m/%d %H:%M")
    sections_html = render_overview_and_news_sections(scored_news, hot_stocks, watchlist)

    return f"""<title>新聞監控儀表板</title>
<style>{STYLE}</style>
<div class="page">
  <div class="topbar">
    <span class="title">台股新聞監控儀表板</span>
    <span class="updated">最後更新 {now_str}</span>
  </div>
  <div class="wrap">
    {sections_html}
  </div>
  <footer>規則式關鍵字評分,僅供輔助參考,非投資建議。資料來源:Yahoo奇摩股市、自由時報財經、台灣證券交易所公開資訊。</footer>
</div>
"""
