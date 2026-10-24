"""FastAPI 伺服器：提供 API 與前端頁面。"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import media, mixing, pipeline, timecode
from .download import validate_url
from .errors import UserError
from .jobs import BusyError, Job, JobManager
from .separate.base import Separator

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]


def default_output_root() -> Path:
    env = os.environ.get("REMOVE_BGM_OUTPUT_DIR")
    return Path(env) if env else Path.home() / "Movies" / "remove-bgm"


def default_static_dir() -> Path:
    return REPO_ROOT / "frontend" / "dist"


def _yt_dlp_version() -> str | None:
    try:
        from yt_dlp.version import __version__

        return __version__
    except ImportError:
        return None


def create_app(
    separator: Separator | None = None,
    output_root: Path | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    if separator is None:
        from .separate.bandit import BanditSeparator

        separator = BanditSeparator()
    output_root = output_root or default_output_root()
    static_dir = static_dir or default_static_dir()

    def runner(job: Job, report) -> pipeline.JobResult:
        try:
            return pipeline.run(job.request, separator, output_root, output_root / ".work" / job.id, report)
        finally:
            # 上傳的原始檔處理完就刪掉，避免佔用空間
            if job.request.upload_path:
                job.request.upload_path.unlink(missing_ok=True)

    manager = JobManager(runner)
    app = FastAPI(title="remove-bgm")
    app.state.manager = manager

    @app.exception_handler(UserError)
    async def user_error_handler(_: Request, exc: UserError):
        status = 409 if isinstance(exc, BusyError) else 400
        return JSONResponse({"detail": str(exc)}, status_code=status)

    @app.get("/api/health")
    def health():
        encoder = media.video_encoder() if media.ffmpeg_path() else None
        is_ready = getattr(separator, "is_ready", None)
        device = getattr(separator, "device", None)
        return {
            "ffmpeg": media.ffmpeg_path() is not None,
            "video_encoder": encoder,
            "yt_dlp": _yt_dlp_version(),
            "model_ready": bool(is_ready()) if callable(is_ready) else True,
            "device": str(device) if device is not None else "cpu",
            "busy": manager.busy(),
        }

    @app.post("/api/jobs")
    async def create_job(
        url: str = Form(""),
        file: UploadFile | None = File(None),
        start: str = Form(""),
        end: str = Form(""),
        dialogue_db: float = Form(0.0),
        effects_db: float = Form(0.0),
        export_music: bool = Form(False),
    ):
        has_file = file is not None and bool(file.filename)
        if bool(url.strip()) == has_file:
            raise UserError("請貼上影片網址，或上傳一個檔案（兩者擇一）")
        if url.strip():
            validate_url(url)
        start_s = timecode.parse(start)
        end_s = timecode.parse(end)
        timecode.validate_range(start_s, end_s, None)
        for label, value in (("對白", dialogue_db), ("音效", effects_db)):
            if not mixing.MIN_DB <= value <= mixing.MAX_DB:
                raise UserError(f"{label}音量必須介於 {mixing.MIN_DB:g} 到 +{mixing.MAX_DB:g} dB")
        if manager.busy():
            raise BusyError("目前已有工作在處理中，請等它完成後再送出")

        req = pipeline.JobRequest(
            url=url.strip() or None,
            start=start_s,
            end=end_s,
            dialogue_db=dialogue_db,
            effects_db=effects_db,
            export_music=export_music,
        )
        if has_file:
            assert file is not None
            upload_dir = output_root / ".uploads"
            upload_dir.mkdir(parents=True, exist_ok=True)
            suffix = Path(file.filename or "").suffix[:10]
            dest = upload_dir / f"{os.urandom(6).hex()}{suffix}"
            with open(dest, "wb") as out:
                await asyncio.to_thread(shutil.copyfileobj, file.file, out, 1 << 20)
            req.upload_path = dest
            req.upload_name = Path(file.filename or "video").name

        return manager.submit(req).snapshot()

    def _job(job_id: str) -> Job:
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(404, "找不到這個工作")
        return job

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return _job(job_id).snapshot()

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request):
        job = _job(job_id)

        async def stream():
            sent = 0
            while True:
                while sent < len(job.events):
                    event = job.events[sent]
                    sent += 1
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event["type"] in ("done", "error"):
                        return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.2)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    def _done_job(job_id: str) -> Job:
        job = _job(job_id)
        if job.result is None:
            raise HTTPException(409, "工作尚未完成")
        return job

    @app.get("/api/jobs/{job_id}/files/{name}")
    def get_file(job_id: str, name: str):
        job = _done_job(job_id)
        assert job.result is not None
        if name not in {f.name for f in job.result.files}:
            raise HTTPException(404, "找不到這個檔案")
        return FileResponse(job.result.folder / name)

    @app.get("/api/jobs/{job_id}/zip")
    def get_zip(job_id: str):
        job = _done_job(job_id)
        assert job.result is not None
        return FileResponse(job.result.zip_path, filename=job.result.zip_path.name, media_type="application/zip")

    @app.post("/api/jobs/{job_id}/reveal")
    def reveal(job_id: str):
        job = _done_job(job_id)
        assert job.result is not None
        if sys.platform != "darwin":
            raise UserError("「在 Finder 中顯示」只支援 macOS")
        subprocess.run(["open", "-R", str(job.result.folder)], check=False)
        return {"ok": True}

    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="remove-bgm 本機伺服器")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = create_app()
    print(f"remove-bgm 已啟動：http://127.0.0.1:{args.port}（關閉這個視窗即可結束）")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
