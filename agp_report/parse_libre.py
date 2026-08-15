"""解析雅培 FreeStyle Libre（輔理善瞬感）匯出的 CSV。

匯出檔第 1 列是病患抬頭，第 2 列才是欄位名稱，第 3 列起為資料。
記錄類型欄決定每一列的意義，同一列只會有對應類型的欄位有值。
"""

import csv
from dataclasses import dataclass, field
from datetime import datetime

# 記錄類型（「記錄類型」欄）
HISTORIC = "0"  # 歷史葡萄糖，感測器每 15 分鐘自動記錄
SCAN = "1"      # 掃描葡萄糖，使用者主動刷卡的即時值
RAPID = "4"     # 速效胰島素
FOOD = "5"      # 碳水化合物
NOTE = "6"      # 事件標記／文字備註

TIMESTAMP_FMT = "%Y-%m-%d %H:%M"


@dataclass
class LibreExport:
    """一份匯出檔的內容，已依記錄類型拆開。"""

    # AGP 血糖軌跡只能用 historic：scan 與 historic 在時間上重疊，
    # 兩者相加會重複計數，涵蓋率會算出超過 100% 的假象。
    historic: list[tuple[datetime, float]] = field(default_factory=list)
    scans: list[tuple[datetime, float]] = field(default_factory=list)
    insulin: list[tuple[datetime, float]] = field(default_factory=list)
    long_insulin: list[tuple[datetime, float]] = field(default_factory=list)
    carbs: list[tuple[datetime, float]] = field(default_factory=list)
    notes: list[tuple[datetime, str]] = field(default_factory=list)
    patient: str = ""


def _num(raw: str) -> float | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def parse(path: str) -> LibreExport:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = csv.reader(fh)
        title = next(rows, [])
        header = next(rows, [])
        col = {name: i for i, name in enumerate(header)}

        out = LibreExport(patient=title[4] if len(title) > 4 else "")

        def value(row: list[str], name: str) -> float | None:
            i = col.get(name)
            return _num(row[i]) if i is not None and i < len(row) else None

        for row in rows:
            if len(row) <= col["記錄類型"]:
                continue
            kind = row[col["記錄類型"]].strip()
            try:
                when = datetime.strptime(row[col["裝置時間戳記"]].strip(), TIMESTAMP_FMT)
            except ValueError:
                continue

            if kind == HISTORIC:
                mgdl = value(row, "歷史葡萄糖 mg/dL")
                if mgdl is not None:
                    out.historic.append((when, mgdl))
            elif kind == SCAN:
                mgdl = value(row, "掃描葡萄糖 mg/dL")
                if mgdl is not None:
                    out.scans.append((when, mgdl))
            elif kind == RAPID:
                units = value(row, "速效胰島素（單位）")
                if units is not None:
                    out.insulin.append((when, units))
            elif kind == FOOD:
                grams = value(row, "碳水化合物（克）")
                if grams is not None:
                    out.carbs.append((when, grams))

            # 長效胰島素與文字備註可能掛在任何類型的列上，獨立檢查。
            units = value(row, "長效胰島素（單位）")
            if units is not None:
                out.long_insulin.append((when, units))
            i = col.get("備註")
            if i is not None and i < len(row) and row[i].strip():
                out.notes.append((when, row[i].strip()))

    for series in (out.historic, out.scans, out.insulin, out.long_insulin, out.carbs):
        series.sort(key=lambda item: item[0])
    out.notes.sort(key=lambda item: item[0])
    return out
