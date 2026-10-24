import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Api, Health, JobEvent, JobSnapshot } from "../src/api";
import { init } from "../src/main";

const html = readFileSync(resolve(__dirname, "../index.html"), "utf8");

const health: Health = {
  ffmpeg: true,
  video_encoder: "h264_videotoolbox",
  yt_dlp: "2026.10.21",
  model_ready: false,
  device: "mps",
  busy: false,
};

const doneJob: JobSnapshot = {
  id: "job1",
  status: "done",
  stage: "package",
  stage_label: "打包",
  progress: 1,
  error: null,
  title: "我的影片",
  folder: "/Users/me/Movies/remove-bgm/我的影片_0001-0004",
  duration: 3,
  files: [
    { name: "video_no_music.mov", kind: "video", label: "去掉配樂的影片", url: "/api/jobs/job1/files/video_no_music.mov" },
    { name: "dialogue.wav", kind: "audio", label: "對白", url: "/api/jobs/job1/files/dialogue.wav" },
    { name: "effects.wav", kind: "audio", label: "音效", url: "/api/jobs/job1/files/effects.wav" },
    { name: "dialogue+effects.wav", kind: "audio", label: "對白＋音效（混音）", url: "/api/jobs/job1/files/dialogue+effects.wav" },
  ],
  zip_name: "我的影片_0001-0004.zip",
  zip_url: "/api/jobs/job1/zip",
};

function makeApi() {
  let listener: ((e: JobEvent) => void) | null = null;
  const api = {
    health: vi.fn(async () => health),
    createJob: vi.fn(async (_: FormData) => ({ ...doneJob, status: "queued", progress: 0, files: undefined }) as JobSnapshot),
    subscribe: vi.fn((_: string, cb: (e: JobEvent) => void) => {
      listener = cb;
      return () => {};
    }),
    reveal: vi.fn(async () => {}),
  } satisfies Api;
  return { api, emit: (e: JobEvent) => listener?.(e) };
}

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const flush = () => new Promise((r) => setTimeout(r, 0));

function type(id: string, value: string) {
  const input = $<HTMLInputElement>(id);
  input.value = value;
  input.dispatchEvent(new Event("input"));
}

function submit() {
  $<HTMLFormElement>("job-form").dispatchEvent(new Event("submit", { cancelable: true }));
}

function errorTexts() {
  return Array.from($("errors").querySelectorAll("li")).map((li) => li.textContent);
}

function selectFile(name = "clip.mp4") {
  const file = new File(["data"], name, { type: "video/mp4" });
  const dt = Object.assign([file], { item: () => file }) as unknown as FileList;
  Object.defineProperty($("file"), "files", { value: dt, configurable: true });
  $("file").dispatchEvent(new Event("change"));
  return file;
}

beforeEach(() => {
  document.documentElement.innerHTML = html.replace(/<script[\s\S]*?<\/script>/g, "");
});

describe("初始狀態", () => {
  it("顯示運算裝置與模型狀態", async () => {
    const { api } = makeApi();
    init(document, api);
    await flush();
    expect($("status").textContent).toContain("Apple GPU（MPS）");
    expect($("status").textContent).toContain("第一次處理時會自動下載");
  });

  it("沒有 ffmpeg 時顯示警告", async () => {
    const { api } = makeApi();
    api.health.mockResolvedValueOnce({ ...health, ffmpeg: false, device: "cpu", model_ready: true });
    init(document, api);
    await flush();
    expect($("status").textContent).toContain("找不到 ffmpeg");
    expect($("status").textContent).toContain("運算：CPU");
    expect($("status").classList.contains("warn")).toBe(true);
  });

  it("預設是網址分頁，結果與進度區塊隱藏", () => {
    init(document, makeApi().api);
    expect(document.querySelector('[data-panel="url"]')!.hasAttribute("hidden")).toBe(false);
    expect(document.querySelector('[data-panel="file"]')!.hasAttribute("hidden")).toBe(true);
    expect($("progress").hidden).toBe(true);
    expect($("results").hidden).toBe(true);
  });
});

describe("表單", () => {
  it("切換分頁", () => {
    init(document, makeApi().api);
    (document.querySelector('[data-mode="file"]') as HTMLButtonElement).click();
    expect(document.querySelector('[data-panel="file"]')!.hasAttribute("hidden")).toBe(false);
    expect(document.querySelector('[data-panel="url"]')!.hasAttribute("hidden")).toBe(true);
    expect(document.querySelector('[data-mode="file"]')!.getAttribute("aria-selected")).toBe("true");
  });

  it("滑桿顯示 dB 數值", () => {
    init(document, makeApi().api);
    type("dialogue_db", "6");
    type("effects_db", "-12");
    expect($("dialogue_db_value").textContent).toBe("+6 dB");
    expect($("effects_db_value").textContent).toBe("-12 dB");
  });

  it("沒填網址就送出會顯示錯誤，不呼叫 API", async () => {
    const { api } = makeApi();
    init(document, api);
    submit();
    await flush();
    expect(errorTexts()).toEqual(["請貼上 YouTube 影片網址"]);
    expect(api.createJob).not.toHaveBeenCalled();
  });

  it("送出一次後，輸入時即時更新錯誤", async () => {
    init(document, makeApi().api);
    submit();
    type("url", "youtube.com/abc");
    expect(errorTexts()[0]).toMatch(/網址格式錯誤/);
    type("url", "https://www.youtube.com/watch?v=abc");
    type("start", "1:99");
    expect(errorTexts()).toEqual([expect.stringMatching(/^開始時間：時間格式錯誤/)]);
    type("start", "1:00");
    type("end", "0:30");
    expect(errorTexts()).toEqual(["結束時間必須晚於開始時間"]);
    type("end", "2:00");
    expect($("errors").hidden).toBe(true);
  });

  it("上傳分頁沒選檔案會提示", async () => {
    init(document, makeApi().api);
    (document.querySelector('[data-mode="file"]') as HTMLButtonElement).click();
    submit();
    expect(errorTexts()).toEqual(["請選擇要上傳的影片檔案"]);
  });

  it("送出網址：FormData 內容正確", async () => {
    const { api } = makeApi();
    init(document, api);
    type("url", "https://www.youtube.com/watch?v=abc");
    type("start", "1:23");
    type("end", "3:45");
    type("dialogue_db", "3");
    $<HTMLInputElement>("export_music").checked = true;
    submit();
    await flush();
    const fd = api.createJob.mock.calls[0][0];
    expect(Object.fromEntries(fd.entries())).toEqual({
      url: "https://www.youtube.com/watch?v=abc",
      start: "1:23",
      end: "3:45",
      dialogue_db: "3",
      effects_db: "0",
      export_music: "true",
    });
  });

  it("送出檔案：只送檔案不送網址，並顯示檔名", async () => {
    const { api } = makeApi();
    init(document, api);
    type("url", "https://www.youtube.com/watch?v=ignored");
    (document.querySelector('[data-mode="file"]') as HTMLButtonElement).click();
    const file = selectFile("我的片段.mp4");
    expect($("file-label").textContent).toBe("已選擇：我的片段.mp4");
    submit();
    await flush();
    const fd = api.createJob.mock.calls[0][0];
    expect(fd.get("url")).toBeNull();
    expect((fd.get("file") as File).name).toBe(file.name);
    expect(fd.get("export_music")).toBe("false");
  });
});

describe("處理流程", () => {
  async function start(api: Api) {
    init(document, api);
    type("url", "https://www.youtube.com/watch?v=abc");
    submit();
    await flush();
  }

  it("處理中鎖住表單並顯示進度", async () => {
    const { api, emit } = makeApi();
    await start(api);
    expect($<HTMLFieldSetElement>("form-fields").disabled).toBe(true);
    expect($<HTMLButtonElement>("submit").disabled).toBe(true);
    expect($("progress").hidden).toBe(false);

    emit({ ...doneJob, type: "progress", status: "running", stage: "separate", stage_label: "分離對白／音樂／音效", progress: 0.456, files: undefined });
    expect($("stage").textContent).toBe("分離對白／音樂／音效");
    expect($<HTMLProgressElement>("progress-bar").value).toBe(46);
    expect($("progress-text").textContent).toBe("46%");
  });

  it("完成後顯示結果：影片、各軌試聽、下載、Finder", async () => {
    const { api, emit } = makeApi();
    await start(api);
    emit({ ...doneJob, type: "done" });

    expect($("results").hidden).toBe(false);
    expect($("job-form").hidden).toBe(true);
    expect($("progress").hidden).toBe(true);
    const results = $("results");
    expect(results.querySelector("video")!.getAttribute("src")).toBe("/api/jobs/job1/files/video_no_music.mov");
    const labels = Array.from(results.querySelectorAll(".stem-label")).map((n) => n.textContent);
    expect(labels).toEqual(["對白", "音效", "對白＋音效（混音）"]);
    expect(results.querySelectorAll("audio")).toHaveLength(3);
    const zip = results.querySelector<HTMLAnchorElement>("a.primary")!;
    expect(zip.getAttribute("href")).toBe("/api/jobs/job1/zip");
    expect(zip.getAttribute("download")).toBe("我的影片_0001-0004.zip");
    expect(results.textContent).toContain(doneJob.folder);

    const reveal = Array.from(results.querySelectorAll("button")).find((b) => b.textContent === "在 Finder 中顯示")!;
    reveal.click();
    expect(api.reveal).toHaveBeenCalledWith("job1");
  });

  it("勾選原配樂時多一個試聽軌", async () => {
    const { api, emit } = makeApi();
    await start(api);
    const files = [...doneJob.files!, { name: "original_music.wav", kind: "audio" as const, label: "原配樂", url: "/x" }];
    emit({ ...doneJob, files, type: "done" });
    expect($("results").querySelectorAll("audio")).toHaveLength(4);
  });

  it("純音訊結果沒有影片預覽", async () => {
    const { api, emit } = makeApi();
    await start(api);
    emit({ ...doneJob, files: doneJob.files!.filter((f) => f.kind === "audio"), type: "done" });
    expect($("results").querySelector("video")).toBeNull();
  });

  it("「處理另一段影片」回到表單，並可再次送出", async () => {
    const { api, emit } = makeApi();
    await start(api);
    emit({ ...doneJob, type: "done" });
    const reset = Array.from($("results").querySelectorAll("button")).find((b) => b.textContent === "處理另一段影片")!;
    reset.click();
    expect($("job-form").hidden).toBe(false);
    expect($("results").hidden).toBe(true);
    expect($<HTMLFieldSetElement>("form-fields").disabled).toBe(false);
    submit();
    await flush();
    expect(api.createJob).toHaveBeenCalledTimes(2);
  });

  it("工作失敗時顯示中文錯誤並解鎖表單", async () => {
    const { api, emit } = makeApi();
    await start(api);
    emit({ ...doneJob, type: "error", status: "error", error: "影片無法觀看（可能已下架、設為私人或有地區限制）", files: undefined });
    expect(errorTexts()).toEqual(["影片無法觀看（可能已下架、設為私人或有地區限制）"]);
    expect($("progress").hidden).toBe(true);
    expect($<HTMLFieldSetElement>("form-fields").disabled).toBe(false);
    expect($<HTMLButtonElement>("submit").textContent).toBe("開始處理");
  });

  it("送出被伺服器拒絕時顯示原因", async () => {
    const { api } = makeApi();
    api.createJob.mockRejectedValueOnce(new Error("目前已有工作在處理中，請等它完成後再送出"));
    await start(api);
    expect(errorTexts()).toEqual(["目前已有工作在處理中，請等它完成後再送出"]);
    expect($<HTMLButtonElement>("submit").disabled).toBe(false);
  });

  it("Finder 失敗時顯示錯誤", async () => {
    const { api, emit } = makeApi();
    api.reveal.mockRejectedValueOnce(new Error("「在 Finder 中顯示」只支援 macOS"));
    await start(api);
    emit({ ...doneJob, type: "done" });
    Array.from($("results").querySelectorAll("button")).find((b) => b.textContent === "在 Finder 中顯示")!.click();
    await flush();
    expect(errorTexts()).toEqual(["「在 Finder 中顯示」只支援 macOS"]);
  });
});
