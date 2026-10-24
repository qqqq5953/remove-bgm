"""ffmpeg / ffprobe 包裝。"""

from __future__ import annotations

import functools
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from .errors import UserError

log = logging.getLogger(__name__)

SAMPLE_RATE = 48000


class MediaError(UserError):
    pass


@dataclass
class MediaInfo:
    duration: float
    has_video: bool
    has_audio: bool
    video_codec: str | None = None
    width: int | None = None
    height: int | None = None


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    log.debug("執行：%s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise MediaError(f"{Path(cmd[0]).name} 執行失敗：{proc.stderr.strip()[-800:]}")
    return proc


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


@functools.lru_cache
def has_encoder(name: str) -> bool:
    if not ffmpeg_path():
        return False
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True)
    return any(line.split()[1:2] == [name] for line in proc.stdout.splitlines() if line.strip())


def probe(path: Path) -> MediaInfo:
    proc = _run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    )
    data = json.loads(proc.stdout)
    streams = data.get("streams", [])
    # 封面圖（attached_pic）不算影片
    video = next(
        (s for s in streams if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")),
        None,
    )
    audio = next((s for s in streams if s["codec_type"] == "audio"), None)
    duration = float(data.get("format", {}).get("duration") or 0)
    if not duration:
        raise MediaError("無法判斷檔案長度，檔案可能已損毀或不是影音檔")
    return MediaInfo(
        duration=duration,
        has_video=video is not None,
        has_audio=audio is not None,
        video_codec=video["codec_name"] if video else None,
        width=video.get("width") if video else None,
        height=video.get("height") if video else None,
    )


def _trim_args(start: float, duration: float) -> list[str]:
    # -ss 放在 -i 前面：快速定位，且重新編碼時仍是取樣精準
    return ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}"]


def extract_audio(src: Path, dst: Path, start: float, duration: float) -> np.ndarray:
    """抽出立體聲 48kHz 音訊，長度剛好等於 duration，回傳 (2, samples) float32。"""
    _run(
        ["ffmpeg", "-hide_banner", "-y", *_trim_args(start, duration), "-i", str(src),
         "-vn", "-ac", "2", "-ar", str(SAMPLE_RATE), "-af", "apad", "-t", f"{duration:.3f}",
         "-c:a", "pcm_f32le", str(dst)]
    )
    audio, sr = sf.read(dst, dtype="float32", always_2d=True)
    assert sr == SAMPLE_RATE
    return fit_length(audio.T, samples_for(duration))


def samples_for(duration: float) -> int:
    return int(round(duration * SAMPLE_RATE))


def fit_length(audio: np.ndarray, n: int) -> np.ndarray:
    """補零或截斷成剛好 n 個取樣。"""
    if audio.shape[-1] >= n:
        return audio[..., :n]
    pad = np.zeros(audio.shape[:-1] + (n - audio.shape[-1],), dtype=audio.dtype)
    return np.concatenate([audio, pad], axis=-1)


def write_wav(path: Path, audio: np.ndarray) -> None:
    """寫成 48kHz / 24-bit WAV。audio: (channels, samples)。"""
    sf.write(path, np.clip(audio.T, -1.0, 1.0), SAMPLE_RATE, subtype="PCM_24")


def video_encoder() -> str:
    return "h264_videotoolbox" if has_encoder("h264_videotoolbox") else "libx264"


def _encoder_args(encoder: str) -> list[str]:
    if encoder == "h264_videotoolbox":
        return ["-c:v", "h264_videotoolbox", "-b:v", "12M", "-allow_sw", "1"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]


def encode_video(src: Path, audio_wav: Path, dst: Path, start: float, duration: float) -> str:
    """把影片轉成 H.264 .mov，音軌換成 audio_wav。回傳實際使用的編碼器。"""
    encoders = [video_encoder()]
    if encoders[0] != "libx264":
        encoders.append("libx264")  # 硬體編碼失敗時退回軟體編碼
    last_error: MediaError | None = None
    for encoder in encoders:
        try:
            _run(
                ["ffmpeg", "-hide_banner", "-y", *_trim_args(start, duration), "-i", str(src),
                 "-i", str(audio_wav), "-map", "0:v:0", "-map", "1:a:0",
                 *_encoder_args(encoder), "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-b:a", "256k", "-t", f"{duration:.3f}",
                 "-movflags", "+faststart", str(dst)]
            )
            return encoder
        except MediaError as e:
            log.warning("編碼器 %s 失敗：%s", encoder, e)
            last_error = e
    assert last_error is not None
    raise last_error
