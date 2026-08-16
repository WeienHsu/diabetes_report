"""血糖回診報告的網頁介面。

上傳 CSV → 產生報告 → 線上閱讀或下載 A4 PDF。

認證不在這一層：服務綁 127.0.0.1，唯一入口是 cloudflared，而 Cloudflare Access
在邊緣就把身分驗完了。應用層自己再做一套帳密只會多一個要維護的祕密。
"""

import json
import secrets
from datetime import datetime
from pathlib import Path

from flask import (Flask, abort, redirect, render_template, request,
                   send_file, url_for)

from agp_report.__main__ import ReportError, build_report

from . import pdf

ROOT = Path(__file__).resolve().parent.parent
VAR = ROOT / "var"
UPLOADS, REPORTS = VAR / "uploads", VAR / "reports"

MAX_UPLOAD_MB = 64          # 目前實檔 18MB，留足成長空間
ALLOWED_DAYS = (14, 30, 90)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


def _save(storage, folder: Path, name: str) -> Path | None:
    """存下上傳的檔案。沒選檔案時 filename 是空字串。"""
    if storage is None or not storage.filename:
        return None
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    storage.save(path)
    return path if path.stat().st_size else None


def _meta_of(report_id: str) -> dict | None:
    path = REPORTS / report_id / "meta.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/")
def index():
    return render_template("upload.html", days_options=ALLOWED_DAYS)


@app.post("/report")
def create():
    days = request.form.get("days", type=int, default=14)
    if days not in ALLOWED_DAYS:
        abort(400, "分析期間不在允許範圍內")

    report_id = secrets.token_urlsafe(16)   # 流水號會讓別次報告可被猜到
    upload_dir = UPLOADS / report_id
    glucose = _save(request.files.get("glucose"), upload_dir, "glucose.csv")
    food = _save(request.files.get("food"), upload_dir, "food.csv")

    if glucose is None:
        return render_template("upload.html", days_options=ALLOWED_DAYS, days=days,
                               error="請選擇 LibreView 匯出的血糖 CSV。"), 400

    # 錯誤畫面要能提供「只產出 AGP 頁」，所以記住這次是不是帶了飲食檔
    try:
        html, summary = build_report(
            str(glucose), str(food) if food else None, days,
            toolbar={"pdf": url_for("download", report_id=report_id),
                     "new": url_for("index")},
        )
    except ReportError as exc:
        return render_template("upload.html", days_options=ALLOWED_DAYS, days=days,
                               error=str(exc), had_food=food is not None), 400
    except (UnicodeDecodeError, KeyError, StopIteration, ValueError):
        # 上傳的不是 Libre 匯出檔時，解析會在各種地方炸開
        return render_template(
            "upload.html", days_options=ALLOWED_DAYS, days=days,
            error="這份檔案不像是 LibreView 匯出的 CSV，無法解析。"
                  "請從 LibreView 帳號選單的「匯出資料」重新下載。"), 400

    out_dir = REPORTS / report_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.html").write_text(html, encoding="utf-8")
    (out_dir / "meta.json").write_text(json.dumps({
        "id": report_id,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "start": summary.start.isoformat(),
        "end": summary.end.isoformat(),
        "days": summary.days,
        "coverage_pct": round(summary.coverage_pct, 1),
        "tir_pct": round(summary.tir_pct, 1),
        "meals": summary.meals,
        "hypo_events": summary.hypo_events,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    return redirect(url_for("view", report_id=report_id), code=303)


@app.get("/r/<report_id>")
def view(report_id: str):
    path = REPORTS / secure(report_id) / "report.html"
    if not path.exists():
        abort(404)
    return send_file(path)


@app.get("/r/<report_id>/pdf")
def download(report_id: str):
    folder = REPORTS / secure(report_id)
    if not (folder / "report.html").exists():
        abort(404)
    meta = _meta_of(report_id) or {}
    name = f"血糖回診報告_{meta.get('start', '')[:10]}_{meta.get('end', '')[:10]}.pdf"
    return send_file(pdf.render(folder / "report.html", folder / "report.pdf"),
                     as_attachment=True, download_name=name)


@app.get("/history")
def history():
    rows = sorted((m for m in (_meta_of(d.name) for d in REPORTS.iterdir() if d.is_dir()) if m),
                  key=lambda m: m["generated"], reverse=True) if REPORTS.exists() else []
    return render_template("history.html", rows=rows)


@app.errorhandler(413)
def too_large(_):
    return render_template("upload.html", days_options=ALLOWED_DAYS,
                           error=f"檔案超過 {MAX_UPLOAD_MB} MB 上限。"), 413


def secure(report_id: str) -> str:
    """報告 ID 只會是 token_urlsafe 的字元集，任何其他東西都當作找不到。"""
    if not report_id or not all(c.isalnum() or c in "-_" for c in report_id):
        abort(404)
    return report_id
