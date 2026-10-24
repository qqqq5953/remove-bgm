import json
import subprocess

import numpy as np
import pytest
import soundfile as sf

from remove_bgm import media


def ffprobe_streams(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def test_probe(youtube_like_video):
    info = media.probe(youtube_like_video)
    assert info.has_video and info.has_audio
    assert info.video_codec == "vp9"
    assert (info.width, info.height) == (320, 240)
    assert info.duration == pytest.approx(6.0, abs=0.1)


def test_probe_audio_only(audio_only_file):
    info = media.probe(audio_only_file)
    assert not info.has_video and info.has_audio


def test_probe_invalid_file(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(media.MediaError):
        media.probe(bad)


def test_extract_audio_exact_length(youtube_like_video, tmp_path):
    audio = media.extract_audio(youtube_like_video, tmp_path / "a.wav", start=1.0, duration=2.5)
    assert audio.shape == (2, 2.5 * 48000)
    assert audio.dtype == np.float32
    # 左右聲道不同（測試檔左右頻率不同），確認沒有被混成單聲道
    assert not np.allclose(audio[0], audio[1])


def test_extract_audio_pads_past_end(youtube_like_video, tmp_path):
    # 要求的長度超過檔案剩餘長度時補靜音，長度仍然精準
    audio = media.extract_audio(youtube_like_video, tmp_path / "a.wav", start=5.0, duration=2.0)
    assert audio.shape == (2, 96000)
    assert np.abs(audio[:, -4800:]).max() == 0


def test_fit_length():
    x = np.ones((2, 10), dtype=np.float32)
    assert media.fit_length(x, 5).shape == (2, 5)
    padded = media.fit_length(x, 15)
    assert padded.shape == (2, 15) and padded[:, 10:].sum() == 0


def test_write_wav_format(tmp_path):
    path = tmp_path / "x.wav"
    media.write_wav(path, np.full((2, 4800), 1.5, dtype=np.float32))  # 超出範圍會被限制在 ±1
    info = sf.info(path)
    assert (info.samplerate, info.channels, info.subtype, info.frames) == (48000, 2, "PCM_24", 4800)
    data, _ = sf.read(path)
    assert data.max() <= 1.0


def test_encode_video_h264_mov(youtube_like_video, tmp_path):
    wav = tmp_path / "mix.wav"
    media.write_wav(wav, np.zeros((2, int(3.0 * 48000)), dtype=np.float32))
    out = tmp_path / "out.mov"
    encoder = media.encode_video(youtube_like_video, wav, out, start=1.0, duration=3.0)
    assert encoder in ("libx264", "h264_videotoolbox")
    data = ffprobe_streams(out)
    v = next(s for s in data["streams"] if s["codec_type"] == "video")
    a = next(s for s in data["streams"] if s["codec_type"] == "audio")
    assert v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p"
    assert a["codec_name"] == "aac"
    assert data["format"]["format_name"].startswith("mov")
    # 影片長度與指定長度誤差小於 1 個影格（25fps → 0.04 秒）
    assert float(v["duration"]) == pytest.approx(3.0, abs=0.04)


def test_encode_video_falls_back_to_libx264(youtube_like_video, tmp_path, monkeypatch):
    calls = []
    real_run = media._run

    def fake_run(cmd):
        calls.append(cmd)
        if "h264_videotoolbox" in cmd:
            raise media.MediaError("videotoolbox 不可用")
        return real_run(cmd)

    monkeypatch.setattr(media, "video_encoder", lambda: "h264_videotoolbox")
    monkeypatch.setattr(media, "_run", fake_run)
    wav = tmp_path / "mix.wav"
    media.write_wav(wav, np.zeros((2, 48000), dtype=np.float32))
    assert media.encode_video(youtube_like_video, wav, tmp_path / "o.mov", 0, 1.0) == "libx264"
    assert len(calls) == 2


def test_videotoolbox_args():
    args = media._encoder_args("h264_videotoolbox")
    assert args[:2] == ["-c:v", "h264_videotoolbox"]
