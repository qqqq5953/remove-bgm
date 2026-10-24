"""時間格式解析：接受 "83"、"1:23"、"01:02:03"、"1:23.5" 這類寫法。"""

from __future__ import annotations

import re

from .errors import UserError

_PART = re.compile(r"^\d+(\.\d+)?$")


class TimecodeError(UserError):
    pass


def parse(text: str | None) -> float | None:
    """解析時間字串成秒數；空字串回傳 None（代表沒有指定）。"""
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None

    parts = text.split(":")
    if len(parts) > 3 or any(not _PART.match(p) for p in parts):
        raise TimecodeError(f"時間格式錯誤：「{text}」，請用 分:秒 或 時:分:秒，例如 1:23 或 01:02:03")
    # 只有最後一段可以有小數
    if any("." in p for p in parts[:-1]):
        raise TimecodeError(f"時間格式錯誤：「{text}」，只有秒數可以有小數")

    values = [float(p) for p in parts]
    # 第一段以外的分、秒必須小於 60
    if any(v >= 60 for v in values[1:]):
        raise TimecodeError(f"時間格式錯誤：「{text}」，分和秒必須小於 60")

    seconds = 0.0
    for v in values:
        seconds = seconds * 60 + v
    return seconds


def validate_range(start: float | None, end: float | None, duration: float | None) -> tuple[float, float | None]:
    """檢查片段範圍，回傳 (start, end)；end 為 None 代表到結尾。"""
    start = start or 0.0
    if end is not None and end <= start:
        raise TimecodeError("結束時間必須晚於開始時間")
    if duration is not None:
        if start >= duration:
            raise TimecodeError(f"開始時間超出影片長度（影片長度 {format_display(duration)}）")
        if end is not None and end > duration + 0.05:
            raise TimecodeError(f"結束時間超出影片長度（影片長度 {format_display(duration)}）")
        if end is not None:
            end = min(end, duration)
    return start, end


def format_display(seconds: float) -> str:
    """顯示用，例如 83.5 → "1:23.5"、3723 → "1:02:03"。"""
    total = round(seconds, 3)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    s_text = f"{s:06.3f}".rstrip("0").rstrip(".")
    if h:
        return f"{int(h)}:{int(m):02d}:{s_text}"
    return f"{int(m)}:{s_text}"


def format_compact(seconds: float) -> str:
    """資料夾名稱用，例如 83 → "0123"、3723 → "010203"。"""
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}{m:02d}{s:02d}"
    return f"{m:02d}{s:02d}"
