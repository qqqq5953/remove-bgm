"""端到端測試用的伺服器：真實的 API、ffmpeg、前端，分離器與 yt-dlp 換成測試替身。

用法：uv run --project ../backend python e2e_server.py <port> <工作目錄>
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "tests"))

import uvicorn  # noqa: E402
from helpers import FakeSeparator, fake_yt_dlp_cmd, make_test_video  # noqa: E402

from remove_bgm import download, server  # noqa: E402


def main() -> None:
    port = int(sys.argv[1])
    work = Path(sys.argv[2])
    work.mkdir(parents=True, exist_ok=True)
    source = make_test_video(work / "youtube_source.mkv", seconds=8.0)
    download._yt_dlp_cmd = lambda: fake_yt_dlp_cmd(source)
    app = server.create_app(separator=FakeSeparator(), output_root=work / "out")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
