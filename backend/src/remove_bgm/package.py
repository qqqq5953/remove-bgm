from __future__ import annotations

import re
import zipfile
from pathlib import Path

from . import timecode

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def safe_name(title: str, max_len: int = 60) -> str:
    """把片名轉成可以當資料夾名稱的字串（保留中文）。"""
    name = _UNSAFE.sub("_", title).strip(" ._")
    name = re.sub(r"\s+", " ", name)
    return name[:max_len].rstrip(" ._") or "video"


def folder_name(title: str, start: float, end: float) -> str:
    return f"{safe_name(title)}_{timecode.format_compact(start)}-{timecode.format_compact(end)}"


def unique_dir(root: Path, name: str) -> Path:
    path = root / name
    n = 2
    while path.exists() or path.with_suffix(".zip").exists():
        path = root / f"{name} ({n})"
        n += 1
    return path


def make_zip(folder: Path) -> Path:
    """把資料夾打包成同名 zip（放在資料夾旁邊），zip 內含一層同名資料夾。"""
    zip_path = folder.parent / f"{folder.name}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
        for f in sorted(folder.iterdir()):
            zf.write(f, arcname=f"{folder.name}/{f.name}")
    return zip_path
