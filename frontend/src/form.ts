import { validateRange } from "./timecode";

export type SourceMode = "url" | "file";

export interface FormValues {
  mode: SourceMode;
  url: string;
  file: File | null;
  start: string;
  end: string;
  dialogueDb: number;
  effectsDb: number;
  exportMusic: boolean;
}

function input(root: ParentNode, id: string): HTMLInputElement {
  const el = root.querySelector<HTMLInputElement>(`#${id}`);
  if (!el) throw new Error(`找不到 #${id}`);
  return el;
}

export function currentMode(root: ParentNode): SourceMode {
  const selected = root.querySelector<HTMLElement>('.tab[aria-selected="true"]');
  return (selected?.dataset.mode as SourceMode) ?? "url";
}

export function readForm(root: ParentNode): FormValues {
  const fileInput = input(root, "file");
  return {
    mode: currentMode(root),
    url: input(root, "url").value.trim(),
    file: fileInput.files && fileInput.files.length > 0 ? fileInput.files[0] : null,
    start: input(root, "start").value,
    end: input(root, "end").value,
    dialogueDb: Number(input(root, "dialogue_db").value),
    effectsDb: Number(input(root, "effects_db").value),
    exportMusic: input(root, "export_music").checked,
  };
}

export function validateForm(values: FormValues): string[] {
  const errors: string[] = [];
  if (values.mode === "url") {
    if (!values.url) {
      errors.push("請貼上 YouTube 影片網址");
    } else if (!/^https?:\/\/\S+$/i.test(values.url)) {
      errors.push("網址格式錯誤，請貼上完整的影片網址，例如 https://www.youtube.com/watch?v=...");
    }
  } else if (!values.file) {
    errors.push("請選擇要上傳的影片檔案");
  }
  return errors.concat(validateRange(values.start, values.end));
}

export function toFormData(values: FormValues): FormData {
  const data = new FormData();
  if (values.mode === "url") {
    data.set("url", values.url);
  } else if (values.file) {
    data.set("file", values.file);
  }
  data.set("start", values.start.trim());
  data.set("end", values.end.trim());
  data.set("dialogue_db", String(values.dialogueDb));
  data.set("effects_db", String(values.effectsDb));
  data.set("export_music", String(values.exportMusic));
  return data;
}

export function formatDb(value: number): string {
  return value > 0 ? `+${value} dB` : `${value} dB`;
}
