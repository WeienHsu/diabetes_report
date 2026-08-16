"""把產出的報告 HTML 轉成 A4 PDF。

用的是機器上既有的 Playwright headless chromium，直接吃 file://——
走的是報告本身那條已經驗證過的 @media print 樣式，版面必然與人工 Ctrl+P 一致。
也因為是 file://，chromium 不必穿過 Cloudflare Access 的認證。
"""

import subprocess
from pathlib import Path

# 機器上同時存在多個版本（-1228、-1234…），寫死版本號在升級後會斷。
CHROMIUM_GLOB = ".cache/ms-playwright/chromium_headless_shell-*/chrome-linux/headless_shell"
TIMEOUT_SEC = 120


def find_chromium() -> Path:
    found = sorted(Path.home().glob(CHROMIUM_GLOB))
    if not found:
        raise RuntimeError(
            f"找不到 headless chromium（~/{CHROMIUM_GLOB}）。"
            "請先安裝 Playwright 的 chromium。")
    # 目錄名結尾是版本號，取最新的一個
    return max(found, key=lambda p: int(p.parts[-3].rsplit("-", 1)[-1]))


def render(html_path: Path, pdf_path: Path) -> Path:
    """轉檔並回傳 PDF 路徑。已存在就直接沿用——同一份報告不會變。"""
    if pdf_path.exists():
        return pdf_path

    result = subprocess.run(
        [str(find_chromium()), "--headless", "--disable-gpu", "--no-sandbox",
         "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}",
         html_path.resolve().as_uri()],
        capture_output=True, timeout=TIMEOUT_SEC,
    )
    if not pdf_path.exists():
        raise RuntimeError(f"PDF 轉檔失敗：{result.stderr.decode(errors='replace')[-500:]}")
    return pdf_path
