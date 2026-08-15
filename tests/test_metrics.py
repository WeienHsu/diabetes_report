"""指標引擎的手算比對。

用刻意設計的小樣本，讓每個指標都能用心算驗證，而不是拿真實資料的
輸出當作「正確答案」自我循環。
"""

import unittest
from datetime import datetime, timedelta

from agp_report import metrics, parse_food, meals


def series(values, start=datetime(2026, 1, 1), step_min=15):
    return [(start + timedelta(minutes=i * step_min), v) for i, v in enumerate(values)]


class TestBands(unittest.TestCase):
    def test_band_boundaries_are_half_open(self):
        # 邊界值必須落在上一區，否則 70 與 180 會被重複計入兩區
        cases = {53.9: "very_low", 54: "low", 69.9: "low", 70: "target",
                 179.9: "target", 180: "high", 250: "very_high"}
        for value, expected in cases.items():
            self.assertEqual(metrics._band_of(value), expected, f"{value} 應屬 {expected}")

    def test_percentages_sum_to_100(self):
        m = metrics.compute(series([40, 60, 100, 200, 300]), days=1)
        self.assertAlmostEqual(sum(m.band_pct.values()), 100.0, places=6)


class TestSummaryStats(unittest.TestCase):
    def test_mean_and_gmi(self):
        # 平均 100 → GMI = 3.31 + 0.02392*100 = 5.702
        m = metrics.compute(series([100] * 8), days=1)
        self.assertAlmostEqual(m.mean, 100.0)
        self.assertAlmostEqual(m.gmi_pct, 5.702, places=3)
        self.assertAlmostEqual(m.sd, 0.0)
        self.assertAlmostEqual(m.cv_pct, 0.0)

    def test_coverage_reflects_missing_readings(self):
        # 一天應有 96 筆，只給 48 筆 → 涵蓋率 50%
        m = metrics.compute(series([120] * 48), days=1)
        self.assertAlmostEqual(m.coverage_pct, 50.0)

    def test_all_in_target(self):
        m = metrics.compute(series([80, 120, 179]), days=1)
        self.assertAlmostEqual(m.band_pct["target"], 100.0)


class TestPercentile(unittest.TestCase):
    def test_interpolates_between_samples(self):
        self.assertAlmostEqual(metrics._percentile([10, 20, 30, 40], 0.5), 25.0)
        self.assertAlmostEqual(metrics._percentile([10, 20, 30, 40], 0.0), 10.0)
        self.assertAlmostEqual(metrics._percentile([10, 20, 30, 40], 1.0), 40.0)

    def test_single_value(self):
        self.assertAlmostEqual(metrics._percentile([7], 0.5), 7.0)


def _meal(when, carbs, label=""):
    return parse_food.Meal(when=when, label=label, items=[parse_food.Item(
        brand="", name="x", serving="", carbs=carbs, protein=0, fat=0, kcal=carbs * 4)])


class TestMealResponse(unittest.TestCase):
    # 讀數對齊整點，進食時間刻意錯開，讓基準值取自明確的餐前讀數
    READINGS_START = datetime(2026, 1, 1, 12, 0)
    MEAL_AT = datetime(2026, 1, 1, 12, 7)

    def test_no_rise_is_flagged(self):
        # 餐後一路下降：peak 只是視窗內最大值，不該當成餐後高峰
        historic = series([200, 190, 180, 170, 160, 150, 140, 130, 120, 110],
                          start=self.READINGS_START)
        r = meals.analyse(_meal(self.MEAL_AT, 50), historic, [])
        self.assertTrue(r.enough_data)
        self.assertEqual(r.baseline, 200)
        self.assertLess(r.delta, 0)
        self.assertFalse(r.rose)

    def test_rise_is_measured_from_premeal_baseline(self):
        # 餐前 100，第 4 筆讀數（12:45，餐後第 38 分鐘）達 200 後回落
        historic = series([100, 140, 180, 200, 180, 160, 140, 120, 110, 105],
                          start=self.READINGS_START)
        r = meals.analyse(_meal(self.MEAL_AT, 50), historic, [])
        self.assertEqual(r.baseline, 100)
        self.assertEqual(r.peak, 200)
        self.assertEqual(r.peak_min, 38)
        self.assertTrue(r.rose)
        self.assertAlmostEqual(r.delta, 100)

    def test_short_window_is_insufficient(self):
        # 餐後只有 1 小時資料，不足以判斷反應
        historic = series([100, 120, 140, 150], start=self.READINGS_START)
        r = meals.analyse(_meal(self.MEAL_AT, 50), historic, [])
        self.assertFalse(r.enough_data)


class TestMealClustering(unittest.TestCase):
    """同一頓飯若被拆成多餐，會共用同一劑胰島素並重複計算同一個峰值。"""

    def _write(self, rows):
        import tempfile
        fh = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                         encoding="utf-8", newline="")
        fh.write("日期時間,餐別,品牌,產品名稱,攝取份量,"
                 "熱量 (kcal),蛋白質 (g),脂肪 (g),碳水化合物 (g),備註\n")
        fh.writelines(rows)
        fh.close()
        return fh.name

    def test_nearby_entries_merge_into_one_meal(self):
        path = self._write([
            "2026-01-01 12:00,午餐,A,主菜,1份,300,20,10,40,\n",
            "2026-01-01 12:05,午餐,B,白飯,200g,280,5,1,62,\n",   # 5 分鐘內 → 同餐
            "2026-01-01 15:00,點心,C,餅乾,1包,100,1,5,15,\n",     # 3 小時後 → 另一餐
        ])
        got = parse_food.parse(path)
        self.assertEqual(len(got), 2)
        self.assertAlmostEqual(got[0].carbs, 102)
        self.assertEqual(got[0].title, "主菜＋白飯")
        self.assertAlmostEqual(got[1].carbs, 15)

    def test_entries_beyond_window_stay_separate(self):
        path = self._write([
            "2026-01-01 12:00,午餐,A,主菜,1份,300,20,10,40,\n",
            "2026-01-01 12:31,點心,B,蛋糕,1塊,300,3,12,45,\n",   # 超過 30 分鐘
        ])
        self.assertEqual(len(parse_food.parse(path)), 2)

    def test_unspecified_label_yields_to_a_real_one(self):
        path = self._write([
            "2026-01-01 12:00,未指定,A,主菜,1份,300,20,10,40,\n",
            "2026-01-01 12:05,午餐,B,白飯,200g,280,5,1,62,\n",
        ])
        self.assertEqual(parse_food.parse(path)[0].label, "午餐")


if __name__ == "__main__":
    unittest.main()
