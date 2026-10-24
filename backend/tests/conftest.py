from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

TESTS_DIR = Path(__file__).parent
sys.path.insert(0, str(TESTS_DIR))

from helpers import FakeSeparator, make_test_video  # noqa: E402


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("media")


@pytest.fixture(scope="session")
def youtube_like_video(media_dir) -> Path:
    """VP9 + Opus 的 mkv（跟 YouTube 下載下來的格式一樣），長 6 秒。"""
    return make_test_video(media_dir / "yt.mkv", seconds=6.0)


@pytest.fixture(scope="session")
def audio_only_file(media_dir) -> Path:
    path = media_dir / "audio.m4a"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=3", "-ac", "2", "-c:a", "aac", str(path)],
        check=True,
    )
    return path


@pytest.fixture
def fake_separator() -> FakeSeparator:
    return FakeSeparator()


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
