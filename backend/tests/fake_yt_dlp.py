"""模擬 yt-dlp 命令列的假程式（只實作 remove_bgm.download 用到的參數）。

- 網址含 "unavailable" → 模擬影片下架錯誤
- 網址含 "forbidden" → 模擬 HTTP 403
- 其他網址 → 用 --source 指定的影片當作下載結果，依 --download-sections 用 ffmpeg 裁切
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys


def duration_of(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--source", required=True)
    p.add_argument("--dump-single-json", action="store_true")
    p.add_argument("--skip-download", action="store_true")
    p.add_argument("--no-playlist", action="store_true")
    p.add_argument("--no-warnings", action="store_true")
    p.add_argument("--newline", action="store_true")
    p.add_argument("--progress", action="store_true")
    p.add_argument("--force-keyframes-at-cuts", action="store_true")
    p.add_argument("-f")
    p.add_argument("--merge-output-format")
    p.add_argument("-o")
    p.add_argument("--progress-template")
    p.add_argument("--print", dest="print_")
    p.add_argument("--download-sections")
    p.add_argument("url")
    args = p.parse_args()

    if "unavailable" in args.url:
        print("ERROR: [youtube] abc: Video unavailable. This video has been removed", file=sys.stderr)
        return 1
    if "forbidden" in args.url:
        print("ERROR: unable to download video data: HTTP Error 403: Forbidden", file=sys.stderr)
        return 1

    if args.dump_single_json:
        print(json.dumps({"title": "測試影片: Fake/Title", "duration": duration_of(args.source)}))
        return 0

    out = args.o.replace("%(ext)s", "mkv")
    for pct in ("10.0", "55.5", "100.0"):
        print(f"PROGRESS {pct}%", flush=True)
    if args.download_sections:
        start, end = args.download_sections.lstrip("*").split("-")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", start, "-i", args.source]
        if end != "inf":
            cmd += ["-t", str(float(end) - float(start))]
        subprocess.run([*cmd, "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8",
                        "-c:a", "libopus", out], check=True)
    else:
        shutil.copy(args.source, out)
    print(f"FILEPATH {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
