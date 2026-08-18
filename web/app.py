"""血糖回診報告的網頁介面。

上傳 CSV → 產生報告 → 線上閱讀或下載 A4 PDF。

認證不在這一層：服務綁 127.0.0.1，唯一入口是 cloudflared，而 Cloudflare Access
在邊緣就把身分驗完了。應用層自己再做一套帳密只會多一個要維護的祕密。
"""

import json
import secrets
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from flask import (Flask, abort, flash, redirect, render_template, request,
                   send_file, url_for)

from agp_report.__main__ import ReportError, build_report

from . import pdf, settings as cfg

ROOT = Path(__file__).resolve().parent.parent
VAR = ROOT / "var"
UPLOADS, REPORTS = VAR / "uploads", VAR / "reports"
SETTINGS_PATH = VAR / "settings.json"

MAX_UPLOAD_MB = 64          # 目前實檔 18MB，留足成長空間
ALLOWED_DAYS = (14, 30, 90)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
# flash 需要 session。金鑰只用於提示訊息，重啟後失效無所謂；
# 認證本身在 Cloudflare Access，這裡沒有登入狀態要保護。
app.secret_key = secrets.token_bytes(32)


def _save(storage, folder: Path, stem: str) -> Path | None:
    """存下上傳的檔案。沒選檔案時 filename 是空字串。

    副檔名照使用者的檔案帶（飲食紀錄可能是 xlsx），但只認得這兩種；
    其餘一律不帶副檔名，免得上傳的檔名決定了磁碟上的路徑。解析格式本身
    是看檔頭而不是副檔名，所以不帶也不影響。
    """
    if storage is None or not storage.filename:
        return None
    folder.mkdir(parents=True, exist_ok=True)
    suffix = Path(storage.filename).suffix.lower()
    path = folder / (stem + (suffix if suffix in (".csv", ".xlsx") else ""))
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
    glucose = _save(request.files.get("glucose"), upload_dir, "glucose")
    food = _save(request.files.get("food"), upload_dir, "food")

    if glucose is None:
        return render_template("upload.html", days_options=ALLOWED_DAYS, days=days,
                               error="請選擇 LibreView 匯出的血糖 CSV。"), 400

    # 錯誤畫面要能提供「只產出 AGP 頁」，所以記住這次是不是帶了飲食檔
    try:
        s = cfg.load(SETTINGS_PATH)
        html, summary = build_report(
            str(glucose), str(food) if food else None, days,
            toolbar={"pdf": url_for("download", report_id=report_id),
                     "new": url_for("index")},
            detail_days=s.detail_days, summary_days=s.summary_days,
            meal_cards=s.meal_cards,
        )
    except ReportError as exc:
        shutil.rmtree(upload_dir, ignore_errors=True)
        return render_template("upload.html", days_options=ALLOWED_DAYS, days=days,
                               error=str(exc), had_food=food is not None), 400
    except (UnicodeDecodeError, KeyError, StopIteration, ValueError):
        # 上傳的不是 Libre 匯出檔時，解析會在各種地方炸開。
        # 失敗的上傳一定要當場刪掉——沒有報告指向它，事後的清除功能找不到它，
        # 那份含姓名與病歷號的 CSV 會永遠留在磁碟上。
        shutil.rmtree(upload_dir, ignore_errors=True)
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
    rows = sorted((m for m in (_meta_of(d.name) for d in _report_dirs()) if m),
                  key=lambda m: m["generated"], reverse=True)
    return render_template("history.html", rows=rows)


def _report_dirs() -> list[Path]:
    return [d for d in REPORTS.iterdir() if d.is_dir()] if REPORTS.exists() else []


@app.get("/settings")
def settings_page():
    return render_template("settings.html", s=cfg.load(SETTINGS_PATH), cfg=cfg,
                           total=len(_report_dirs()))


@app.post("/settings")
def settings_save():
    current = cfg.load(SETTINGS_PATH)
    picked = cfg.Settings(
        detail_days=request.form.get("detail_days", type=int, default=current.detail_days),
        summary_days=request.form.get("summary_days", type=int, default=current.summary_days),
        retention_days=request.form.get("retention_days", type=int,
                                        default=current.retention_days),
        meal_cards=request.form.get("meal_cards", type=int, default=current.meal_cards),
    )
    # 只接受清單內的值——手打 URL 送進 detail_days=999 會排出 999 個全寬圖表
    if (picked.detail_days not in cfg.DETAIL_CHOICES
            or picked.summary_days not in cfg.SUMMARY_CHOICES
            or picked.retention_days not in cfg.RETENTION_CHOICES
            or picked.meal_cards not in cfg.MEAL_CHOICES):
        abort(400, "設定值不在允許範圍內")
    cfg.save(SETTINGS_PATH, picked)
    flash("設定已儲存，下次產生報告時套用。")
    return redirect(url_for("settings_page"), code=303)


@app.post("/settings/purge")
def settings_purge():
    """依保留期限清除，或清除全部。刪除不可逆，也沒有垃圾桶——
    對醫療資料而言，留一份「已刪除」的副本比直接刪更糟。"""
    scope = request.form.get("scope")
    if scope == "all":
        removed = cfg.purge(_report_dirs())
        orphans = cfg.orphan_uploads(VAR)
        for folder in orphans:
            shutil.rmtree(folder, ignore_errors=True)
        flash(f"已清除全部 {removed} 份報告與對應的上傳檔。"
              + (f"另清除 {len(orphans)} 份沒有對應報告的殘留上傳檔。" if orphans else ""))
    else:
        s = cfg.load(SETTINGS_PATH)
        if not s.retention_days:
            flash("目前設定為永久保留，沒有可清除的過期資料。")
            return redirect(url_for("settings_page"), code=303)
        cutoff = datetime.now() - timedelta(days=s.retention_days)
        stale = [d for d in _report_dirs()
                 if (_meta_of(d.name) or {}).get("generated", "9999") < cutoff.isoformat()]
        removed = cfg.purge(stale)
        flash(f"已清除 {removed} 份超過 {s.retention_days} 天的報告與對應的上傳檔。"
              if removed else "沒有超過保留期限的報告。")
    return redirect(url_for("settings_page"), code=303)


@app.post("/r/<report_id>/delete")
def delete(report_id: str):
    folder = REPORTS / secure(report_id)
    if not folder.exists():
        abort(404)
    cfg.purge([folder])
    flash("報告與對應的上傳檔已刪除。")
    return redirect(url_for("history"), code=303)


@app.errorhandler(413)
def too_large(_):
    return render_template("upload.html", days_options=ALLOWED_DAYS,
                           error=f"檔案超過 {MAX_UPLOAD_MB} MB 上限。"), 413


def secure(report_id: str) -> str:
    """報告 ID 只會是 token_urlsafe 的字元集，任何其他東西都當作找不到。"""
    if not report_id or not all(c.isalnum() or c in "-_" for c in report_id):
        abort(404)
    return report_id
