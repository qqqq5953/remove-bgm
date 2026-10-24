import json
import threading
import time
import zipfile
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from helpers import fake_yt_dlp_cmd

from remove_bgm import download, server


@pytest.fixture
def app_dir(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>remove-bgm</html>")
    return tmp_path, static


@pytest.fixture
def client(app_dir, fake_separator):
    root, static = app_dir
    app = server.create_app(separator=fake_separator, output_root=root / "out", static_dir=static)
    with TestClient(app) as c:
        yield c


def wait_done(client, job_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.1)
    raise TimeoutError


def upload(client, path, **form):
    with open(path, "rb") as f:
        return client.post("/api/jobs", data=form, files={"file": (path.name, f, "video/x-matroska")})


def read_events(client, job_id):
    events = []
    with client.stream("GET", f"/api/jobs/{job_id}/events") as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def test_health(client):
    data = client.get("/api/health").json()
    assert data["ffmpeg"] is True
    assert data["video_encoder"] in ("libx264", "h264_videotoolbox")
    assert data["yt_dlp"]
    assert data["model_ready"] is True and data["device"] == "cpu" and data["busy"] is False


def test_serves_frontend(client):
    assert "remove-bgm" in client.get("/").text


def test_upload_job_full_flow(client, youtube_like_video):
    resp = upload(client, youtube_like_video, start="0:01", end="0:03", export_music="true")
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["id"]

    events = read_events(client, job_id)
    assert events[-1]["type"] == "done"
    progress = [e["progress"] for e in events]
    assert progress == sorted(progress) and progress[-1] == 1.0
    assert all(e["stage_label"] for e in events[:-1])

    job = events[-1]
    assert job["title"] == "yt" and job["duration"] == pytest.approx(2.0)
    names = [f["name"] for f in job["files"]]
    assert names == ["video_no_music.mov", "dialogue.wav", "effects.wav", "dialogue+effects.wav", "original_music.wav"]

    # 單一檔案、Range 請求（瀏覽器拖動播放進度需要）
    wav = client.get(f"/api/jobs/{job_id}/files/dialogue.wav")
    assert wav.status_code == 200 and wav.content[:4] == b"RIFF"
    part = client.get(f"/api/jobs/{job_id}/files/video_no_music.mov", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100
    assert part.headers["content-range"].startswith("bytes 0-99/")

    # zip 下載
    z = client.get(f"/api/jobs/{job_id}/zip")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    assert "attachment" in z.headers["content-disposition"]
    with zipfile.ZipFile(BytesIO(z.content)) as zf:
        assert len(zf.namelist()) == 5

    # 上傳暫存檔已清除
    assert not any((client.app.state.manager.get(job_id).request.upload_path.parent).iterdir())


def test_without_music_option(client, youtube_like_video):
    job_id = upload(client, youtube_like_video, end="2").json()["id"]
    job = wait_done(client, job_id)
    assert "original_music.wav" not in [f["name"] for f in job["files"]]


def test_url_job(client, youtube_like_video, monkeypatch):
    monkeypatch.setattr(download, "_yt_dlp_cmd", lambda: fake_yt_dlp_cmd(youtube_like_video))
    resp = client.post("/api/jobs", data={"url": "https://www.youtube.com/watch?v=ok", "start": "1", "end": "3"})
    job = wait_done(client, resp.json()["id"])
    assert job["status"] == "done" and job["title"] == "測試影片: Fake/Title"


def test_url_job_download_error(client, youtube_like_video, monkeypatch):
    monkeypatch.setattr(download, "_yt_dlp_cmd", lambda: fake_yt_dlp_cmd(youtube_like_video))
    resp = client.post("/api/jobs", data={"url": "https://www.youtube.com/watch?v=unavailable"})
    events = read_events(client, resp.json()["id"])
    assert events[-1]["type"] == "error" and "影片無法觀看" in events[-1]["error"]


@pytest.mark.parametrize(
    "form,msg",
    [
        ({}, "兩者擇一"),
        ({"url": "not a url"}, "網址格式錯誤"),
        ({"url": "https://youtu.be/x", "start": "1:99"}, "時間格式錯誤"),
        ({"url": "https://youtu.be/x", "start": "0:10", "end": "0:05"}, "結束時間必須晚於開始時間"),
        ({"url": "https://youtu.be/x", "dialogue_db": "7"}, "對白音量必須介於"),
        ({"url": "https://youtu.be/x", "effects_db": "-13"}, "音效音量必須介於"),
    ],
)
def test_validation_errors(client, form, msg):
    resp = client.post("/api/jobs", data=form)
    assert resp.status_code == 400
    assert msg in resp.json()["detail"]


def test_url_and_file_together_rejected(client, youtube_like_video):
    resp = upload(client, youtube_like_video, url="https://youtu.be/x")
    assert resp.status_code == 400 and "兩者擇一" in resp.json()["detail"]


def test_range_error_reported_as_job_error(client, youtube_like_video):
    job = wait_done(client, upload(client, youtube_like_video, start="0:30").json()["id"])
    assert job["status"] == "error" and "開始時間超出影片長度" in job["error"]


def test_only_one_job_at_a_time(app_dir, youtube_like_video):
    gate = threading.Event()

    class SlowSeparator:
        sample_rate = 48000

        def separate(self, audio, progress=None):
            gate.wait(10)
            return {"dialogue": audio, "music": audio * 0, "effects": audio * 0}

    root, static = app_dir
    app = server.create_app(separator=SlowSeparator(), output_root=root / "out", static_dir=static)
    with TestClient(app) as c:
        first = upload(c, youtube_like_video, end="1")
        assert first.status_code == 200
        second = upload(c, youtube_like_video, end="1")
        assert second.status_code == 409 and "已有工作在處理中" in second.json()["detail"]
        assert c.get("/api/health").json()["busy"] is True
        gate.set()
        assert wait_done(c, first.json()["id"])["status"] == "done"
        assert upload(c, youtube_like_video, end="1").status_code == 200


def test_unexpected_error_reported(app_dir, youtube_like_video):
    class Broken:
        sample_rate = 48000

        def separate(self, audio, progress=None):
            raise ValueError("boom")

    root, static = app_dir
    with TestClient(server.create_app(separator=Broken(), output_root=root / "out", static_dir=static)) as c:
        job = wait_done(c, upload(c, youtube_like_video, end="1").json()["id"])
        assert job["status"] == "error" and job["error"] == "處理失敗：boom"


def test_unknown_job_and_file(client, youtube_like_video):
    assert client.get("/api/jobs/nope").status_code == 404
    job_id = upload(client, youtube_like_video, end="1").json()["id"]
    wait_done(client, job_id)
    assert client.get(f"/api/jobs/{job_id}/files/../../etc/passwd").status_code == 404
    assert client.get(f"/api/jobs/{job_id}/files/original_music.wav").status_code == 404


def test_reveal_on_mac(client, youtube_like_video, monkeypatch):
    job_id = upload(client, youtube_like_video, end="1").json()["id"]
    job = wait_done(client, job_id)
    calls = []
    monkeypatch.setattr(server.sys, "platform", "darwin")
    monkeypatch.setattr(server.subprocess, "run", lambda cmd, check: calls.append(cmd))
    assert client.post(f"/api/jobs/{job_id}/reveal").json() == {"ok": True}
    assert calls == [["open", "-R", job["folder"]]]


def test_reveal_not_mac(client, youtube_like_video, monkeypatch):
    job_id = upload(client, youtube_like_video, end="1").json()["id"]
    wait_done(client, job_id)
    monkeypatch.setattr(server.sys, "platform", "linux")
    resp = client.post(f"/api/jobs/{job_id}/reveal")
    assert resp.status_code == 400 and "只支援 macOS" in resp.json()["detail"]
