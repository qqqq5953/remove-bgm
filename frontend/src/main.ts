import "./style.css";
import { type Api, type JobEvent, httpApi } from "./api";
import { currentMode, formatDb, readForm, toFormData, validateForm } from "./form";
import { renderResults } from "./results";

function $(root: Document, id: string): HTMLElement {
  const node = root.getElementById(id);
  if (!node) throw new Error(`找不到 #${id}`);
  return node;
}

export function init(doc: Document, api: Api): void {
  const form = $(doc, "job-form") as HTMLFormElement;
  const fields = $(doc, "form-fields") as HTMLFieldSetElement;
  const submit = $(doc, "submit") as HTMLButtonElement;
  const errors = $(doc, "errors");
  const status = $(doc, "status");
  const progress = $(doc, "progress");
  const stage = $(doc, "stage");
  const bar = $(doc, "progress-bar") as HTMLProgressElement;
  const progressText = $(doc, "progress-text");
  const results = $(doc, "results");
  const fileInput = $(doc, "file") as HTMLInputElement;
  const fileLabel = $(doc, "file-label");
  const dropzone = $(doc, "dropzone");
  let touched = false;
  let currentJob: string | null = null;

  function showErrors(messages: string[]): void {
    errors.replaceChildren(...messages.map((m) => Object.assign(doc.createElement("li"), { textContent: m })));
    errors.hidden = messages.length === 0;
  }

  function validate(): string[] {
    const messages = validateForm(readForm(doc));
    if (touched) showErrors(messages);
    return messages;
  }

  function setBusy(busy: boolean): void {
    fields.disabled = busy;
    submit.disabled = busy;
    submit.textContent = busy ? "處理中…" : "開始處理";
  }

  function setProgress(event: Pick<JobEvent, "progress" | "stage_label">): void {
    const pct = Math.round(event.progress * 100);
    bar.value = pct;
    progressText.textContent = `${pct}%`;
    if (event.stage_label) stage.textContent = event.stage_label;
  }

  // 分頁切換
  for (const tab of doc.querySelectorAll<HTMLButtonElement>(".tab")) {
    tab.addEventListener("click", () => {
      for (const t of doc.querySelectorAll<HTMLButtonElement>(".tab")) {
        t.setAttribute("aria-selected", String(t === tab));
      }
      for (const panel of doc.querySelectorAll<HTMLElement>(".tab-panel")) {
        panel.hidden = panel.dataset.panel !== tab.dataset.mode;
      }
      validate();
    });
  }

  // 檔案選擇與拖放
  function showFileName(): void {
    const file = fileInput.files?.[0];
    fileLabel.textContent = file ? `已選擇：${file.name}` : "把影片拖到這裡，或點一下選擇檔案";
    dropzone.classList.toggle("has-file", Boolean(file));
  }
  fileInput.addEventListener("change", () => {
    showFileName();
    validate();
  });
  dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("dragging");
  });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragging"));
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragging");
    if (e.dataTransfer?.files.length) {
      fileInput.files = e.dataTransfer.files;
      showFileName();
      validate();
    }
  });

  // 輸入時即時驗證
  for (const id of ["url", "start", "end"]) {
    $(doc, id).addEventListener("input", () => validate());
  }

  // 音量滑桿顯示數值
  for (const id of ["dialogue_db", "effects_db"]) {
    const slider = $(doc, id) as HTMLInputElement;
    const output = $(doc, `${id}_value`);
    slider.addEventListener("input", () => {
      output.textContent = formatDb(Number(slider.value));
    });
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    touched = true;
    if (validate().length > 0) return;

    setBusy(true);
    results.hidden = true;
    setProgress({ progress: 0, stage_label: currentMode(doc) === "url" ? "下載影片中…" : "上傳檔案中…" });
    progress.hidden = false;

    try {
      const job = await api.createJob(toFormData(readForm(doc)));
      currentJob = job.id;
      api.subscribe(job.id, (event) => {
        if (event.type === "progress") {
          setProgress(event);
        } else if (event.type === "done") {
          progress.hidden = true;
          renderResults(results, event, {
            onReveal: () => {
              if (currentJob) api.reveal(currentJob).catch((err: Error) => showErrors([err.message]));
            },
            onReset: () => {
              results.hidden = true;
              form.hidden = false;
              showErrors([]);
            },
          });
          results.hidden = false;
          form.hidden = true;
          setBusy(false);
          refreshStatus();
        } else {
          progress.hidden = true;
          showErrors([event.error ?? "處理失敗"]);
          setBusy(false);
        }
      });
    } catch (err) {
      progress.hidden = true;
      showErrors([(err as Error).message]);
      setBusy(false);
    }
  });

  async function refreshStatus(): Promise<void> {
    try {
      const h = await api.health();
      const parts = [
        h.device === "mps" ? "運算：Apple GPU（MPS）" : `運算：${h.device.toUpperCase()}`,
        h.model_ready ? "模型：已就緒" : "模型：第一次處理時會自動下載（約數百 MB）",
      ];
      if (!h.ffmpeg) parts.push("⚠ 找不到 ffmpeg，請重新執行 start.command");
      status.textContent = parts.join("　·　");
      status.classList.toggle("warn", !h.ffmpeg);
    } catch (err) {
      status.textContent = (err as Error).message;
      status.classList.add("warn");
    }
  }

  refreshStatus();
}

if (typeof window !== "undefined" && !import.meta.env?.VITEST) {
  init(document, httpApi);
}
