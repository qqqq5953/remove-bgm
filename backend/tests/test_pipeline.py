import zipfile

import numpy as np
import pytest
import soundfile as sf
from conftest import rms
from helpers import FAKE_RATIOS, fake_yt_dlp_cmd

from remove_bgm import download, media, pipeline
from remove_bgm.errors import UserError
from remove_bgm.pipeline import JobRequest


def run(req, sep, tmp_path):
    events = []
    result = pipeline.run(req, sep, tmp_path / "out", tmp_path / "work", lambda s, f: events.append((s, f)))
    return result, events


def test_overall_progress():
    assert pipeline.overall_progress("source", 0) == 0
    assert pipeline.overall_progress("package", 1) == pytest.approx(1)
    values = [pipeline.overall_progress(k, f) for k, _, _ in pipeline.STAGES for f in (0, 0.5, 1)]
    assert values == sorted(values)


def test_upload_with_trim(youtube_like_video, fake_separator, tmp_path):
    req = JobRequest(upload_path=youtube_like_video, upload_name="我的影片.mkv", start=1.0, end=4.0)
    result, events = run(req, fake_separator, tmp_path)

    assert result.title == "我的影片"
    assert result.folder.name == "我的影片_0001-0004"
    assert result.duration == pytest.approx(3.0)
    names = [f.name for f in result.files]
    assert names == ["video_no_music.mov", "dialogue.wav", "effects.wav", "dialogue+effects.wav"]

    # 每個 wav 都是 48kHz / 24-bit、長度剛好 3 秒
    for name in names[1:]:
        info = sf.info(result.folder / name)
        assert (info.samplerate, info.subtype, info.frames) == (48000, "PCM_24", 144000)
    # 影片長度一致（誤差小於 1 個影格）
    assert media.probe(result.folder / "video_no_music.mov").duration == pytest.approx(3.0, abs=0.05)

    # 各軌真的寫到對的檔案：假分離器比例是 對白 0.5、音效 0.2
    mixture = media.extract_audio(youtube_like_video, tmp_path / "m.wav", 1.0, 3.0)
    d, _ = sf.read(result.folder / "dialogue.wav")
    e, _ = sf.read(result.folder / "effects.wav")
    assert rms(d) == pytest.approx(rms(mixture) * FAKE_RATIOS["dialogue"], rel=0.01)
    assert rms(e) == pytest.approx(rms(mixture) * FAKE_RATIOS["effects"], rel=0.01)

    # 進度事件依階段順序出現
    stages = [s for s, _ in events]
    order = [k for k, _, _ in pipeline.STAGES]
    assert [k for k in order if k in stages] == order
    assert stages.index("separate") < stages.index("video")

    # zip 內容與資料夾一致，工作暫存資料夾已清掉
    with zipfile.ZipFile(result.zip_path) as zf:
        assert sorted(n.split("/")[1] for n in zf.namelist()) == sorted(names)
    assert not (tmp_path / "work").exists()


def test_export_music_option(youtube_like_video, fake_separator, tmp_path):
    result, _ = run(JobRequest(upload_path=youtube_like_video, end=2.0, export_music=True), fake_separator, tmp_path)
    assert "original_music.wav" in [f.name for f in result.files]
    with zipfile.ZipFile(result.zip_path) as zf:
        assert any(n.endswith("original_music.wav") for n in zf.namelist())


def test_gains_change_mix(youtube_like_video, fake_separator, tmp_path):
    a, _ = run(JobRequest(upload_path=youtube_like_video, end=2.0), fake_separator, tmp_path / "a")
    b, _ = run(JobRequest(upload_path=youtube_like_video, end=2.0, dialogue_db=-12, effects_db=6), fake_separator, tmp_path / "b")
    ma, _ = sf.read(a.folder / "dialogue+effects.wav")
    mb, _ = sf.read(b.folder / "dialogue+effects.wav")
    # 預設：0.5 + 0.2 = 0.7；調整後：0.5*0.2512 + 0.2*1.9953 ≈ 0.5246
    assert rms(mb) / rms(ma) == pytest.approx((0.5 * 0.2512 + 0.2 * 1.9953) / 0.7, rel=0.01)


def test_whole_file_without_trim(youtube_like_video, fake_separator, tmp_path):
    result, _ = run(JobRequest(upload_path=youtube_like_video), fake_separator, tmp_path)
    assert result.duration == pytest.approx(6.0, abs=0.1)


def test_audio_only_upload_skips_video(audio_only_file, fake_separator, tmp_path):
    result, _ = run(JobRequest(upload_path=audio_only_file), fake_separator, tmp_path)
    assert "video_no_music.mov" not in [f.name for f in result.files]


def test_youtube_url_with_trim(youtube_like_video, fake_separator, tmp_path, monkeypatch):
    monkeypatch.setattr(download, "_yt_dlp_cmd", lambda: fake_yt_dlp_cmd(youtube_like_video))
    result, events = run(JobRequest(url="https://www.youtube.com/watch?v=ok", start=2.0, end=5.0), fake_separator, tmp_path)
    assert result.title == "測試影片: Fake/Title"
    assert result.folder.name == "測試影片_ Fake_Title_0002-0005"
    assert result.duration == pytest.approx(3.0, abs=0.05)
    assert sf.info(result.folder / "dialogue.wav").frames == int(round(result.duration * 48000))
    # 下載進度有轉成 source 階段的進度
    assert ("source", 0.555) in events


@pytest.mark.parametrize(
    "req,msg",
    [
        (JobRequest(), "請貼上影片網址或上傳檔案"),
        (JobRequest(start=10.0), "請貼上影片網址或上傳檔案"),
    ],
)
def test_missing_source(req, msg, fake_separator, tmp_path):
    with pytest.raises(UserError, match=msg):
        run(req, fake_separator, tmp_path)


def test_range_beyond_duration(youtube_like_video, fake_separator, tmp_path):
    with pytest.raises(UserError, match="結束時間超出影片長度"):
        run(JobRequest(upload_path=youtube_like_video, start=1, end=60), fake_separator, tmp_path)
    assert fake_separator.calls == 0


def test_url_range_beyond_duration(youtube_like_video, fake_separator, tmp_path, monkeypatch):
    monkeypatch.setattr(download, "_yt_dlp_cmd", lambda: fake_yt_dlp_cmd(youtube_like_video))
    with pytest.raises(UserError, match="開始時間超出影片長度"):
        run(JobRequest(url="https://www.youtube.com/watch?v=ok", start=30), fake_separator, tmp_path)


def test_no_audio_track(tmp_path, fake_separator):
    import subprocess

    silent = tmp_path / "silent.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=duration=1", "-c:v", "libx264", str(silent)],
        check=True,
    )
    with pytest.raises(UserError, match="沒有音軌"):
        run(JobRequest(upload_path=silent), fake_separator, tmp_path)


def test_same_clip_twice_gets_unique_folder(youtube_like_video, fake_separator, tmp_path):
    a, _ = run(JobRequest(upload_path=youtube_like_video, end=1.0), fake_separator, tmp_path)
    b, _ = run(JobRequest(upload_path=youtube_like_video, end=1.0), fake_separator, tmp_path)
    assert a.folder != b.folder and b.folder.name.endswith("(2)")
    assert np.array_equal(sf.read(a.folder / "dialogue.wav")[0], sf.read(b.folder / "dialogue.wav")[0])
