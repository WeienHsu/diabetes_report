"""產生示範用的 CSV。

資料全部是合成的——不是把真實紀錄去識別化，而是用固定亂數種子從頭造出來的。
留著這支腳本是為了讓這件事可被驗證，也方便日後調整示範情境。

    uv run python examples/generate.py

數值刻意落在「部分達標、部分未達標」的區間，示範報告才會同時出現 ✓ 與 ✗。

刻意鋪了幾個情境，讓示範報告涵蓋各項功能：
  · 第 5 天凌晨低血糖（連續 45 分鐘 <70）→ 低血糖事件
  · 第 9 天早上感測器斷線 6 小時       → 曲線不連線、涵蓋率下降
  · 第 11 天午餐後衝到 340             → >250 分區與橘底格
  · 第 13 天晚餐與點心相隔 25 分鐘      → 餐次合併、分行標示餐別
"""

import csv
import math
import random
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
START = datetime(2026, 3, 2)          # 週一
DAYS = 14
SEED = 20260302

GLUCOSE_HEADER = [
    "裝置", "序號", "裝置時間戳記", "記錄類型", "歷史葡萄糖 mg/dL", "掃描葡萄糖 mg/dL",
    "非數值速效胰島素", "速效胰島素（單位）", "非數值食品", "碳水化合物（克）",
    "碳水化合物（份）", "非數值長效胰島素", "長效胰島素（單位）", "備註",
    "葡萄糖試紙 mg/dL", "血酮 mmol/L", "餐時胰島素（單位）", "校正胰島素（單位）",
    "使用者變更胰島素（單位）", "歷史資料的酮體 mmol/L", "掃描酮體 mmol/L",
]
DEVICE, SERIAL = "FreeStyle LibreLink", "DEMO-0000"

# (時, 分, 餐別, 碳水, 蛋白, 脂肪, 熱量, 品名)
MEALS = [
    (8, 10, "早餐", 45, 18, 12, 380, "全麥吐司＋水煮蛋＋無糖豆漿"),
    (12, 30, "午餐", 70, 32, 22, 640, "雞腿飯＋燙青菜"),
    (19, 0, "晚餐", 58, 30, 18, 520, "鮭魚＋糙米飯＋味噌湯"),
]


def curve(day: int, minute: int, rng: random.Random) -> float:
    """一天的血糖形狀：黎明上升 + 三餐波峰 + 個體雜訊。"""
    hour = minute / 60
    value = 134 + 12 * math.sin((hour - 4) / 24 * 2 * math.pi)
    value += 22 * math.exp(-((hour - 6.0) ** 2) / 2.0)          # 黎明現象
    for peak_hour, height in ((9.4, 92), (13.8, 118), (20.3, 104)):
        spread = 2.2 if peak_hour < 12 else 2.6
        value += height * math.exp(-((hour - peak_hour) ** 2) / spread)
    value += 14 * math.sin((day * 1.7 + hour) / 5)              # 日間差異
    return value + rng.gauss(0, 6)


def scripted(day: int, minute: int, value: float) -> float | None:
    """刻意鋪陳的情境。回傳 None 代表該筆讀數不存在（感測器斷線）。"""
    hour = minute / 60
    if day == 4 and 3.0 <= hour <= 3.9:                          # 凌晨低血糖
        return 58 + (hour - 3.0) * 14
    if day == 8 and 8.0 <= hour < 14.0:                          # 感測器斷線
        return None
    if day == 10 and 13.5 <= hour <= 16.0:                       # 大幅超標
        return value + 95 * math.exp(-((hour - 14.4) ** 2) / 1.4)
    return value


def main() -> None:
    rng = random.Random(SEED)
    rows, food = [], []

    for day in range(DAYS):
        date = START + timedelta(days=day)
        for slot in range(96):
            minute = slot * 15
            when = date + timedelta(minutes=minute)
            value = scripted(day, minute, curve(day, minute, rng))
            if value is None:
                continue
            row = [""] * len(GLUCOSE_HEADER)
            row[0], row[1] = DEVICE, SERIAL
            row[2] = when.strftime("%Y-%m-%d %H:%M")
            row[3], row[4] = "0", str(round(max(40, min(400, value))))
            rows.append(row)

        # 掃描：每天在餐前後各刷幾次
        for hour in (7.8, 9.5, 12.2, 14.0, 16.5, 18.8, 20.5, 22.4):
            when = date + timedelta(minutes=int(hour * 60))
            value = scripted(day, int(hour * 60), curve(day, int(hour * 60), rng))
            if value is None:
                continue
            row = [""] * len(GLUCOSE_HEADER)
            row[0], row[1] = DEVICE, SERIAL
            row[2] = when.strftime("%Y-%m-%d %H:%M")
            row[3], row[5] = "1", str(round(max(40, min(400, value))))
            rows.append(row)

        for hour, minute, label, carbs, protein, fat, kcal, name in MEALS:
            # 第 13 天的晚餐延後，好讓後面的點心落在 30 分鐘的合併窗內
            if day == 12 and label == "晚餐":
                hour, minute = 19, 30
            when = date + timedelta(hours=hour, minutes=minute)
            food.append([when.strftime("%Y-%m-%d %H:%M"), label, "示範",
                         name, "1份", kcal, protein, fat, carbs, ""])

            units = round(carbs / 8 + rng.uniform(-1, 1))
            shot = when - timedelta(minutes=rng.choice([0, 5, 10]))
            row = [""] * len(GLUCOSE_HEADER)
            row[0], row[1] = DEVICE, SERIAL
            row[2] = shot.strftime("%Y-%m-%d %H:%M")
            row[3], row[7] = "4", f"{units}"
            rows.append(row)

        if day == 12:      # 晚餐 19:30、點心 19:55 → 相隔 25 分鐘會被合併
            when = date + timedelta(hours=19, minutes=55)
            food.append([when.strftime("%Y-%m-%d %H:%M"), "點心", "示範",
                         "黑巧克力", "2 片", 110, 1.5, 8, 9, ""])
            row = [""] * len(GLUCOSE_HEADER)
            row[0], row[1] = DEVICE, SERIAL
            row[2] = (when + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M")
            row[3], row[7] = "4", "2"
            rows.append(row)

    rows.sort(key=lambda r: (r[2], r[3]))
    generated = (START + timedelta(days=DAYS)).strftime("%Y-%m-%d %H:%M")

    with (HERE / "glucose.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["葡萄糖資料", "產生於", f"{generated} UTC", "產生者", "示範使用者"])
        writer.writerow(GLUCOSE_HEADER)
        writer.writerows(rows)

    with (HERE / "food.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["日期時間", "餐別", "品牌", "產品名稱", "攝取份量", "熱量 (kcal)",
                         "蛋白質 (g)", "脂肪 (g)", "碳水化合物 (g)", "備註"])
        writer.writerows(sorted(food))

    print(f"glucose.csv  {len(rows)} 列")
    print(f"food.csv     {len(food)} 列")


if __name__ == "__main__":
    main()
