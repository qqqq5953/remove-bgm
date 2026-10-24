"""測試用的假分離器、假 yt-dlp 與測試影片產生器。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np

# 假分離器把混音依固定比例拆成三軌（加起來等於原音），用來確認每一軌有寫到正確的檔案
FAKE_RATIOS = {"dialogue": 0.5, "music": 0.3, "effects": 0.2}


class FakeSeparator:
    sample_rate = 48000
    device = "cpu"

    def __init__(self) -> None:
        self.calls = 0

    def is_ready(self) -> bool:
        return True

    def separate(self, audio: np.ndarray, progress=None) -> dict[str, np.ndarray]:
        self.calls += 1
        if progress:
            progress(0.5)
            progress(1.0)
        return {k: (audio * r).astype(np.float32) for k, r in FAKE_RATIOS.items()}


def make_test_video(path: Path, seconds: float, vcodec: str = "libvpx-vp9", acodec: str = "libopus") -> Path:
    """產生測試影片：彩色測試畫面 + 立體聲（左右不同頻率的正弦波加上脈衝）。"""
    audio_expr = (
        "aevalsrc="
        "'0.3*sin(2*PI*220*t)+0.2*sin(2*PI*1500*t)*lt(mod(t,1),0.1)'"
        "|'0.3*sin(2*PI*330*t)+0.2*sin(2*PI*2500*t)*lt(mod(t,1),0.1)'"
        f":s=48000:d={seconds}"
    )
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=25:duration={seconds}",
            "-f", "lavfi", "-i", audio_expr,
            "-c:v", vcodec, *(["-b:v", "300k", "-deadline", "realtime", "-cpu-used", "8"] if "vp9" in vcodec else []),
            "-c:a", acodec, "-shortest", str(path),
        ],
        check=True,
    )
    return path


FAKE_YT_DLP = Path(__file__).parent / "fake_yt_dlp.py"


def fake_yt_dlp_cmd(source_video: Path) -> list[str]:
    return [sys.executable, str(FAKE_YT_DLP), "--source", str(source_video)]
