# diabetes_report

把雅培 FreeStyle Libre 的血糖 CSV 與 Google Sheet 的飲食紀錄，合成一份可帶去回診的 A4 報告。

第 1 頁是遵循國際共識版面的 AGP，給醫師秒讀；第 2 頁起是原廠 LibreView 做不到的
餐食×血糖分析——因為飲食紀錄帶著蛋白質、脂肪與品項名稱，不只是碳水克數。

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

第四個指標是**低血糖事件次數**，不是標準差——SD 等於 CV 乘上平均值，
三者同時列出時它不帶任何獨立資訊，而一個達標的 TBR 百分比可能藏著睡夢中
一小時的低血糖。分區表的「判定值」欄是達標實際比較的累計分區
（「低」比的是 <70 全體），與同列的單一分區佔比不同。

**第 2 頁 — 時段與每日模式。** 每 2 小時的平均／TIR／速效用量／餐次，
框線標出本期間平均值最高的時段；低血糖事件逐次列出；每日一列的摘要表。
這頁回答的是「問題集中在一天中的哪個時段、哪一天」。

標 `*` 的日期位於期間頭尾、只涵蓋部分時間，數值不可與整日相比。

**第 3 頁起 — 餐食。** 每張卡片是一餐：黑線為餐後 4 小時血糖，直線為進食時刻，
藍點為注射時間與劑量。用途是找出「同一種食物反應都很糟」的模式。

**最後一頁 — 誠實聲明。** 涵蓋率、樣本數、算法、配色的無障礙限制，
以及與 LibreView 官方報告的交叉驗證。醫師質疑數字怎麼來的就翻這頁。

---

## 常見狀況

| 狀況 | 原因 |
|---|---|
| 餐次 0 | 幾乎都是匯出錯分頁，確認是「飲食日誌」 |
| 中文變方框 | 把 HTML 搬到沒有中文字型的機器才會發生 |
| 想改版面 | 改 `templates/report.html.j2`（排版）或 `charts.py`（圖表），重跑即可 |

---

## 線上版

網頁介面在 `web/`，跑在 Pi 上、透過 Cloudflare Tunnel 對外，網址
`https://cgm.whtwbrown.com`。上傳兩份 CSV 就直接讀報告、一鍵下載 PDF，
不需要 SSH 也不需要指令列。

```bash
uv sync --extra web                       # 裝 flask 與 gunicorn
uv run gunicorn -w 2 -b 127.0.0.1:8090 -t 180 web.app:app   # 本機試跑
```

線上版與 CLI 共用同一個 `build_report()`，產出的報告本體逐位元組相同，
只多一條列印時會隱藏的工具列。PDF 是伺服器端 headless chromium 吃
`file://` 轉的——與人工 Ctrl+P 走同一條 `@media print` 樣式。

**資料存放**：`var/uploads/<id>/` 與 `var/reports/<id>/`，權限 700，已列入
`.gitignore`。目前設定為全部保留，**尚未決定保留期限**——在決定之前，
Pi 上會持續累積完整的健康資料檔。

### 部署步驟

**1. 需要 root 的部分**（sudo 需要密碼，無法代跑）

```bash
cd ~/weien/diabetes_report
uv sync --extra web
sudo bash deploy/setup-root.sh
```

腳本會裝 cloudflared、安裝並啟用 systemd 服務、確認 `var/` 權限，可重複執行。

**2. 建立 Tunnel**（需要你的 Cloudflare 帳號）

```bash
cloudflared tunnel login          # 會給一個網址，用瀏覽器授權 whtwbrown.com
cloudflared tunnel create cgm     # 記下輸出的 UUID
sudo cp deploy/cloudflared-config.yml /etc/cloudflared/config.yml
sudo nano /etc/cloudflared/config.yml   # 把兩處 TUNNEL_ID 換成那組 UUID
sudo chmod 600 /etc/cloudflared/*.json  # 憑證檔
cloudflared tunnel route dns cgm cgm.whtwbrown.com
sudo cloudflared service install
sudo systemctl restart cloudflared
```

> `route dns` 會自動建立一筆**橘雲（proxied）的 CNAME**。這與
> `lala_dashboard` 指向 Vercel 那筆「必須灰雲 DNS only」的規則**相反**，
> 兩筆記錄在同一個 zone 裡，不要照抄。

**3. 加上認證**（Cloudflare 儀表板，不能用指令）

Zero Trust → Access → Applications → Add an application → Self-hosted

| 欄位 | 值 |
|---|---|
| Application name | `血糖回診報告` |
| Session duration | 依習慣，建議 1 週 |
| Public hostname | `cgm.whtwbrown.com` |
| Policy name | `只有我` |
| Action | Allow |
| Include | Emails → 你的信箱 |

登入方式在 Settings → Authentication，預設的 **One-time PIN** 就是 email
一次性驗證碼，不需要額外設定 identity provider。

**沒做這一步之前，網址是完全公開的。**

### 驗收

```bash
systemctl status diabetes-report cloudflared      # 兩個都要 active

# 區網直連應該要失敗——確認沒有繞過 Access 的路徑
curl -m 3 http://$(hostname -I | awk '{print $1}'):8090/
```

最後用手機關掉 Wi-Fi、走行動網路開 `https://cgm.whtwbrown.com`：
應該被 Access 擋下要求驗證，通過後才看得到上傳頁。

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
                 低血糖事件、每 2 小時時段統計、每日摘要
meals.py         餐次 × 血糖軌跡 × 注射 → 餐後反應
charts.py        純 SVG 圖表產生器
__main__.py      build_report() 報告組裝，與其上的 CLI 包裝

web/app.py       Flask：上傳、產生、閱讀、下載 PDF、歷史清單
web/pdf.py       headless chromium 轉 A4 PDF
deploy/          systemd unit、cloudflared 設定範本、setup-root.sh
```

---

## 注意

報告只呈現資料型態，**不構成任何胰島素劑量或用藥建議**。定位是帶去回診與
醫療團隊討論的素材。

`data/` 與 `out/` 已列入 .gitignore——原始 CSV 檔頭含真實姓名與病歷號，
產出的報告是完整健康資料。上傳前請先確認 `git status`。
