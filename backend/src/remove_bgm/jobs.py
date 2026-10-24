"""工作佇列：一次只跑一個工作，並記錄進度事件給 SSE 推播。"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from .errors import UserError
from .pipeline import STAGE_LABELS, JobRequest, JobResult, overall_progress

log = logging.getLogger(__name__)

Runner = Callable[["Job", Callable[[str, float], None]], JobResult]


class BusyError(UserError):
    pass


@dataclass
class Job:
    id: str
    request: JobRequest
    status: str = "queued"  # queued | running | done | error
    stage: str | None = None
    progress: float = 0.0
    error: str | None = None
    result: JobResult | None = None
    events: list[dict] = field(default_factory=list)

    def snapshot(self) -> dict:
        data = {
            "id": self.id,
            "status": self.status,
            "stage": self.stage,
            "stage_label": STAGE_LABELS.get(self.stage or "", None),
            "progress": round(self.progress, 4),
            "error": self.error,
        }
        if self.result:
            data.update(
                title=self.result.title,
                folder=str(self.result.folder),
                duration=self.result.duration,
                files=[
                    {
                        "name": f.name,
                        "kind": f.kind,
                        "label": f.label,
                        "url": f"/api/jobs/{self.id}/files/{f.name}",
                    }
                    for f in self.result.files
                ],
                zip_name=self.result.zip_path.name,
                zip_url=f"/api/jobs/{self.id}/zip",
            )
        return data


class JobManager:
    def __init__(self, runner: Runner):
        self._runner = runner
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def busy(self) -> bool:
        return any(j.status in ("queued", "running") for j in self._jobs.values())

    def submit(self, request: JobRequest) -> Job:
        with self._lock:
            if self.busy():
                raise BusyError("目前已有工作在處理中，請等它完成後再送出")
            job = Job(id=uuid.uuid4().hex[:12], request=request)
            self._jobs[job.id] = job
        threading.Thread(target=self._work, args=(job,), daemon=True).start()
        return job

    def _emit(self, job: Job, event: dict) -> None:
        job.events.append(event)

    def _work(self, job: Job) -> None:
        job.status = "running"
        last = {"stage": None, "progress": -1.0, "time": 0.0}

        def report(stage: str, fraction: float) -> None:
            job.stage = stage
            job.progress = overall_progress(stage, fraction)
            now = time.monotonic()
            # 換階段一定推播；同一階段內限制推播頻率
            if stage != last["stage"] or job.progress - last["progress"] >= 0.005 or now - last["time"] > 1:
                last.update(stage=stage, progress=job.progress, time=now)
                self._emit(job, {"type": "progress", **job.snapshot()})

        try:
            job.result = self._runner(job, report)
            job.status = "done"
            job.progress = 1.0
            self._emit(job, {"type": "done", **job.snapshot()})
        except UserError as e:
            job.status = "error"
            job.error = str(e)
            self._emit(job, {"type": "error", **job.snapshot()})
        except Exception as e:  # noqa: BLE001
            log.exception("工作 %s 失敗", job.id)
            job.status = "error"
            job.error = f"處理失敗：{e}"
            self._emit(job, {"type": "error", **job.snapshot()})
