"""AGP 核心指標，依 2019 年 International Consensus on Time in Range。

血糖單位一律 mg/dL。所有指標只吃 historic 讀數（見 parse_libre 的說明）。
"""

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

READING_INTERVAL_MIN = 15  # Libre 感測器取樣間隔
BINS_PER_DAY = 24 * 60 // READING_INTERVAL_MIN

# 五個分區的下界（mg/dL）與國際共識目標佔比（%）
BANDS = [
    ("very_low", "很低", None, 54, "<1"),
    ("low", "低", 54, 70, "<4"),
    ("target", "目標", 70, 180, ">70"),
    ("high", "高", 180, 250, "<25"),
    ("very_high", "很高", 250, None, "<5"),
]


@dataclass
class Metrics:
    start: datetime
    end: datetime
    days: int
    readings: int
    coverage_pct: float
    mean: float
    sd: float
    cv_pct: float
    gmi_pct: float
    band_pct: dict[str, float]
    agp: list[dict]          # 每個時間格的百分位
    daily: list[dict]        # 每日縮圖用


def _band_of(mgdl: float) -> str:
    for key, _label, lo, hi, _goal in BANDS:
        if (lo is None or mgdl >= lo) and (hi is None or mgdl < hi):
            return key
    return "very_high"


def _percentile(sorted_vals: list[float], q: float) -> float:
    """線性插值百分位；q 為 0..1。"""
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def slice_period(readings: list[tuple[datetime, float]], days: int,
                 end: datetime | None = None) -> list[tuple[datetime, float]]:
    """取最後 N 天的讀數。end 預設為資料中最後一筆的時間。"""
    if not readings:
        return []
    end = end or readings[-1][0]
    start = end - timedelta(days=days)
    return [r for r in readings if start <= r[0] <= end]


def compute(readings: list[tuple[datetime, float]], days: int,
            smooth_min: int = 45) -> Metrics:
    """算出一段期間的 AGP 指標。

    smooth_min 是百分位曲線的平滑半徑：每個時間格納入前後 N 分鐘內的讀數。
    14 天資料每格只有約 14 筆，不平滑的話百分位曲線會非常鋸齒。
    """
    if not readings:
        raise ValueError("期間內沒有血糖讀數")

    values = [v for _, v in readings]
    start, end = readings[0][0], readings[-1][0]
    mean = statistics.fmean(values)
    sd = statistics.pstdev(values) if len(values) > 1 else 0.0

    counts = {key: 0 for key, *_ in BANDS}
    for v in values:
        counts[_band_of(v)] += 1
    band_pct = {k: c / len(values) * 100 for k, c in counts.items()}

    # 百分位曲線：把每筆讀數放進當日的時間格，再跨日彙總同一格
    buckets: list[list[float]] = [[] for _ in range(BINS_PER_DAY)]
    for when, v in readings:
        minutes = when.hour * 60 + when.minute
        centre = minutes // READING_INTERVAL_MIN
        span = smooth_min // READING_INTERVAL_MIN
        for offset in range(-span, span + 1):
            buckets[(centre + offset) % BINS_PER_DAY].append(v)

    agp = []
    for i, bucket in enumerate(buckets):
        bucket.sort()
        agp.append({
            "minute": i * READING_INTERVAL_MIN,
            "n": len(bucket),
            "p5": _percentile(bucket, 0.05),
            "p25": _percentile(bucket, 0.25),
            "p50": _percentile(bucket, 0.50),
            "p75": _percentile(bucket, 0.75),
            "p95": _percentile(bucket, 0.95),
        })

    # 每日縮圖
    by_day: dict[str, list[tuple[int, float]]] = {}
    for when, v in readings:
        by_day.setdefault(when.date().isoformat(), []).append(
            (when.hour * 60 + when.minute, v))
    daily = [{"date": d, "points": sorted(pts),
              "in_target": sum(1 for _, v in pts if 70 <= v < 180) / len(pts) * 100}
             for d, pts in sorted(by_day.items())]

    return Metrics(
        start=start, end=end, days=days, readings=len(values),
        coverage_pct=len(values) / (days * BINS_PER_DAY) * 100,
        mean=mean, sd=sd,
        cv_pct=sd / mean * 100 if mean else 0.0,
        gmi_pct=3.31 + 0.02392 * mean,
        band_pct=band_pct, agp=agp, daily=daily,
    )
