"""產生血糖回診報告。

    python -m agp_report --glucose data/glucose.csv --food data/food.csv \
                         --days 14 --out out/report.html
"""

import argparse
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


def main() -> None:
    ap = argparse.ArgumentParser(prog="agp_report", description="產生血糖回診報告")
    ap.add_argument("--glucose", required=True, help="Libre 匯出的 CSV")
    ap.add_argument("--food", help="Google Sheet 飲食日誌分頁匯出的 CSV（選用）")
    ap.add_argument("--days", type=int, default=14, help="分析期間天數（預設 14，AGP 標準）")
    ap.add_argument("--out", default="out/report.html", help="輸出 HTML 路徑")
    args = ap.parse_args()

    libre = parse_libre.parse(args.glucose)
    if not libre.historic:
        raise SystemExit("錯誤：CSV 內沒有歷史葡萄糖讀數（記錄類型 0）")

    period = metrics.slice_period(libre.historic, args.days)
    m = metrics.compute(period, args.days)

    responses, skipped, drinks, food_start = [], 0, 0, "—"
    if args.food:
        all_meals = parse_food.parse(args.food)
        if all_meals:
            food_start = all_meals[0].when.strftime("%Y-%m-%d")
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

    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    html = env.get_template("report.html.j2").render(
        m=m,
        patient=libre.patient,
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

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"已產出 {out}（{len(html) // 1024} KB）")
    print(f"  期間 {m.start:%Y-%m-%d} – {m.end:%Y-%m-%d}　涵蓋率 {m.coverage_pct:.0f}%"
          f"　TIR {m.band_pct['target']:.1f}%　餐次 {len(responses)}")


if __name__ == "__main__":
    main()
