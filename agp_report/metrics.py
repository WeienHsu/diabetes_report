"""AGP 核心指標，依 2019 年 International Consensus on Time in Range。

血糖單位一律 mg/dL。所有指標只吃 historic 讀數（見 parse_libre 的說明）。
"""

import statistics
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

READING_INTERVAL_MIN = 15  # Libre 感測器取樣間隔
BINS_PER_DAY = 24 * 60 // READING_INTERVAL_MIN

# 低血糖事件（國際共識）：連續 15 分鐘以上低於 70 mg/dL 算一次。
# 感測器有斷線時，跨過空窗的兩段讀數不算同一次事件——否則貼片脫落幾小時
# 再回來、剛好前後都偏低，會捏造出一次橫跨數小時的假事件。
HYPO_THRESHOLD = 70
HYPO_MIN_MINUTES = 15
HYPO_MAX_GAP_MIN = 2 * READING_INTERVAL_MIN

BLOCK_HOURS = 2  # 時段統計的區間長度
WEEKDAY_ZH = "一二三四五六日"

# 五個分區的下界（mg/dL）與國際共識目標佔比（%）
BANDS = [
    ("very_low", "很低", None, 54, "<1"),
    ("low", "低", 54, 70, "<4"),
    ("target", "目標", 70, 180, ">70"),
    ("high", "高", 180, 250, "<25"),
    ("very_high", "很高", 250, None, "<5"),
]


@dataclass
class HypoEvent:
    start: datetime
    end: datetime
    minutes: int
    nadir: float
    bolus_before: float = 0.0   # 事件前 4 小時內的速效總量，由 hypo_events 填入

    @property
    def period(self) -> str:
        """發生時段。低血糖落在睡眠時段的臨床意義最重，值得單獨標示。"""
        h = self.start.hour
        if h < 3:
            return "深夜"
        if h < 6:
            return "凌晨"
        if h < 12:
            return "上午"
        if h < 18:
            return "下午"
        return "夜間"


@dataclass
class TimeBlock:
    hour: int                   # 區間起始小時
    n: int
    mean: float
    tir_pct: float
    very_high_pct: float
    insulin_per_day: float
    meals: int
    carbs_mean: float

    @property
    def label(self) -> str:
        return f"{self.hour:02d}-{self.hour + BLOCK_HOURS:02d}"

    @property
    def band(self) -> str:
        """平均值所屬分區，供報告替該格上淡色底。"""
        return _band_of(self.mean)


@dataclass
class DaySummary:
    day: date
    weekday: str
    n: int
    mean: float
    tir_pct: float
    highest: float
    lowest: float
    insulin_units: float
    hypo_count: int
    partial: bool               # 期間頭尾被切斷的日子，數值不可與整日相比


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


def hypo_events(readings: list[tuple[datetime, float]],
                insulin: list[tuple[datetime, float]] = ()) -> list[HypoEvent]:
    """低血糖事件。

    只報 TBR 佔比會漏掉最重要的那件事：1.4% 分散成十幾次五分鐘的雜訊，
    和集中在睡夢中一次 60 分鐘的低血糖，臨床意義完全不同。
    """
    runs: list[list[tuple[datetime, float]]] = []
    current: list[tuple[datetime, float]] = []
    for when, value in readings:
        # 回到 70 以上、或與前一筆之間有斷線空窗，都讓目前這段收尾
        gapped = bool(current) and when - current[-1][0] > timedelta(minutes=HYPO_MAX_GAP_MIN)
        if value >= HYPO_THRESHOLD or gapped:
            if current:
                runs.append(current)
            current = []
        if value < HYPO_THRESHOLD:
            current.append((when, value))
    if current:
        runs.append(current)

    events = []
    for run in runs:
        minutes = int((run[-1][0] - run[0][0]).total_seconds() // 60)
        if minutes < HYPO_MIN_MINUTES:
            continue
        window = run[0][0] - timedelta(hours=4)
        events.append(HypoEvent(
            start=run[0][0], end=run[-1][0], minutes=minutes,
            nadir=min(v for _, v in run),
            bolus_before=sum(u for t, u in insulin if window <= t < run[0][0]),
        ))
    return events


def time_blocks(readings: list[tuple[datetime, float]],
                insulin: list[tuple[datetime, float]],
                meals: list[tuple[datetime, float]],
                days: int) -> list[TimeBlock]:
    """每 2 小時的時段統計。

    AGP 曲線看得出形狀，但講不出「哪個時段最糟」。這張表把曲線翻譯成
    可以拿去跟醫師討論的具體時段。meals 為 (時間, 碳水克數)。
    """
    blocks = []
    for hour in range(0, 24, BLOCK_HOURS):
        end_hour = hour + BLOCK_HOURS
        values = [v for t, v in readings if hour <= t.hour < end_hour]
        if not values:
            continue
        units = [u for t, u in insulin if hour <= t.hour < end_hour]
        carbs = [c for t, c in meals if hour <= t.hour < end_hour]
        blocks.append(TimeBlock(
            hour=hour,
            n=len(values),
            mean=statistics.fmean(values),
            tir_pct=sum(1 for v in values if 70 <= v < 180) / len(values) * 100,
            very_high_pct=sum(1 for v in values if v >= 250) / len(values) * 100,
            insulin_per_day=sum(units) / days if days else 0.0,
            meals=len(carbs),
            carbs_mean=statistics.fmean(carbs) if carbs else 0.0,
        ))
    return blocks


def daily_summary(readings: list[tuple[datetime, float]],
                  insulin: list[tuple[datetime, float]],
                  events: list[HypoEvent]) -> list[DaySummary]:
    """每日一列的摘要。揭露每日縮圖看不出來的東西——例如某天速效只記了 4u。"""
    if not readings:
        return []
    by_day: dict[date, list[float]] = {}
    for when, value in readings:
        by_day.setdefault(when.date(), []).append(value)

    # 期間的頭尾兩天通常只涵蓋半天，其血糖與胰島素都不可與整日相比。
    # 不標出來的話，最後一天的低胰島素會被誤讀成漏記。
    first, last = readings[0][0], readings[-1][0]
    cut_first = first.date() if first.time() > time(0, 0) else None
    cut_last = last.date() if last.time() < time(23, 45) else None

    rows = []
    for day, values in sorted(by_day.items()):
        rows.append(DaySummary(
            day=day,
            weekday=WEEKDAY_ZH[day.weekday()],
            n=len(values),
            mean=statistics.fmean(values),
            tir_pct=sum(1 for v in values if 70 <= v < 180) / len(values) * 100,
            highest=max(values),
            lowest=min(values),
            insulin_units=sum(u for t, u in insulin if t.date() == day),
            # 跨日事件歸屬於起始日
            hypo_count=sum(1 for e in events if e.start.date() == day),
            partial=day in (cut_first, cut_last),
        ))
    return rows
