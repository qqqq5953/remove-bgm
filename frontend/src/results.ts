import type { JobSnapshot } from "./api";

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: Partial<HTMLElementTagNameMap[K]> = {},
  children: (Node | string)[] = [],
): HTMLElementTagNameMap[K] {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

export interface ResultHandlers {
  onReveal(): void;
  onReset(): void;
}

/** 依工作的輸出檔案產生結果畫面：影片預覽、各軌試聽、下載與 Finder 按鈕。 */
export function renderResults(container: HTMLElement, job: JobSnapshot, handlers: ResultHandlers): void {
  const files = job.files ?? [];
  const video = files.find((f) => f.kind === "video");
  const audios = files.filter((f) => f.kind === "audio");

  const children: Node[] = [el("h2", { textContent: "完成！" }), el("p", { className: "result-title", textContent: job.title ?? "" })];

  if (video) {
    children.push(
      el("figure", { className: "video-preview" }, [
        el("video", { src: video.url, controls: true, preload: "metadata" }),
        el("figcaption", { textContent: video.label }),
      ]),
    );
  }

  const list = el("ul", { className: "stems" });
  for (const file of audios) {
    list.append(
      el("li", { className: "stem" }, [
        el("span", { className: "stem-label", textContent: file.label }),
        el("audio", { src: file.url, controls: true, preload: "none" }),
        el("a", { href: file.url, download: file.name, className: "stem-file", textContent: file.name }),
      ]),
    );
  }
  children.push(el("h3", { textContent: "試聽" }), list);

  const reveal = el("button", { type: "button", className: "secondary", textContent: "在 Finder 中顯示" });
  reveal.addEventListener("click", () => handlers.onReveal());
  const reset = el("button", { type: "button", className: "secondary", textContent: "處理另一段影片" });
  reset.addEventListener("click", () => handlers.onReset());

  children.push(
    el("div", { className: "actions" }, [
      el("a", { href: job.zip_url ?? "#", className: "primary button", download: job.zip_name ?? "", textContent: "下載 Logic 素材包（.zip）" }),
      reveal,
      reset,
    ]),
    el("p", { className: "hint", textContent: `檔案位置：${job.folder ?? ""}` }),
    el("p", { className: "hint", textContent: "Logic Pro：「檔案 > 影片 > 打開影片」載入影片，再把 wav 拖進專案（專案取樣率設為 48kHz），從第一小節開頭對齊即可同步。" }),
  );

  container.replaceChildren(...children);
}
