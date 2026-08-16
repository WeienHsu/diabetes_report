"""產生血糖回診報告。

    python -m agp_report --glucose data/glucose.csv --food data/food.csv \
                         --days 14 --out out/report.html

組裝邏輯在 build_report()，CLI 只是它的一層薄包裝——web 層需要同一份邏輯，
但不能吃掉 SystemExit，也不該被迫先把 HTML 寫到檔案再讀回來。
"""

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import charts, meals, metrics, parse_food, parse_libre

# 國際共識目標。第二欄是實際據以判定的累計分區——例如「低」的 4% 門檻
# 是針對 <70 全體，不是單獨的 54-69 區間。
GOALS = {
    "very_low": ("<1", ["very_low"]),
    "low": ("<4", ["very_low", "low"]),
    "target": (">70", ["target"]),
    "high": ("<25", ["high", "very_high"]),
    "very_high": ("<5", ["very_high"]),
}
BAND_ORDER = ["very_high", "high", "target", "low", "very_low"]

# 低於此標準的紀錄（無糖茶、黑咖啡）不產生餐後反應卡
NON_NUTRITIVE_CARBS, NON_NUTRITIVE_KCAL = 5.0, 25.0

# 每日縮圖最多顯示的天數。指標本身仍涵蓋完整 --days 期間，但 90 天會排出
# 91 個縮圖、把第 1 頁撐成兩頁——「醫師一頁秒讀」正是這頁存在的理由。
DAILY_PROFILE_DAYS = 14


def _hhmm(pct: float) -> str:
    total = round(pct / 100 * 24 * 60)
    return f"{total // 60}h {total % 60:02d}m"


def _band_rows(band_pct: dict[str, float]) -> list[dict]:
    rows = []
    for key in BAND_ORDER:
        goal_text, members = GOALS[key]
        actual = sum(band_pct.get(k, 0.0) for k in members)
        ok = actual > 70 if key == "target" else actual < float(goal_text[1:])
        rows.append({
            "label": charts.BAND_LABEL[key],
            "color": charts.BAND_COLOR[key],
            "pct": band_pct.get(key, 0.0),
            "hhmm": _hhmm(band_pct.get(key, 0.0)),
            "goal": goal_text,
            "ok": ok,
            # 達標判定用的是累計分區（「低」比的是 <70 全體），與本列顯示的
            # 單一分區佔比不同。不寫出來讀者會以為 1.3% 是直接跟 <4% 比。
            "combined": actual if len(members) > 1 else None,
        })
    return rows


def _verdict(r) -> str:
    """描述這一餐的反應。只陳述資料，不給劑量建議。"""
    if not r.rose:
        return f"餐前 <b>{r.baseline:.0f}</b>，餐後 4 小時未上升（持續下降）。"

    parts = [f"餐前 <b>{r.baseline:.0f}</b> → 峰值 <b>{r.peak:.0f}</b> "
             f"（Δ+{r.delta:.0f}，第 {r.peak_min} 分鐘）"]
    if r.meal.fat >= 25 and r.peak_min >= 180:
        parts.append("高脂餐，峰值落在 3 小時後——脂肪延緩胃排空的典型型態。")
    if r.bolus_units == 0 and r.meal.carbs >= 30:
        parts.append(f"此餐含 {r.meal.carbs:.0f}g 碳水但未記錄注射。")
    elif r.prebolus_min is not None and r.prebolus_min <= 5 and r.meal.carbs >= 40:
        parts.append(f"前置注射僅 {r.prebolus_min} 分鐘。")
    return "　".join(parts)


class ReportError(Exception):
    """輸入資料本身的問題。訊息寫給使用者看，可直接呈現在網頁上。"""


@dataclass
class Summary:
    """產完報告後的一句話交代，CLI 印出來、web 層存進 meta 與歷史清單。"""

    start: datetime
    end: datetime
    days: int
    coverage_pct: float
    tir_pct: float
    meals: int
    hypo_events: int


def build_report(glucose_path: str, food_path: str | None = None, days: int = 14,
                 toolbar: dict[str, str] | None = None) -> tuple[str, Summary]:
    """讀 CSV、算指標、組出單檔自包含 HTML。回傳 (html, summary)。

    toolbar 只有 web 層會給（{"pdf": ..., "new": ...}），列印時一律隱藏，
    因此同一份 HTML 既是線上頁面、也是轉 PDF 的來源。
    """
    libre = parse_libre.parse(glucose_path)
    if not libre.historic:
        raise ReportError("這份 CSV 裡沒有歷史葡萄糖讀數（記錄類型 0）。"
                          "請確認匯出的是 LibreView 的完整資料。")

    period = metrics.slice_period(libre.historic, days)
    m = metrics.compute(period, days)

    responses, skipped, drinks, food_start = [], 0, 0, "—"
    block_meals: list[tuple[datetime, float]] = []
    if food_path:
        all_meals = parse_food.parse(food_path)
        # 這幾乎一定是匯出錯分頁。原本只是靜靜產出 0 餐，讀者要翻到第 3 頁
        # 才會發現餐食分析整段不見了——不如當場講清楚。
        if not all_meals:
            raise ReportError(
                "飲食 CSV 解析出 0 餐。試算表有「食物資料庫」與「飲食日誌」兩個分頁，"
                "匯出時只會拿到當前所在的那一頁——請切到「飲食日誌」再匯出一次。")
        if all_meals:
            food_start = all_meals[0].when.strftime("%Y-%m-%d")
        block_meals = [(x.when, x.carbs) for x in all_meals if m.start <= x.when <= m.end]
        for r in meals.analyse_all(all_meals, libre.historic, libre.insulin):
            if not (m.start <= r.meal.when <= m.end):
                continue
            if r.meal.carbs < NON_NUTRITIVE_CARBS and r.meal.kcal < NON_NUTRITIVE_KCAL:
                drinks += 1          # 無糖茶水之類，畫成餐後反應卡只是雜訊
            elif not r.enough_data:
                skipped += 1
            else:
                r.svg = charts.meal_curve(r)
                r.verdict = _verdict(r)
                responses.append(r)

    period_insulin = [(t, u) for t, u in libre.insulin if m.start <= t <= m.end]
    events = metrics.hypo_events(period, period_insulin)
    blocks = metrics.time_blocks(period, period_insulin, block_meals, days)
    days_rows = metrics.daily_summary(period, period_insulin, events)

    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    html = env.get_template("report.html.j2").render(
        m=m,
        patient=libre.patient,
        events=events,
        blocks=blocks,
        # 手機版把同一批數字改依平均值排序——「哪個時段最糟」正是這頁的目的
        blocks_ranked=sorted(blocks, key=lambda b: b.mean, reverse=True),
        worst_block=max(blocks, key=lambda b: b.mean) if blocks else None,
        band_tint=charts.BAND_TINT,
        toolbar=toolbar,
        # 90 天會排出 91 列，把 P2 撐成三頁以上——與每日縮圖砍到 14 天同一個問題
        days_rows=days_rows[-DAILY_PROFILE_DAYS:],
        days_note=(f"僅列最近 {DAILY_PROFILE_DAYS} 天；時段統計與低血糖事件涵蓋完整 "
                   f"{m.days} 天期間" if len(days_rows) > DAILY_PROFILE_DAYS else None),
        tir_svg=charts.tir_bar(m.band_pct, height=300),
        agp_svg=charts.agp_curve(m.agp),
        daily_svg=charts.daily_grid(m.daily[-DAILY_PROFILE_DAYS:]),
        daily_note=(f"僅顯示最近 {DAILY_PROFILE_DAYS} 天；上方各項指標仍涵蓋完整 "
                    f"{m.days} 天期間" if len(m.daily) > DAILY_PROFILE_DAYS else None),
        band_rows=_band_rows(m.band_pct),
        responses=responses,
        skipped=skipped,
        drinks=drinks,
        food_start=food_start,
        thin_warning=(
            f"目前僅 {len(responses)} 餐可分析，樣本數不足以歸納個人化的食物影響排行"
            "或推估碳水／胰島素比。累積約 4–6 週飲食紀錄後，這些分析才具參考價值。"
            if 0 < len(responses) < 20 else None
        ),
        insulin_count=len([1 for t, _ in libre.insulin if m.start <= t <= m.end]),
        insulin_units=sum(u for t, u in libre.insulin if m.start <= t <= m.end),
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )

    return html, Summary(
        start=m.start, end=m.end, days=m.days,
        coverage_pct=m.coverage_pct, tir_pct=m.band_pct["target"],
        meals=len(responses), hypo_events=len(events),
    )


def main() -> None:
    ap = argparse.ArgumentParser(prog="agp_report", description="產生血糖回診報告")
    ap.add_argument("--glucose", required=True, help="Libre 匯出的 CSV")
    ap.add_argument("--food", help="Google Sheet 飲食日誌分頁匯出的 CSV（選用）")
    ap.add_argument("--days", type=int, default=14, help="分析期間天數（預設 14，AGP 標準）")
    ap.add_argument("--out", default="out/report.html", help="輸出 HTML 路徑")
    args = ap.parse_args()

    try:
        html, s = build_report(args.glucose, args.food, args.days)
    except ReportError as exc:
        raise SystemExit(f"錯誤：{exc}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"已產出 {out}（{len(html) // 1024} KB）")
    print(f"  期間 {s.start:%Y-%m-%d} – {s.end:%Y-%m-%d}　涵蓋率 {s.coverage_pct:.0f}%"
          f"　TIR {s.tir_pct:.1f}%　餐次 {s.meals}")


if __name__ == "__main__":
    main()
