# diabetes_report

把雅培 FreeStyle Libre 的血糖 CSV 與 Google Sheet 的飲食紀錄，合成一份可帶去回診的 A4 報告。

第 1 頁是遵循國際共識版面的 AGP，給醫師秒讀；第 2 頁起是原廠 LibreView 做不到的
餐食×血糖分析——因為飲食紀錄帶著蛋白質、脂肪與品項名稱，不只是碳水克數。

設計理由、各階段驗證方式與過程中修正的錯誤，見 [`docs/實作計劃.md`](docs/實作計劃.md)。

---

## 安裝

```bash
cd ~/weien/diabetes_report
uv sync
```

建立 `.venv`、依 `uv.lock` 裝好相依、並把本專案裝成可執行指令。
只有 Jinja2 一個直接相依，其餘全用標準函式庫。

---

## 每次回診前的流程

### 1. 匯出血糖資料

[LibreView](https://www.libreview.com) → 帳號選單 → 匯出資料 → 下載 CSV。

```bash
mv ~/Downloads/*glucose*.csv data/glucose.csv
```

### 2. 匯出飲食資料

Google Sheet「飲食與三大營養素紀錄表」→ **切到「飲食日誌」分頁** →
檔案 → 下載 → 逗號分隔值。

> 試算表有兩個分頁，一次只會匯出**當前所在的那一頁**。停在「食物資料庫」
> 匯出的話，程式會解析出 0 餐。

```bash
mv ~/Downloads/*飲食*.csv data/food.csv
```

### 3. 產報告

```bash
uv run agp-report \
    --glucose data/glucose.csv \
    --food    data/food.csv \
    --days 14 \
    --out out/report.html
```

```
已產出 out/report.html（58 KB）
  期間 2026-08-01 – 2026-08-15　涵蓋率 91%　TIR 53.3%　餐次 8
```

先看這行確認資料吃對了。**涵蓋率低於 70%** 代表感測器有大段時間沒貼或沒掃到，
AGP 統計會失真，報告第 3 頁會標紅字。

### 4. 印出來

瀏覽器開啟 `out/report.html` → `Ctrl+P` → 紙張選 A4 → 存 PDF 或直接列印。

---

## 參數

| 參數 | 說明 |
|---|---|
| `--glucose` | **必填**，Libre 匯出的 CSV |
| `--food` | 選用。不給就只出 AGP 頁與資料品質頁，餐食頁整頁不輸出 |
| `--days` | 預設 14（AGP 國際標準）。回診通常看 14 或 90 |
| `--out` | 輸出路徑，資料夾不存在會自動建立 |

```bash
# 季度趨勢
uv run agp-report --glucose data/glucose.csv --food data/food.csv \
                  --days 90 --out out/季度.html

# 只要血糖不要飲食
uv run agp-report --glucose data/glucose.csv --out out/僅血糖.html
```

`--days 90` 時每日縮圖仍只顯示最近 14 天（91 個縮圖會把第 1 頁撐爆），
但所有指標都是完整 90 天算的，報告上會註明。

---

## 怎麼讀這份報告

**第 1 頁 — 給醫師。** 四個核心指標、時間佔比、24 小時百分位曲線、每日縮圖，
固定一張 A4。

**第 2 頁起 — 給自己。** 每張卡片是一餐：黑線為餐後 4 小時血糖，直線為進食時刻，
藍點為注射時間與劑量。用途是找出「同一種食物反應都很糟」的模式。

**最後一頁 — 誠實聲明。** 涵蓋率、樣本數、算法、配色的無障礙限制。
醫師質疑數字怎麼來的就翻這頁。

---

## 常見狀況

| 狀況 | 原因 |
|---|---|
| 餐次 0 | 幾乎都是匯出錯分頁，確認是「飲食日誌」 |
| 中文變方框 | 把 HTML 搬到沒有中文字型的機器才會發生 |
| 想改版面 | 改 `templates/report.html.j2`（排版）或 `charts.py`（圖表），重跑即可 |

---

## 開發

```bash
uv run python -m unittest discover -s tests
```

相依定義在 `pyproject.toml`，`uv.lock` 鎖定完整相依樹（含間接相依），
兩者都要進版控才能重現環境。

```
parse_libre.py   Libre CSV → 血糖／胰島素／碳水／備註事件流
parse_food.py    飲食日誌 CSV → 餐次（鄰近 30 分鐘內合併、營養素加總）
metrics.py       AGP 指標：涵蓋率、GMI、CV、五區間佔比、24h 百分位曲線
meals.py         餐次 × 血糖軌跡 × 注射 → 餐後反應
charts.py        純 SVG 圖表產生器
__main__.py      CLI 與報告組裝
```

---

## 注意

報告只呈現資料型態，**不構成任何胰島素劑量或用藥建議**。定位是帶去回診與
醫療團隊討論的素材。

`data/` 與 `out/` 已列入 .gitignore——原始 CSV 檔頭含真實姓名與病歷號，
產出的報告是完整健康資料。上傳前請先確認 `git status`。
