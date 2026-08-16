"""網頁上可調的設定。

存成一個 JSON 檔而不是資料庫——設定只有三個欄位、只有一個使用者，
引進 SQLite 只是多一個要備份的東西。

保留期限目前只做**手動清除**：設定頁按下按鈕才會刪。自動排程（systemd timer）
列在 future work，因為那需要再開一個要 sudo 安裝的 unit。
"""

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULTS = {"detail_days": 7, "summary_days": 14, "retention_days": 0}

DETAIL_CHOICES = (3, 7, 14)
SUMMARY_CHOICES = (7, 14, 30)
# 0 代表永久保留。刻意不提供「1 天」——手滑選到會在下一次清除時洗掉全部歷史。
RETENTION_CHOICES = (0, 30, 90, 180, 365)


@dataclass
class Settings:
    detail_days: int = 7
    summary_days: int = 14
    retention_days: int = 0     # 0 = 永久保留

    @property
    def retention_label(self) -> str:
        return "永久保留" if not self.retention_days else f"{self.retention_days} 天"


def load(path: Path) -> Settings:
    """讀不到或壞掉都退回預設值——設定檔壞掉不該讓整個網站打不開。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return Settings()
    return Settings(**{k: int(raw.get(k, v)) for k, v in DEFAULTS.items()})


def save(path: Path, settings: Settings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")


def purge(report_dirs: list[Path]) -> int:
    """刪掉指定的報告，連同上傳的原始 CSV。回傳刪除筆數。

    只刪報告會留下含姓名與病歷號的原始檔在磁碟上，那比不刪更糟——
    使用者會以為已經清乾淨了。
    """
    removed = 0
    for folder in report_dirs:
        uploads = folder.parent.parent / "uploads" / folder.name
        shutil.rmtree(folder, ignore_errors=True)
        shutil.rmtree(uploads, ignore_errors=True)
        removed += 1
    return removed


def orphan_uploads(var: Path) -> list[Path]:
    """有上傳檔卻沒有對應報告的目錄。

    上傳是在產報告「之前」存檔的，所以解析失敗（例如匯出錯分頁）時報告不會
    產生，那份 CSV 卻留了下來，而且以報告為索引的清除完全掃不到它。
    """
    reports, uploads = var / "reports", var / "uploads"
    if not uploads.exists():
        return []
    return [d for d in uploads.iterdir()
            if d.is_dir() and not (reports / d.name).exists()]
