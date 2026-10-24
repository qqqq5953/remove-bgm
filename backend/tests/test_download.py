import pytest
from helpers import fake_yt_dlp_cmd

from remove_bgm import download, media
from remove_bgm.download import DownloadError


@pytest.fixture
def fake_ytdlp(monkeypatch, youtube_like_video):
    monkeypatch.setattr(download, "_yt_dlp_cmd", lambda: [*fake_yt_dlp_cmd(youtube_like_video), "--no-playlist"])


@pytest.mark.parametrize("url", ["https://www.youtube.com/watch?v=abc", "http://youtu.be/abc", " https://x.com/v "])
def test_validate_url_ok(url):
    assert download.validate_url(url) == url.strip()


@pytest.mark.parametrize("url", ["", "youtube.com/watch?v=abc", "ftp://a/b", "javascript:alert(1)", "https://"])
def test_validate_url_bad(url):
    with pytest.raises(DownloadError, match="網址格式錯誤"):
        download.validate_url(url)


@pytest.mark.parametrize(
    "start,end,expected",
    [(0, None, None), (0.0, 30, "*0.000-30.000"), (83, None, "*83.000-inf"), (1.5, 2.25, "*1.500-2.250")],
)
def test_section_arg(start, end, expected):
    assert download.section_arg(start, end) == expected


def test_default_command_uses_installed_yt_dlp():
    cmd = download._yt_dlp_cmd()
    assert cmd[1:3] == ["-m", "yt_dlp"] and "--no-playlist" in cmd


@pytest.mark.parametrize(
    "stderr,expected",
    [
        ("ERROR: Unsupported URL: https://a.b", "不支援這個網址"),
        ("ERROR: [youtube] x: Video unavailable", "影片無法觀看"),
        ("ERROR: [youtube] x: Private video", "影片無法觀看"),
        ("ERROR: Sign in to confirm you're not a bot", "要求登入驗證"),
        ("ERROR: unable to download video data: HTTP Error 403: Forbidden", "yt-dlp 版本太舊"),
        ("ERROR: Unable to download webpage: <urlopen error timed out>", "網路連線失敗"),
        ("ERROR: something new", "下載失敗：ERROR: something new"),
    ],
)
def test_explain_error(stderr, expected):
    assert expected in download.explain_error(stderr)


def test_fetch_info(fake_ytdlp):
    info = download.fetch_info("https://www.youtube.com/watch?v=ok")
    assert info.title == "測試影片: Fake/Title"
    assert info.duration == pytest.approx(6.0, abs=0.1)


def test_download_full(fake_ytdlp, tmp_path):
    progress = []
    path = download.download("https://www.youtube.com/watch?v=ok", tmp_path, progress=progress.append)
    assert path.exists() and path.suffix == ".mkv"
    assert progress == [0.1, 0.555, 1.0]
    assert media.probe(path).duration == pytest.approx(6.0, abs=0.1)


def test_download_section(fake_ytdlp, tmp_path):
    path = download.download("https://www.youtube.com/watch?v=ok", tmp_path, start=2.0, end=4.0)
    assert media.probe(path).duration == pytest.approx(2.0, abs=0.1)


@pytest.mark.parametrize("url,msg", [("https://y.com/unavailable", "影片無法觀看"), ("https://y.com/forbidden", "版本太舊")])
def test_download_errors(fake_ytdlp, tmp_path, url, msg):
    with pytest.raises(DownloadError, match=msg):
        download.download(url, tmp_path)
    with pytest.raises(DownloadError, match=msg):
        download.fetch_info(url)
