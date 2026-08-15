"""解析 Google Sheet「飲食日誌」分頁匯出的 CSV。

鄰近的多筆明細視為同一次進食（例如晚餐的主菜與白飯分兩列、或邊吃邊補記
而差了幾分鐘），營養素加總、品項合併成一份餐次。不合併的話同一頓飯會
拆成數張餐後反應卡，共用同一劑胰島素、重複計算同一個血糖峰值。
"""

import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# Sheet 的時間欄位小時不補零（例如 "2026-08-14 8:24"）
TIMESTAMP_FMT = "%Y-%m-%d %H:%M"

# 距離同一餐起始多久內的紀錄併入該餐
CLUSTER_WINDOW = timedelta(minutes=30)


@dataclass
class Item:
    brand: str
    name: str
    serving: str
    carbs: float
    protein: float
    fat: float
    kcal: float


@dataclass
class Meal:
    when: datetime
    label: str                       # 餐別
    items: list[Item] = field(default_factory=list)

    @property
    def carbs(self) -> float:
        return sum(i.carbs for i in self.items)

    @property
    def protein(self) -> float:
        return sum(i.protein for i in self.items)

    @property
    def fat(self) -> float:
        return sum(i.fat for i in self.items)

    @property
    def kcal(self) -> float:
        return sum(i.kcal for i in self.items)

    @property
    def title(self) -> str:
        return "＋".join(i.name for i in self.items)


def _num(raw: str) -> float:
    try:
        return float(raw.strip())
    except (ValueError, AttributeError):
        return 0.0


def parse(path: str) -> list[Meal]:
    entries: list[tuple[datetime, str, Item]] = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            raw = (row.get("日期時間") or "").strip()
            try:
                when = datetime.strptime(raw, TIMESTAMP_FMT)
            except ValueError:
                continue
            entries.append((when, (row.get("餐別") or "").strip(), Item(
                brand=(row.get("品牌") or "").strip(),
                name=(row.get("產品名稱") or "").strip(),
                serving=(row.get("攝取份量") or "").strip(),
                carbs=_num(row.get("碳水化合物 (g)", "")),
                protein=_num(row.get("蛋白質 (g)", "")),
                fat=_num(row.get("脂肪 (g)", "")),
                kcal=_num(row.get("熱量 (kcal)", "")),
            )))

    meals: list[Meal] = []
    for when, label, item in sorted(entries, key=lambda e: e[0]):
        if meals and when - meals[-1].when <= CLUSTER_WINDOW:
            meals[-1].items.append(item)
            # 起始列常是「未指定」，後續列若有明確餐別就採用
            if not meals[-1].label or meals[-1].label == "未指定":
                meals[-1].label = label
        else:
            meals.append(Meal(when=when, label=label, items=[item]))
    return meals
