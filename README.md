# diabetes_report

把雅培 FreeStyle Libre 的血糖 CSV 與 Google Sheet 的飲食紀錄，合成一份可帶去回診的報告。

線上版：**https://cgm.whtwbrown.com**

第 1 頁是遵循國際共識的 AGP，給醫師秒讀；後面幾頁是原廠 LibreView 做不到的
分析——因為飲食紀錄帶著蛋白質、脂肪與品項名稱，不只是碳水克數。

---

## 每次回診前

1. **匯出血糖**：[LibreView](https://www.libreview.com) → 帳號選單 → 匯出資料
2. **匯出飲食**：Google Sheet「飲食與三大營養素紀錄表」→ **切到「飲食日誌」分頁**
   → 檔案 → 下載 → 逗號分隔值
3. 開 https://cgm.whtwbrown.com ，兩個檔案丟上去，選期間，按產生
4. 報告直接在畫面上讀；要紙本就按「下載 PDF」

手機也走得完——iOS Safari 的檔案選擇器讀得到「檔案」app，兩份匯出檔都在那裡。

> 試算表有兩個分頁，一次只會匯出**當前所在的那一頁**。停在「食物資料庫」
> 匯出的話會解析出 0 餐，網頁會直接告訴你。

---

## 想先看看長什麼樣子

`examples/` 有一組合成的示範資料（14 天，不含任何個人資訊），直接丟上網頁或跑：

```bash
uv run agp-report --glucose examples/glucose.csv --food examples/food.csv \
                  --days 14 --out out/demo.html
```

裡面刻意鋪了低血糖事件、感測器斷線、血糖衝破 300、餐次合併等情境，
指標也落在部分達標、部分未達標，一份就看得到報告的全部樣貌。

**用其他裝置的資料？** `examples/README.md` 是可執行的格式規格——
只要你的來源能轉成那兩個檔案的欄位，這個專案就能用。

---

## 報告怎麼讀

| 頁 | 內容 |
|---|---|
| **P1 醫師版 AGP** | 四個核心指標、時間佔比、24 小時百分位曲線、每日縮圖。固定一張 A4 |
| **P2 時段與每日模式** | 每 2 小時的平均／TIR／速效用量／餐次、低血糖事件清單、每日摘要表 |
| **P3 每日詳細記錄** | 每天的完整曲線、注射與進食時刻、每小時極值 |
| **P4 餐食 × 血糖** | 每餐一張卡：餐後 4 小時曲線、三大營養素、注射劑量 |
| **P5 資料品質** | 涵蓋率、算法、無障礙限制、與 LibreView 官方報告的交叉驗證 |

**第 1 頁的第四格是低血糖事件次數，不是標準差。** SD 等於 CV 乘上平均值，
三者同列時它不帶任何獨立資訊；而一個達標的 TBR 百分比可能藏著睡夢中一小時的低血糖。

**分區表右側三欄是跨列的。** 國際共識的門檻本身重疊：>180 全體 <25%、
其中 >250 單獨 <5%；<70 全體 <4%、其中 <54 單獨 <1%。

**標 `*` 的日期**位於期間頭尾、只涵蓋部分時間，數值不可與整日相比。

### 線上版才有的操作

- **滑過曲線**顯示該時刻的血糖、注射與餐點（手機點一下）
- **四個區段可折疊**。下載的 PDF 一律完整，不受折疊影響
- **P2 的日期可點**，跳到 P3 該日的詳圖；P3 頂端也可下拉只看某一天

---

## 設定

網頁右上「設定」：

| 項目 | 選項 |
|---|---|
| 每日詳細記錄 | 7 / 14 / 21 / 28 / 60 / 90 天（旁邊標了各自的頁數與檔案大小） |
| 餐食反應卡片 | 10 / 20 / 40 / 不限（依時間取最近 N 餐） |
| 每日摘要表 | 7 / 14 / 30 天 |
| 資料保留期限 | 永久 / 30 / 90 / 180 / 365 天 |

保留期限**不會自動清除**，要在設定頁按「清除過期資料」。刪除同時移除報告與
當初上傳的 CSV，**不可復原，沒有垃圾桶**。

---

## 部署

跑在 Raspberry Pi 上，透過 Cloudflare Tunnel 對外。服務綁 `127.0.0.1`，
唯一入口是 cloudflared，身分驗證由 Cloudflare Access 在邊緣完成。

```
瀏覽器 ─HTTPS→ Cloudflare 邊緣（Access 驗證）
                   ↓ Tunnel（出站連線，路由器不開任何 port）
             cloudflared → 127.0.0.1:8090 gunicorn → Flask
```

### 1. 安裝服務

```bash
cd ~/weien/diabetes_report
uv sync --extra web
sudo bash deploy/setup-root.sh
```

腳本會裝 cloudflared、安裝並啟用 systemd 服務、確認 `var/` 權限。可重複執行。

### 2. 建立 Tunnel

```bash
cloudflared tunnel login          # 不要加 sudo
cloudflared tunnel create cgm     # 記下 UUID
```

`create` 把憑證寫在**家目錄**，服務以 root 執行、讀的是 `/etc/cloudflared/`，
要自己複製過去：

```bash
UUID=<剛才那組 UUID>
sudo install -o root -g root -m 600 ~/.cloudflared/$UUID.json /etc/cloudflared/
sudo cp deploy/cloudflared-config.yml /etc/cloudflared/config.yml
sudo nano /etc/cloudflared/config.yml   # 兩處 TUNNEL_ID 換成 UUID

cloudflared tunnel route dns cgm cgm.whtwbrown.com   # 不要加 sudo
sudo cloudflared service install
sudo systemctl restart cloudflared
```

> `login` 與 `route dns` **不能加 sudo**，它們要讀 `~/.cloudflared/cert.pem`。
>
> `route dns` 建立的是**橘雲 proxied 的 CNAME**，與同一個 zone 裡指向 Vercel
> 那筆「必須灰雲」的規則相反，不要照抄。

### 3. 加上認證

Zero Trust → Access → Applications → Add an application → **Self-hosted** →
**Public DNS**

| 欄位 | 值 |
|---|---|
| Subdomain / Domain | `cgm` ／ `whtwbrown.com`（Path 留空） |
| Policy Action | **Allow**（不是 Bypass） |
| Include | Emails → 你的信箱 |

**沒做這步之前，網址是完全公開的。** 驗證：

```bash
curl -sI https://cgm.whtwbrown.com/ | head -3    # 要回 302 導向 *.cloudflareaccess.com
curl -m 3 http://$(hostname -I | awk '{print $1}'):8090/   # 區網直連要失敗
```

### 更新

```bash
git pull && uv sync --extra web && sudo systemctl restart diabetes-report
```

---

## 離線使用

不想開網頁時，同一個引擎有 CLI：

```bash
uv sync
uv run agp-report --glucose data/glucose.csv --food data/food.csv \
                  --days 14 --out out/report.html
```

`--food` 選用，不給就不輸出餐食頁。`--days` 預設 14（AGP 國際標準）。
產出的是單檔自包含 HTML，瀏覽器 `Ctrl+P` 存成 A4 PDF。

線上版與 CLI 共用同一個 `build_report()`，報告本體逐位元組相同。

---

## 開發

```bash
uv run python -m unittest discover -s tests
```

```
agp_report/parse_libre.py   Libre CSV → 血糖／胰島素／碳水／備註事件流
agp_report/parse_food.py    飲食日誌 CSV → 餐次（30 分鐘內合併）
agp_report/metrics.py       AGP 指標、低血糖事件、時段統計、每日摘要與詳圖
agp_report/meals.py         餐次 × 血糖 × 注射 → 餐後反應
agp_report/charts.py        純 SVG 圖表產生器
agp_report/__main__.py      build_report() 與其上的 CLI 包裝
web/                        Flask：上傳、閱讀、PDF、歷史、設定
deploy/                     systemd unit、cloudflared 設定、setup-root.sh
```

---

## 注意

報告只呈現資料型態，**不構成任何胰島素劑量或用藥建議**。定位是帶去回診與
醫療團隊討論的素材。

原始 CSV 檔頭含真實姓名與病歷號，產出的報告是完整健康資料。
`data/`、`out/`、`var/` 都已列入 `.gitignore`。
