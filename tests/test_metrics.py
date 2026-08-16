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
            self.assertEqual(metrics.band_of(value), expected, f"{value} 應屬 {expected}")

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


def _meal(when, carbs, label="午餐"):
    return parse_food.Meal(when=when, items=[parse_food.Item(
        label=label, brand="", name="x", serving="", carbs=carbs, protein=0,
        fat=0, kcal=carbs * 4)])


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

    def test_merged_meal_keeps_each_source_label(self):
        # 20:05 晚餐與 20:30 點心血糖反應分不開，必須合併；但卡片要分行
        # 標出哪些品項屬於哪一餐別，否則整張卡標「晚餐」卻含使用者記為
        # 「點心」的碳水。
        path = self._write([
            "2026-01-01 20:05,晚餐,A,牛肉堡,1個,535,32,28,38.5,\n",
            "2026-01-01 20:05,晚餐,A,雞腿,2塊,370,41,21,3,\n",
            "2026-01-01 20:30,點心,A,香芋派,1個,231,2,11,30.7,\n",
        ])
        got = parse_food.parse(path)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].label, "晚餐＋點心")
        self.assertEqual([(lab, [i.name for i in its]) for lab, its in got[0].groups],
                         [("晚餐", ["牛肉堡", "雞腿"]), ("點心", ["香芋派"])])
        self.assertAlmostEqual(got[0].carbs, 72.2, places=3)

    def test_same_label_rows_stay_in_one_group(self):
        path = self._write([
            "2026-01-01 12:00,午餐,A,主菜,1份,300,20,10,40,\n",
            "2026-01-01 12:05,午餐,B,白飯,200g,280,5,1,62,\n",
        ])
        self.assertEqual([lab for lab, _ in parse_food.parse(path)[0].groups], ["午餐"])

    def test_unspecified_label_yields_to_a_real_one(self):
        path = self._write([
            "2026-01-01 12:00,未指定,A,主菜,1份,300,20,10,40,\n",
            "2026-01-01 12:05,午餐,B,白飯,200g,280,5,1,62,\n",
        ])
        self.assertEqual(parse_food.parse(path)[0].label, "午餐")


class TestHypoEvents(unittest.TestCase):
    def test_fourteen_minutes_is_not_an_event(self):
        # 兩筆間隔 15 分鐘才構成 15 分鐘，單筆低讀數跨度是 0
        self.assertEqual(metrics.hypo_events(series([60], step_min=15)), [])

    def test_fifteen_minutes_is_an_event(self):
        got = metrics.hypo_events(series([60, 65]))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].minutes, 15)
        self.assertEqual(got[0].nadir, 60)

    def test_a_single_recovery_splits_one_run_into_two(self):
        # 中間回升到 70 以上就斷開；兩段各自要夠長才算數
        got = metrics.hypo_events(series([60, 65, 120, 55, 50, 68]))
        self.assertEqual([e.minutes for e in got], [15, 30])
        self.assertEqual([e.nadir for e in got], [60, 50])

    def test_sensor_gap_does_not_merge_two_events(self):
        # 貼片脫落數小時後回來，前後都偏低——不可捏造成一次橫跨數小時的事件
        early = series([60, 62], start=datetime(2026, 1, 1, 3, 0))
        late = series([58, 56], start=datetime(2026, 1, 1, 9, 0))
        got = metrics.hypo_events(early + late)
        self.assertEqual(len(got), 2)
        self.assertEqual([e.minutes for e in got], [15, 15])

    def test_boundary_seventy_is_not_low(self):
        self.assertEqual(metrics.hypo_events(series([70, 70, 70])), [])

    def test_bolus_before_counts_only_the_preceding_four_hours(self):
        readings = series([60, 62], start=datetime(2026, 1, 1, 12, 0))
        insulin = [
            (datetime(2026, 1, 1, 7, 30), 9.0),   # 4.5 小時前，太早
            (datetime(2026, 1, 1, 10, 0), 6.0),   # 2 小時前，算
            (datetime(2026, 1, 1, 12, 30), 3.0),  # 事件開始後，不算
        ]
        self.assertEqual(metrics.hypo_events(readings, insulin)[0].bolus_before, 6.0)


class TestTimeBlocks(unittest.TestCase):
    def test_block_boundaries_are_half_open(self):
        # 14:00 屬 14-16，16:00 屬 16-18
        readings = [(datetime(2026, 1, 1, 14, 0), 100.0),
                    (datetime(2026, 1, 1, 16, 0), 300.0)]
        got = {b.hour: b for b in metrics.time_blocks(readings, [], [], days=1)}
        self.assertEqual(got[14].mean, 100.0)
        self.assertEqual(got[16].mean, 300.0)

    def test_insulin_is_averaged_over_the_period(self):
        readings = [(datetime(2026, 1, d, 8, 0), 150.0) for d in (1, 2)]
        insulin = [(datetime(2026, 1, 1, 8, 30), 10.0), (datetime(2026, 1, 2, 9, 0), 6.0)]
        block = metrics.time_blocks(readings, insulin, [], days=2)[0]
        self.assertEqual(block.hour, 8)
        self.assertEqual(block.insulin_per_day, 8.0)   # (10 + 6) / 2 天

    def test_empty_blocks_are_omitted_not_zeroed(self):
        # 沒有讀數的時段留白，不能報 0 —— 0 會被讀成「血糖是 0」
        got = metrics.time_blocks([(datetime(2026, 1, 1, 3, 0), 90.0)], [], [], days=1)
        self.assertEqual([b.hour for b in got], [2])


class TestDailyDetails(unittest.TestCase):
    def test_readings_land_in_the_right_fifteen_minute_slot(self):
        readings = [(datetime(2026, 1, 1, 0, 7), 100.0),    # 第 0 格
                    (datetime(2026, 1, 1, 0, 15), 120.0),   # 第 1 格
                    (datetime(2026, 1, 1, 23, 45), 140.0)]  # 第 95 格
        d = metrics.daily_details(readings, [], [])[0]
        self.assertEqual(d.slots[0], 100.0)
        self.assertEqual(d.slots[1], 120.0)
        self.assertEqual(d.slots[95], 140.0)
        self.assertEqual(d.readings, 3)

    def test_hours_without_readings_stay_none(self):
        # 空白代表沒有讀數。填 0 會被讀成「血糖是 0」
        d = metrics.daily_details([(datetime(2026, 1, 1, 5, 0), 90.0)], [], [])[0]
        self.assertEqual(d.hourly[5], (90.0, 90.0))
        self.assertIsNone(d.hourly[0])
        self.assertIsNone(d.hourly[23])

    def test_hourly_takes_max_and_min_of_the_four_slots(self):
        readings = series([120, 90, 200, 150], start=datetime(2026, 1, 1, 8, 0))
        d = metrics.daily_details(readings, [], [])[0]
        self.assertEqual(d.hourly[8], (200, 90))

    def test_limit_keeps_the_most_recent_days(self):
        readings = [(datetime(2026, 1, day, 8, 0), 150.0) for day in range(1, 11)]
        got = metrics.daily_details(readings, [], [], limit=3)
        self.assertEqual([d.day.day for d in got], [8, 9, 10])

    def test_events_from_other_days_are_not_mixed_in(self):
        readings = [(datetime(2026, 1, d, 8, 0), 150.0) for d in (1, 2)]
        insulin = [(datetime(2026, 1, 1, 8, 30), 10.0), (datetime(2026, 1, 2, 9, 0), 6.0)]
        scans = [(datetime(2026, 1, 2, 10, 0), 160.0)]
        got = metrics.daily_details(readings, insulin, scans)
        self.assertEqual(got[0].insulin, [(510, 10.0)])
        self.assertEqual(got[0].scans, [])
        self.assertEqual(got[1].insulin, [(540, 6.0)])
        self.assertEqual(got[1].scans, [600])

    def test_insulin_by_hour_sums_within_the_hour(self):
        readings = [(datetime(2026, 1, 1, 8, 0), 150.0)]
        insulin = [(datetime(2026, 1, 1, 8, 5), 4.0), (datetime(2026, 1, 1, 8, 50), 3.0),
                   (datetime(2026, 1, 1, 9, 10), 2.0)]
        got = metrics.daily_details(readings, insulin, [])[0].insulin_by_hour()
        self.assertEqual(got, {8: 7.0, 9: 2.0})


class TestDailySummary(unittest.TestCase):
    def test_one_row_per_day_with_weekday(self):
        readings = (series([100, 200], start=datetime(2026, 1, 1, 8, 0))
                    + series([80, 90], start=datetime(2026, 1, 2, 8, 0)))
        got = metrics.daily_summary(readings, [], [])
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0].mean, 150.0)
        self.assertEqual(got[0].highest, 200)
        self.assertEqual(got[1].lowest, 80)
        self.assertEqual(got[0].weekday, "四")   # 2026-01-01 是週四

    def test_boundary_days_are_marked_partial(self):
        # 期間 08:00 起、次日 10:00 止 —— 兩天都不是完整一日，數值不可與整日相比
        readings = (series([150] * 4, start=datetime(2026, 1, 1, 8, 0))
                    + series([150] * 4, start=datetime(2026, 1, 2, 9, 0)))
        self.assertEqual([r.partial for r in metrics.daily_summary(readings, [], [])],
                         [True, True])

    def test_a_full_day_is_not_partial(self):
        readings = series([150] * metrics.BINS_PER_DAY, start=datetime(2026, 1, 1, 0, 0))
        self.assertEqual([r.partial for r in metrics.daily_summary(readings, [], [])], [False])

    def test_hypo_events_are_attributed_to_their_start_day(self):
        # 23:50 開始、跨過午夜的事件算前一天
        readings = series([60, 62, 64], start=datetime(2026, 1, 1, 23, 50))
        events = metrics.hypo_events(readings)
        self.assertEqual(len(events), 1)
        got = {d.day.day: d.hypo_count for d in metrics.daily_summary(readings, [], events)}
        self.assertEqual(got[1], 1)
        self.assertEqual(got[2], 0)


if __name__ == "__main__":
    unittest.main()
