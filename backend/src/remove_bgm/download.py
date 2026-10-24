"""用 yt-dlp 下載 YouTube（或其他 yt-dlp 支援網站）的影片片段。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .errors import UserError

ProgressFn = Callable[[float], None]

# 最高 1080p，夠練習用，也避免下載太久
FORMAT = "bv*[height<=1080]+ba/b[height<=1080]/b"


class DownloadError(UserError):
    pass


@dataclass
class VideoInfo:
    title: str
    duration: float | None


def validate_url(url: str) -> str:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise DownloadError("網址格式錯誤，請貼上完整的影片網址，例如 https://www.youtube.com/watch?v=...")
    return url


def _yt_dlp_cmd() -> list[str]:
    return [sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-warnings"]


def explain_error(stderr: str) -> str:
    """把 yt-dlp 的英文錯誤轉成中文說明。"""
    text = stderr.lower()
    if "unsupported url" in text or "is not a valid url" in text:
        return "不支援這個網址，請確認是 YouTube 影片網址"
    if "video unavailable" in text or "private video" in text or "has been removed" in text:
        return "影片無法觀看（可能已下架、設為私人或有地區限制）"
    if "sign in to confirm" in text or ("age" in text and "restricted" in text):
        return "YouTube 要求登入驗證（年齡限制或機器人驗證），請改用「上傳檔案」"
    if "http error 403" in text or "nsig" in text or "signature" in text:
        return "YouTube 拒絕下載，通常是 yt-dlp 版本太舊；重新執行 start.command 會自動更新"
    if "unable to download" in text or "urlopen error" in text or "timed out" in text:
        return "網路連線失敗，請檢查網路後再試一次"
    last = stderr.strip().splitlines()[-1] if stderr.strip() else "未知錯誤"
    return f"下載失敗：{last}"


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise DownloadError(explain_error(proc.stderr))
    return proc


def fetch_info(url: str) -> VideoInfo:
    proc = _run([*_yt_dlp_cmd(), "--dump-single-json", "--skip-download", url])
    data = json.loads(proc.stdout)
    return VideoInfo(title=data.get("title") or "video", duration=data.get("duration"))


def section_arg(start: float, end: float | None) -> str | None:
    if not start and end is None:
        return None
    return f"*{start:.3f}-{'inf' if end is None else f'{end:.3f}'}"


_PROGRESS = re.compile(r"^PROGRESS\s+([\d.]+)%")


def download(
    url: str,
    dest_dir: Path,
    start: float = 0.0,
    end: float | None = None,
    progress: ProgressFn | None = None,
) -> Path:
    """下載影片（有指定範圍時只下載那一段），回傳檔案路徑。

    指定範圍時下載下來的檔案就是該片段，後續處理從 0 秒開始。
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        *_yt_dlp_cmd(),
        "-f", FORMAT,
        "--merge-output-format", "mkv",
        "-o", str(dest_dir / "source.%(ext)s"),
        "--newline",
        "--progress",  # --print 會隱含 --quiet，要明確開啟進度輸出
        "--progress-template", "download:PROGRESS %(progress._percent_str)s",
        "--print", "after_move:FILEPATH %(filepath)s",
    ]
    section = section_arg(start, end)
    if section:
        # 在切點強制補關鍵影格，片段才會從指定時間精準開始
        cmd += ["--download-sections", section, "--force-keyframes-at-cuts"]
    cmd.append(url)

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    filepath: str | None = None
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if line.startswith("FILEPATH "):
            filepath = line[len("FILEPATH "):]
        elif m := _PROGRESS.match(line):
            if progress:
                progress(min(1.0, float(m.group(1)) / 100))
    stderr = proc.stderr.read() if proc.stderr else ""
    if proc.wait() != 0:
        raise DownloadError(explain_error(stderr))
    if not filepath or not Path(filepath).exists():
        raise DownloadError("下載完成但找不到檔案")
    return Path(filepath)
