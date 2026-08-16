"""把餐次對上血糖軌跡與胰島素注射，算出餐後反應。

這是本專案相對 LibreView 的差異點：LibreView 只有碳水克數，
這裡的餐次帶著蛋白質、脂肪與品項名稱，才看得出高脂餐的延遲性高血糖。
"""

from dataclasses import dataclass
from datetime import timedelta

from .parse_food import Meal

BEFORE_MIN = 30    # 餐前納入視窗
AFTER_MIN = 240    # 餐後追蹤 4 小時
BASELINE_MIN = 20  # 餐前基準值的容許回溯範圍
BOLUS_BEFORE_MIN = 60  # 注射視為屬於這一餐的最早時間（相對第一筆進食）
BOLUS_AFTER_MIN = 30   # 注射視為屬於這一餐的最晚時間（相對最後一筆進食）


@dataclass
class MealResponse:
    meal: Meal
    curve: list[tuple[int, float]]      # (相對分鐘, mg/dL)，餐前為負
    baseline: float | None
    peak: float | None
    peak_min: int | None
    auc: float                          # 基準線以上面積（mg/dL·分鐘）
    bolus_units: float
    bolus_count: int                    # 幾劑加總而來；>1 時卡片會標明
    prebolus_min: int | None            # 進食前多久注射；負值代表餐後才打
    enough_data: bool
    svg: str = ""                       # 由 charts 填入
    verdict: str = ""                   # 由報告層填入

    @property
    def delta(self) -> float | None:
        if self.baseline is None or self.peak is None:
            return None
        return self.peak - self.baseline

    @property
    def rose(self) -> bool:
        """餐後血糖是否真的上升過。

        全程下降時 peak 只是視窗內的最大值，Δ 會是負數，
        「峰值」與「到峰時間」都沒有意義，報告需改標示為未上升。
        """
        return self.delta is not None and self.delta > 0


def analyse(meal: Meal, historic: list[tuple], insulin: list[tuple]) -> MealResponse:
    lo = meal.when - timedelta(minutes=BEFORE_MIN)
    hi = meal.when + timedelta(minutes=AFTER_MIN)
    window = [(int((t - meal.when).total_seconds() // 60), v)
              for t, v in historic if lo <= t <= hi]

    # 視窗綁在整個進食場合的跨距上，不是只綁起始點。合併的餐次（例如晚餐
    # 20:05 併入 20:30 的點心）裡，第二份食物的針通常是「吃的時候才打」，
    # 若窗尾仍以起始時間為準，那一劑會落在窗外、完全不算也不提示。
    # 單筆餐次時 ends == when，行為與先前完全相同。
    doses = [(t, u) for t, u in insulin
             if meal.when - timedelta(minutes=BOLUS_BEFORE_MIN) <= t
             <= meal.ends + timedelta(minutes=BOLUS_AFTER_MIN)]
    bolus_units = sum(u for _, u in doses)
    prebolus = int((meal.when - min(t for t, _ in doses)).total_seconds() // 60) if doses else None

    # 基準值取餐前最後一筆讀數
    before = [(m, v) for m, v in window if -BASELINE_MIN <= m <= 0]
    baseline = before[-1][1] if before else None

    after = [(m, v) for m, v in window if m >= 0]
    peak_min = peak = None
    if after:
        peak_min, peak = max(after, key=lambda x: x[1])

    auc = 0.0
    if baseline is not None and len(after) > 1:
        for (m0, v0), (m1, v1) in zip(after, after[1:]):
            avg = (max(v0 - baseline, 0) + max(v1 - baseline, 0)) / 2
            auc += avg * (m1 - m0)

    # 餐後至少要覆蓋 2 小時才算得出有意義的反應
    enough = bool(after) and max(m for m, _ in after) >= 120 and baseline is not None

    return MealResponse(
        meal=meal, curve=window, baseline=baseline, peak=peak, peak_min=peak_min,
        auc=auc, bolus_units=bolus_units, bolus_count=len(doses),
        prebolus_min=prebolus, enough_data=enough,
    )


def analyse_all(meals: list[Meal], historic: list[tuple],
                insulin: list[tuple]) -> list[MealResponse]:
    return [analyse(m, historic, insulin) for m in meals]
