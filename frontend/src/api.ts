// 後端 API 的型別與呼叫

export interface Health {
  ffmpeg: boolean;
  video_encoder: string | null;
  yt_dlp: string | null;
  model_ready: boolean;
  device: string;
  busy: boolean;
}

export interface OutputFile {
  name: string;
  kind: "audio" | "video";
  label: string;
  url: string;
}

export interface JobSnapshot {
  id: string;
  status: "queued" | "running" | "done" | "error";
  stage: string | null;
  stage_label: string | null;
  progress: number;
  error: string | null;
  title?: string;
  folder?: string;
  duration?: number;
  files?: OutputFile[];
  zip_name?: string;
  zip_url?: string;
}

export interface JobEvent extends JobSnapshot {
  type: "progress" | "done" | "error";
}

export interface Api {
  health(): Promise<Health>;
  createJob(data: FormData): Promise<JobSnapshot>;
  subscribe(jobId: string, onEvent: (event: JobEvent) => void): () => void;
  reveal(jobId: string): Promise<void>;
}

export class ApiError extends Error {}

async function parse<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let message = `伺服器錯誤（${resp.status}）`;
    try {
      const body = await resp.json();
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      // 回應不是 JSON 時沿用預設訊息
    }
    throw new ApiError(message);
  }
  return resp.json() as Promise<T>;
}

async function request(input: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init);
  } catch {
    throw new ApiError("無法連線到本機伺服器，請確認 start.command 的視窗還開著");
  }
}

export const httpApi: Api = {
  async health() {
    return parse<Health>(await request("/api/health"));
  },

  async createJob(data) {
    return parse<JobSnapshot>(await request("/api/jobs", { method: "POST", body: data }));
  },

  subscribe(jobId, onEvent) {
    const source = new EventSource(`/api/jobs/${jobId}/events`);
    source.onmessage = (msg) => {
      const event = JSON.parse(msg.data) as JobEvent;
      onEvent(event);
      if (event.type !== "progress") source.close();
    };
    source.onerror = () => {
      // 連線中斷時改查一次工作狀態，避免畫面卡住
      if (source.readyState === EventSource.CLOSED) return;
      source.close();
      fetch(`/api/jobs/${jobId}`)
        .then((r) => r.json())
        .then((job: JobSnapshot) => {
          if (job.status === "done" || job.status === "error") {
            onEvent({ ...job, type: job.status });
          } else {
            onEvent({ ...job, type: "error", error: "與伺服器的連線中斷，請重新整理頁面" });
          }
        })
        .catch(() => onEvent({ id: jobId, type: "error", status: "error", stage: null, stage_label: null, progress: 0, error: "與伺服器的連線中斷，請確認 start.command 的視窗還開著" }));
    };
    return () => source.close();
  },

  async reveal(jobId) {
    await parse(await request(`/api/jobs/${jobId}/reveal`, { method: "POST" }));
  },
};
