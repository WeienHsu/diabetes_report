"""以純 SVG 產生報告圖表。

不用 matplotlib（中文字型與排版品質差）也不用 JS 圖表庫（離線與列印不可靠）。
SVG 由 Python 直接輸出，列印銳利、自包含、檔案小。
"""

from html import escape

from .metrics import band_of

# ── 設計 token ────────────────────────────────────────────────────────────
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"

# P1 醫師頁：臨床慣例分區色。刻意沿用 LibreView/Dexcom 的紅綠黃橙，
# 換色會讓醫師失去秒讀優勢；紅綠在色盲下不可分（deutan ΔE 4.1），
# 因此每段一律附直接數值標籤，並提供表格檢視作為補償。
BAND_COLOR = {
    "very_low": "#8b1a1a",
    "low": "#d03b3b",
    "target": "#0ca30c",
    "high": "#fab219",
    "very_high": "#ec835a",
}
BAND_LABEL = {
    "very_low": "很低 <54",
    "low": "低 54-69",
    "target": "目標 70-180",
    "high": "高 181-250",
    "very_high": "很高 >250",
}
# 深色底上的標籤用白字，淺色底（黃）用墨色
BAND_ON_COLOR = {"high": INK}

# 同一組分區色的淡底，用於時段表的儲存格。分區色本身在小面積色塊上
# 會蓋掉黑字，這裡只需要「一眼看出哪幾格偏高」，不需要飽和度。
BAND_TINT = {
    "very_low": "#f6e4e4",
    "low": "#fae3e3",
    "target": "#e8f4e8",
    "high": "#fdf1d8",
    "very_high": "#fbe6dd",
}

# P2+ 洞察頁：dataviz 參考配色前三槽，all-pairs 全數通過
SERIES = {"carbs": "#2a78d6", "fat": "#eb6834", "protein": "#1baf7a"}

TARGET_LO, TARGET_HI = 70, 180
# 上限取 400：Libre 讀數可到 400 以上，設 360 會把餐後高峰夾平，
# 讓「衝到 377」和「衝到 360」在圖上看起來一樣。
Y_MIN, Y_MAX = 40, 400
Y_LABELS = (70, 180, 250, 350)  # 54 有格線但不標字，否則會和 70 疊在一起


def _y(value: float, top: float, height: float) -> float:
    """血糖值 → 像素 y。超出範圍夾在邊界，避免畫出圖框。"""
    v = max(Y_MIN, min(Y_MAX, value))
    return top + height * (Y_MAX - v) / (Y_MAX - Y_MIN)


def _fmt(n: float) -> str:
    """座標取到小數 2 位。

    圖表最大不過 700px，1/100 px 已遠超顯示與列印所需；預設的 6 位有效數字
    只是把 `73.3333` 這種無意義精度灌進檔案，光每日縮圖就多出數十 KB。
    """
    return f"{n:.2f}".rstrip("0").rstrip(".")


def _path(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{'M' if i == 0 else 'L'}{_fmt(x)},{_fmt(y)}"
                    for i, (x, y) in enumerate(points))


def _band(pts_hi: list[tuple[float, float]], pts_lo: list[tuple[float, float]]) -> str:
    """兩條曲線之間的填色區。"""
    return _path(pts_hi) + " " + " ".join(
        f"L{_fmt(x)},{_fmt(y)}" for x, y in reversed(pts_lo)) + " Z"


# ── 互動層 ────────────────────────────────────────────────────────────────
# 十字準線、圓點與透明感應區。幾何參數以 data- 屬性傳給模板裡的 JS——
# 版面常數只在這裡定義一份，JS 不必跟著複製 Y_MIN/Y_MAX 或邊界寬度。
def _interactive(x0: float, x1: float, top: float, ph: float) -> str:
    return (
        f'<line class="xh-cross" x1="0" y1="{_fmt(top)}" x2="0" y2="{_fmt(top + ph)}" '
        f'stroke="{INK}" stroke-width="0.8" opacity="0"/>'
        f'<circle class="xh-dot" r="3.2" fill="{INK}" opacity="0"/>'
        f'<rect class="xh-hit" x="{_fmt(x0)}" y="{_fmt(top)}" width="{_fmt(x1 - x0)}" '
        f'height="{_fmt(ph)}" fill="transparent"/>')


def _xh_attrs(kind: str, x0: float, x1: float, top: float, ph: float, index: int = 0) -> str:
    return (f'class="xh" data-xh="{kind}" data-i="{index}" data-x0="{_fmt(x0)}" '
            f'data-x1="{_fmt(x1)}" data-top="{_fmt(top)}" data-ph="{_fmt(ph)}" '
            f'data-ymin="{Y_MIN}" data-ymax="{Y_MAX}"')


def _grid_and_axis(left: float, top: float, w: float, h: float,
                   x_ticks: list[tuple[float, str]]) -> str:
    """共用的血糖 y 軸格線 + 目標帶 + x 軸刻度。"""
    out = [
        # 目標範圍帶：整份報告最重要的參考，最先畫在底層
        f'<rect x="{_fmt(left)}" y="{_fmt(_y(TARGET_HI, top, h))}" width="{_fmt(w)}" '
        f'height="{_fmt(_y(TARGET_LO, top, h) - _y(TARGET_HI, top, h))}" '
        f'fill="{BAND_COLOR["target"]}" opacity="0.08"/>'
    ]
    for level in (54, 70, 180, 250, 350):
        y = _y(level, top, h)
        emphasis = level in (TARGET_LO, TARGET_HI)
        out.append(
            f'<line x1="{_fmt(left)}" y1="{_fmt(y)}" x2="{_fmt(left + w)}" y2="{_fmt(y)}" '
            f'stroke="{AXIS if emphasis else GRID}" stroke-width="1" '
            f'{"" if emphasis else "stroke-dasharray=\"2 3\""}/>')
        if level in Y_LABELS:
            out.append(
                f'<text x="{_fmt(left - 6)}" y="{_fmt(y + 3.5)}" text-anchor="end" '
                f'font-size="9" fill="{MUTED}">{level}</text>')
    for x, label in x_ticks:
        out.append(
            f'<text x="{_fmt(x)}" y="{_fmt(top + h + 14)}" text-anchor="middle" '
            f'font-size="9" fill="{MUTED}">{escape(label)}</text>')
    return "".join(out)


def agp_curve(agp: list[dict], width: int = 700, height: int = 210) -> str:
    """24 小時百分位曲線：p5-p95 外帶、p25-p75 內帶、p50 中位線。"""
    left, top = 34, 10
    w, h = width - left - 12, height - top - 24

    def pts(key: str) -> list[tuple[float, float]]:
        return [(left + w * b["minute"] / 1440, _y(b[key], top, h)) for b in agp]

    # 曲線需頭尾相接成完整 24 小時，補上終點
    def closed(key: str) -> list[tuple[float, float]]:
        p = pts(key)
        return p + [(left + w, p[0][1])]

    ticks = [(left + w * m / 1440, f"{m // 60:02d}:00") for m in range(0, 1441, 180)]
    return (
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'{_xh_attrs("agp", left, left + w, top, h)} '
        f'role="img" aria-label="24 小時血糖百分位曲線">'
        + _grid_and_axis(left, top, w, h, ticks)
        + f'<path d="{_band(closed("p95"), closed("p5"))}" fill="{INK_2}" opacity="0.13"/>'
        + f'<path d="{_band(closed("p75"), closed("p25"))}" fill="{INK_2}" opacity="0.26"/>'
        + f'<path d="{_path(closed("p50"))}" fill="none" stroke="{INK}" '
          f'stroke-width="2" stroke-linejoin="round"/>'
        + _interactive(left, left + w, top, h)
        + '</svg>'
    )


def tir_bar(band_pct: dict[str, float], width: int = 132, height: int = 210) -> str:
    """時間佔比堆疊條。每段直接標數值——這是紅綠不可分時的主要補償。"""
    order = ["very_high", "high", "target", "low", "very_low"]  # 由上而下＝高到低
    bar_w, left, top = 46, 0, 6
    # 底部留白給被往下推開的細分段引線標籤，否則最後一個標籤會撞到畫布邊緣
    h = height - top - 20
    gap = 2  # 段與段之間的表面色間隙

    out = [f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
           f'role="img" aria-label="血糖時間佔比">']
    y = top
    last_label_y = None  # 相鄰的細分段引線標籤會疊在一起，需往下推開
    for key in order:
        pct = band_pct.get(key, 0.0)
        seg = max(h * pct / 100 - gap, 0)
        if pct > 0:
            out.append(
                f'<rect x="{_fmt(left)}" y="{_fmt(y)}" width="{bar_w}" height="{_fmt(seg)}" '
                f'fill="{BAND_COLOR[key]}"/>')
            # 段夠高就把數值放進色塊，太薄則移到右側引線標示，避免壓字
            if seg >= 15:
                out.append(
                    f'<text x="{_fmt(left + bar_w / 2)}" y="{_fmt(y + seg / 2 + 4)}" '
                    f'text-anchor="middle" font-size="11" font-weight="600" '
                    f'fill="{BAND_ON_COLOR.get(key, "#ffffff")}">{pct:.1f}%</text>')
            else:
                anchor = y + seg / 2
                label_y = anchor if last_label_y is None else max(anchor, last_label_y + 11)
                last_label_y = label_y
                out.append(
                    f'<polyline points="{_fmt(left + bar_w)},{_fmt(anchor)} '
                    f'{_fmt(left + bar_w + 5)},{_fmt(anchor)} '
                    f'{_fmt(left + bar_w + 9)},{_fmt(label_y)}" '
                    f'fill="none" stroke="{AXIS}" stroke-width="1"/>')
                out.append(
                    f'<text x="{_fmt(left + bar_w + 12)}" y="{_fmt(label_y + 3.5)}" '
                    f'font-size="10" font-weight="600" fill="{INK}">{pct:.1f}%</text>')
        y += h * pct / 100
    out.append('</svg>')
    return "".join(out)


def daily_grid(daily: list[dict], width: int = 710, cell_h: int = 58) -> str:
    """每日縮圖。橫軸固定 0-24 時，讓各日可直接對齊比較。

    每列格數由天數決定並盡量湊成兩列：14 天的期間常橫跨 15 個日期，
    固定 7 格一列會讓第三列只剩一格孤兒，白白吃掉一整列的高度。
    """
    gap_x, gap_y, label_h = 4, 20, 12
    n = len(daily)
    per_row = max(1, -(-n // 2)) if n <= 16 else 7
    cell_w = (width - (per_row - 1) * gap_x) / per_row
    rows = -(-n // per_row)
    height = rows * (cell_h + label_h + gap_y)

    out = [f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
           f'role="img" aria-label="每日血糖縮圖">']
    for i, day in enumerate(daily):
        cx = (i % per_row) * (cell_w + gap_x)
        cy = (i // per_row) * (cell_h + label_h + gap_y) + label_h
        out.append(
            f'<text x="{_fmt(cx)}" y="{_fmt(cy - 4)}" font-size="8.5" fill="{INK_2}">'
            f'{escape(day["date"][5:])}</text>')
        out.append(
            f'<rect x="{_fmt(cx)}" y="{_fmt(cy)}" width="{_fmt(cell_w)}" height="{cell_h}" '
            f'fill="#ffffff" stroke="{GRID}" stroke-width="1"/>')
        out.append(
            f'<rect x="{_fmt(cx)}" y="{_fmt(_y(TARGET_HI, cy, cell_h))}" width="{_fmt(cell_w)}" '
            f'height="{_fmt(_y(TARGET_LO, cy, cell_h) - _y(TARGET_HI, cy, cell_h))}" '
            f'fill="{BAND_COLOR["target"]}" opacity="0.10"/>')
        pts = [(cx + cell_w * m / 1440, _y(v, cy, cell_h)) for m, v in day["points"]]
        if pts:
            out.append(f'<path d="{_path(pts)}" fill="none" stroke="{INK}" '
                       f'stroke-width="1" stroke-linejoin="round" opacity="0.75"/>')
    out.append('</svg>')
    return "".join(out)


def meal_curve(response, width: int = 320, height: int = 128) -> str:
    """單一餐次的餐後曲線，疊上進食時刻與注射標記。"""
    left, top = 30, 8
    w, h = width - left - 10, height - top - 22
    lo, hi = -30, 240
    x_of = lambda m: left + w * (m - lo) / (hi - lo)

    ticks = [(x_of(m), f"{m // 60}h" if m else "進食") for m in (0, 60, 120, 180, 240)]
    out = [f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
           f'role="img" aria-label="餐後血糖反應">',
           _grid_and_axis(left, top, w, h, ticks)]

    # 進食時刻
    out.append(f'<line x1="{_fmt(x_of(0))}" y1="{_fmt(top)}" x2="{_fmt(x_of(0))}" '
               f'y2="{_fmt(top + h)}" stroke="{INK}" stroke-width="1.5"/>')

    pts = [(x_of(m), _y(v, top, h)) for m, v in response.curve if lo <= m <= hi]
    if pts:
        out.append(f'<path d="{_path(pts)}" fill="none" stroke="{INK}" '
                   f'stroke-width="2" stroke-linejoin="round"/>')

    # 注射標記：白色描邊環讓它疊在曲線上仍看得清
    if response.prebolus_min is not None:
        bx = x_of(max(lo, -response.prebolus_min))
        out.append(f'<circle cx="{_fmt(bx)}" cy="{_fmt(top + h - 6)}" r="5" '
                   f'fill="{SERIES["carbs"]}" stroke="{SURFACE}" stroke-width="2"/>')
        out.append(f'<text x="{_fmt(bx)}" y="{_fmt(top + h - 12)}" text-anchor="middle" '
                   f'font-size="9" font-weight="600" fill="{SERIES["carbs"]}">'
                   f'{response.bolus_units:.0f}U</text>')

    # 峰值只在真的上升過時才標
    if response.rose and response.peak_min is not None:
        px, py = x_of(response.peak_min), _y(response.peak, top, h)
        out.append(f'<circle cx="{_fmt(px)}" cy="{_fmt(py)}" r="3.5" fill="{INK}" '
                   f'stroke="{SURFACE}" stroke-width="2"/>')
        # 峰值貼近上緣時標籤改放點下方，否則會被畫布裁掉
        above = py - top > 14
        out.append(f'<text x="{_fmt(px)}" y="{_fmt(py - 8 if above else py + 13)}" '
                   f'text-anchor="middle" font-size="10" font-weight="600" fill="{INK}">'
                   f'{response.peak:.0f}</text>')
    out.append('</svg>')
    return "".join(out)


# ── 每日詳圖 ──────────────────────────────────────────────────────────────
DETAIL_LABEL_W = 78      # 左側標籤欄，與下方每小時表格的第一欄同寬才對得齊
DETAIL_H = 84
# 早／午／晚的分界。實線讓眼睛有錨點，其餘格線維持虛線。
DETAIL_ANCHOR_HOURS = (6, 12, 18)
SCAN_COLOR = "#2a78d6"   # 洞察頁配色的藍，與注射的紫可區分
BOLUS_COLOR = "#8e2b6b"


def daily_detail(detail, width: int = 710, index: int = 0) -> str:
    """單日全寬曲線：依分區上色、疊上注射時刻與掃描點。

    與 daily_grid 的差別是這裡是主角而非縮圖——有座標軸、有標記，
    看得出「幾點打了幾單位、之後血糖怎麼走」。
    """
    plot_w = width - DETAIL_LABEL_W
    slots = detail.slots

    def x(minute: float) -> float:
        return DETAIL_LABEL_W + plot_w * minute / 1440

    def y(value: float) -> float:
        return _y(value, 0, DETAIL_H)

    out = [f'<svg viewBox="0 0 {width} {DETAIL_H}" width="{width}" height="{DETAIL_H}" '
           f'{_xh_attrs("day", DETAIL_LABEL_W, width, 0, DETAIL_H, index)} '
           f'role="img" aria-label="{escape(detail.day.isoformat())} 血糖曲線">']

    out.append(f'<rect x="{DETAIL_LABEL_W}" y="{_fmt(y(TARGET_HI))}" width="{_fmt(plot_w)}" '
               f'height="{_fmt(y(TARGET_LO) - y(TARGET_HI))}" '
               f'fill="{BAND_COLOR["target"]}" opacity="0.13"/>')
    for hour in range(0, 25, 2):
        anchor = hour in DETAIL_ANCHOR_HOURS
        out.append(f'<line x1="{_fmt(x(hour * 60))}" y1="0" x2="{_fmt(x(hour * 60))}" '
                   f'y2="{DETAIL_H}" stroke="{AXIS if anchor else GRID}" '
                   f'stroke-width="{0.8 if anchor else 0.5}"'
                   f'{"" if anchor else " stroke-dasharray=\"2 3\""}/>')
    for value in (70, 180, 350):
        out.append(f'<line x1="{DETAIL_LABEL_W}" y1="{_fmt(y(value))}" x2="{width}" '
                   f'y2="{_fmt(y(value))}" stroke="{AXIS}" stroke-width="0.5"/>')
        out.append(f'<text x="{DETAIL_LABEL_W - 4}" y="{_fmt(y(value) + 3)}" font-size="7.5" '
                   f'fill="{MUTED}" text-anchor="end">{value}</text>')

    # 逐段上色。相鄰兩格都有值才連——感測器脫落數小時後回來，直接連起來
    # 會描出一條實際不存在的平緩曲線。
    for i in range(len(slots) - 1):
        a, b = slots[i], slots[i + 1]
        if a is None or b is None:
            continue
        out.append(
            f'<line x1="{_fmt(x(i * 15))}" y1="{_fmt(y(a))}" '
            f'x2="{_fmt(x((i + 1) * 15))}" y2="{_fmt(y(b))}" '
            f'stroke="{BAND_COLOR[band_of(b)]}" stroke-width="1.7" stroke-linecap="round"/>')

    for minute in detail.scans:
        value = slots[min(minute // 15, len(slots) - 1)]
        if value is None:
            continue
        out.append(f'<circle cx="{_fmt(x(minute))}" cy="{_fmt(y(value))}" r="2.4" '
                   f'fill="{SURFACE}" stroke="{SCAN_COLOR}" stroke-width="1"/>')

    for minute, _units in detail.insulin:
        value = slots[min(minute // 15, len(slots) - 1)]
        top = (y(value) if value is not None else y(TARGET_HI)) - 10
        out.append(f'<line x1="{_fmt(x(minute))}" y1="{_fmt(top)}" x2="{_fmt(x(minute))}" '
                   f'y2="{_fmt(top + 6)}" stroke="{BOLUS_COLOR}" stroke-width="1.6"/>')
        out.append(f'<circle cx="{_fmt(x(minute))}" cy="{_fmt(top - 1.5)}" r="1.9" '
                   f'fill="{BOLUS_COLOR}"/>')

    out.append(_interactive(DETAIL_LABEL_W, width, 0, DETAIL_H))
    out.append("</svg>")
    return "".join(out)
