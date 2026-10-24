"""串接整個處理流程：取得來源 → 抽音軌 → 分離 → 混音 → 轉檔 → 打包。"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import download, media, mixing, package, timecode
from .errors import UserError
from .separate.base import Separator

log = logging.getLogger(__name__)

# 各階段：(代號, 顯示名稱, 佔整體進度的比重)
STAGES = [
    ("source", "取得影片", 15),
    ("extract", "抽出音軌", 5),
    ("model", "準備模型（第一次使用需下載）", 5),
    ("separate", "分離對白／音樂／音效", 55),
    ("mix", "混音", 5),
    ("video", "轉檔影片", 15),
    ("package", "打包", 5),
]
STAGE_LABELS = {key: label for key, label, _ in STAGES}

ReportFn = Callable[[str, float], None]


@dataclass
class JobRequest:
    url: str | None = None
    upload_path: Path | None = None
    upload_name: str | None = None
    start: float | None = None
    end: float | None = None
    dialogue_db: float = 0.0
    effects_db: float = 0.0
    export_music: bool = False


@dataclass
class OutputFile:
    name: str
    kind: str  # "audio" | "video"
    label: str


@dataclass
class JobResult:
    title: str
    folder: Path
    zip_path: Path
    duration: float
    files: list[OutputFile] = field(default_factory=list)


def overall_progress(stage: str, fraction: float) -> float:
    total = sum(w for _, _, w in STAGES)
    done = 0.0
    for key, _, weight in STAGES:
        if key == stage:
            return (done + weight * max(0.0, min(1.0, fraction))) / total
        done += weight
    raise KeyError(stage)


def run(
    req: JobRequest,
    separator: Separator,
    output_root: Path,
    work_dir: Path,
    report: ReportFn,
) -> JobResult:
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        return _run(req, separator, output_root, work_dir, report)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _run(req: JobRequest, separator: Separator, output_root: Path, work_dir: Path, report: ReportFn) -> JobResult:
    # 1. 取得來源，算出要處理的區間（local_start, length）
    report("source", 0.0)
    if req.url:
        url = download.validate_url(req.url)
        info = download.fetch_info(url)
        start, end = timecode.validate_range(req.start, req.end, info.duration)
        src = download.download(url, work_dir / "download", start, end, lambda f: report("source", f))
        title = info.title
        src_info = media.probe(src)
        local_start = 0.0
        wanted = (end - start) if end is not None else src_info.duration
        length = min(wanted, src_info.duration)
    elif req.upload_path:
        src = req.upload_path
        title = Path(req.upload_name or src.name).stem
        src_info = media.probe(src)
        start, end = timecode.validate_range(req.start, req.end, src_info.duration)
        local_start = start
        length = (end if end is not None else src_info.duration) - start
    else:
        raise UserError("請貼上影片網址或上傳檔案")
    if not src_info.has_audio:
        raise UserError("這個檔案沒有音軌")
    report("source", 1.0)

    # 2. 抽音軌
    report("extract", 0.0)
    mixture = media.extract_audio(src, work_dir / "mixture.wav", local_start, length)
    n_samples = mixture.shape[-1]
    report("extract", 1.0)

    # 3. 分離
    report("model", 0.0)
    prepare = getattr(separator, "prepare", None)
    if prepare:
        prepare(lambda f: report("model", f))
    report("model", 1.0)
    report("separate", 0.0)
    stems = separator.separate(mixture, lambda f: report("separate", f))
    stems = {k: media.fit_length(v, n_samples) for k, v in stems.items()}
    report("separate", 1.0)

    # 4. 混音並寫出 stem
    report("mix", 0.0)
    folder = package.unique_dir(output_root, package.folder_name(title, start, start + length))
    folder.mkdir(parents=True)
    files: list[OutputFile] = []

    def write(name: str, audio, label: str) -> None:
        media.write_wav(folder / name, audio)
        files.append(OutputFile(name, "audio", label))

    write("dialogue.wav", stems["dialogue"], "對白")
    write("effects.wav", stems["effects"], "音效")
    mixed = mixing.mix(stems["dialogue"], stems["effects"], req.dialogue_db, req.effects_db)
    write("dialogue+effects.wav", mixed, "對白＋音效（混音）")
    if req.export_music:
        write("original_music.wav", stems["music"], "原配樂")
    report("mix", 1.0)

    # 5. 轉檔影片（純音訊檔案就跳過）
    report("video", 0.0)
    if src_info.has_video:
        media.encode_video(src, folder / "dialogue+effects.wav", folder / "video_no_music.mov", local_start, length)
        files.insert(0, OutputFile("video_no_music.mov", "video", "去掉配樂的影片"))
    report("video", 1.0)

    # 6. 打包
    report("package", 0.0)
    zip_path = package.make_zip(folder)
    report("package", 1.0)

    return JobResult(title=title, folder=folder, zip_path=zip_path, duration=length, files=files)
