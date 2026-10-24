// 端到端測試：啟動真實後端（分離器與 yt-dlp 用測試替身），用 Playwright 操作建置好的前端。
// 執行：node --test e2e/app.e2e.cjs（需要 playwright 與 chromium）

const { test, before, after } = require("node:test");
const assert = require("node:assert/strict");
const { spawn, execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { chromium } = require("playwright");

const ROOT = path.resolve(__dirname, "..");
const PORT = 8799;
const BASE = `http://127.0.0.1:${PORT}`;
const WORK = fs.mkdtempSync(path.join(os.tmpdir(), "remove-bgm-e2e-"));
const SHOTS = process.env.E2E_SCREENSHOTS || path.join(WORK, "screenshots");
fs.mkdirSync(SHOTS, { recursive: true });

let serverProc;
let browser;
let page;
let uploadVideo;

function rmsOfWav24(buf) {
  // 解析 PCM 24-bit WAV 的 data chunk，計算 RMS
  let offset = 12;
  while (offset < buf.length) {
    const id = buf.toString("ascii", offset, offset + 4);
    const size = buf.readUInt32LE(offset + 4);
    if (id === "data") {
      let sum = 0;
      const n = Math.floor(size / 3);
      for (let i = 0; i < n; i++) {
        const v = buf.readIntLE(offset + 8 + i * 3, 3) / 8388608;
        sum += v * v;
      }
      return Math.sqrt(sum / n);
    }
    offset += 8 + size + (size % 2);
  }
  throw new Error("找不到 data chunk");
}

function zipEntries(file) {
  return execFileSync("python3", ["-c", "import sys,zipfile;print('\\n'.join(zipfile.ZipFile(sys.argv[1]).namelist()))", file])
    .toString()
    .trim()
    .split("\n");
}

async function waitForServer() {
  for (let i = 0; i < 100; i++) {
    try {
      const r = await fetch(`${BASE}/api/health`);
      if (r.ok) return;
    } catch {}
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error("伺服器沒有啟動");
}

async function fillAndSubmit({ url, file, start = "", end = "", dialogueDb, effectsDb, music = false }) {
  if (file) {
    await page.getByRole("tab", { name: "上傳檔案" }).click();
    await page.setInputFiles("#file", file);
  } else {
    await page.getByRole("tab", { name: "YouTube 網址" }).click();
    await page.fill("#url", url);
  }
  await page.fill("#start", start);
  await page.fill("#end", end);
  if (dialogueDb !== undefined) await page.locator("#dialogue_db").fill(String(dialogueDb));
  if (effectsDb !== undefined) await page.locator("#effects_db").fill(String(effectsDb));
  await page.locator("#export_music").setChecked(music);
  await page.click("#submit");
}

async function waitForResults() {
  await page.waitForSelector("#results:not([hidden])", { timeout: 60_000 });
}

async function backToForm() {
  await page.getByRole("button", { name: "處理另一段影片" }).click();
  await page.waitForSelector("#job-form:not([hidden])");
}

before(async () => {
  uploadVideo = path.join(WORK, "我的片段.mkv");
  execFileSync("ffmpeg", [
    "-hide_banner", "-loglevel", "error", "-y",
    "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=6",
    "-f", "lavfi", "-i", "aevalsrc='0.3*sin(2*PI*220*t)'|'0.3*sin(2*PI*330*t)':s=48000:d=6",
    "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-c:a", "libopus", "-shortest", uploadVideo,
  ]);
  serverProc = spawn("uv", ["run", "--project", path.join(ROOT, "backend"), "python", path.join(__dirname, "e2e_server.py"), String(PORT), WORK], {
    stdio: ["ignore", "inherit", "inherit"],
  });
  await waitForServer();
  // 瀏覽器需要 UTF-8 locale 才能處理中文檔名（macOS 預設就是；CI 容器常常是 C）
  browser = await chromium.launch({ env: { ...process.env, LANG: "C.UTF-8", LC_ALL: "C.UTF-8" } });
  page = await browser.newPage({ acceptDownloads: true, viewport: { width: 900, height: 1100 } });
  await page.goto(BASE);
});

after(async () => {
  await browser?.close();
  serverProc?.kill();
  if (!process.env.E2E_SCREENSHOTS) fs.rmSync(WORK, { recursive: true, force: true });
});

test("首頁載入並顯示環境狀態", async () => {
  await page.waitForFunction(() => !document.getElementById("status").textContent.includes("檢查環境中"));
  const status = await page.textContent("#status");
  assert.match(status, /運算：CPU/);
  assert.match(status, /模型：已就緒/);
  await page.screenshot({ path: path.join(SHOTS, "01-form.png"), fullPage: true });
});

test("前端即時驗證：網址與時間格式", async () => {
  await page.fill("#url", "");
  await page.click("#submit");
  assert.deepEqual(await page.locator("#errors li").allTextContents(), ["請貼上 YouTube 影片網址"]);
  await page.fill("#url", "youtube.com/watch?v=x");
  await page.fill("#start", "1:75");
  const errors = await page.locator("#errors li").allTextContents();
  assert.match(errors[0], /網址格式錯誤/);
  assert.match(errors[1], /開始時間：時間格式錯誤/);
  await page.screenshot({ path: path.join(SHOTS, "02-validation.png"), fullPage: true });
  await page.fill("#start", "");
  await page.fill("#url", "");
});

test("上傳檔案 + 擷取片段 + 輸出原配樂：完整流程", async () => {
  // 讓送出請求晚一點回應，確認等待期間表單是鎖定的
  let release;
  let continued;
  const held = new Promise((r) => (release = r));
  const passed = new Promise((r) => (continued = r));
  await page.route("**/api/jobs", async (route) => {
    await held;
    await route.continue();
    continued();
  });
  await fillAndSubmit({ file: uploadVideo, start: "0:01", end: "0:04", music: true });
  assert.equal(await page.locator("#form-fields").evaluate((el) => el.disabled), true);
  assert.equal(await page.textContent("#submit"), "處理中…");
  assert.equal(await page.locator("#progress").isVisible(), true);
  release();
  await passed;
  await page.unroute("**/api/jobs");
  await waitForResults();
  await page.screenshot({ path: path.join(SHOTS, "03-results.png"), fullPage: true });

  const labels = await page.locator(".stem-label").allTextContents();
  assert.deepEqual(labels, ["對白", "音效", "對白＋音效（混音）", "原配樂"]);
  assert.equal(await page.locator("#results video").count(), 1);
  assert.match(await page.textContent("#results"), /我的片段_0001-0004/);

  // 每個試聽檔都能取得
  for (const src of await page.locator("#results audio").evaluateAll((els) => els.map((e) => e.src))) {
    const r = await page.request.get(src);
    assert.equal(r.status(), 200);
  }
  // 影片支援 Range（瀏覽器拖動進度條需要）
  const videoSrc = await page.locator("#results video").getAttribute("src");
  const part = await page.request.get(BASE + videoSrc, { headers: { Range: "bytes=0-1023" } });
  assert.equal(part.status(), 206);

  // 下載 zip
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("link", { name: /下載 Logic 素材包/ }).click()]);
  assert.equal(download.suggestedFilename(), "我的片段_0001-0004.zip");
  const zipPath = path.join(WORK, download.suggestedFilename());
  await download.saveAs(zipPath);
  assert.deepEqual(zipEntries(zipPath).sort(), [
    "我的片段_0001-0004/dialogue+effects.wav",
    "我的片段_0001-0004/dialogue.wav",
    "我的片段_0001-0004/effects.wav",
    "我的片段_0001-0004/original_music.wav",
    "我的片段_0001-0004/video_no_music.mov",
  ]);
});

test("沒勾原配樂：試聽與 zip 都不含原配樂", async () => {
  await backToForm();
  await fillAndSubmit({ file: uploadVideo, end: "0:02", music: false });
  await waitForResults();
  assert.deepEqual(await page.locator(".stem-label").allTextContents(), ["對白", "音效", "對白＋音效（混音）"]);
  const href = await page.getByRole("link", { name: /下載 Logic 素材包/ }).getAttribute("href");
  const zip = path.join(WORK, "no-music.zip");
  fs.writeFileSync(zip, await (await page.request.get(BASE + href)).body());
  assert.equal(zipEntries(zip).some((n) => n.endsWith("original_music.wav")), false);
});

test("音量滑桿會改變混音結果", async () => {
  async function mixRms(dialogueDb, effectsDb) {
    await backToForm();
    await fillAndSubmit({ file: uploadVideo, end: "0:02", dialogueDb, effectsDb });
    await waitForResults();
    const src = await page.locator("#results a.stem-file", { hasText: "dialogue+effects.wav" }).getAttribute("href");
    return rmsOfWav24(await (await page.request.get(BASE + src)).body());
  }
  const base = await mixRms(0, 0);
  assert.equal(await page.textContent("#dialogue_db_value"), "0 dB");
  const changed = await mixRms(-12, 6);
  assert.equal(await page.textContent("#dialogue_db_value"), "-12 dB");
  assert.equal(await page.textContent("#effects_db_value"), "+6 dB");
  // 假分離器：對白 0.5、音效 0.2 → (0.5*0.2512 + 0.2*1.9953) / 0.7
  const expected = (0.5 * 0.2512 + 0.2 * 1.9953) / 0.7;
  assert.ok(Math.abs(changed / base - expected) < 0.01, `比例 ${changed / base}，預期 ${expected}`);
});

test("拖放檔案到上傳區", async () => {
  await backToForm();
  await page.getByRole("tab", { name: "上傳檔案" }).click();
  const bytes = fs.readFileSync(uploadVideo).toString("base64");
  await page.evaluate(async (b64) => {
    const data = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const dt = new DataTransfer();
    dt.items.add(new File([data], "拖放的影片.mkv", { type: "video/x-matroska" }));
    document.getElementById("dropzone").dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
  }, bytes);
  assert.equal(await page.textContent("#file-label"), "已選擇：拖放的影片.mkv");
  await page.fill("#start", "");
  await page.fill("#end", "0:01");
  await page.click("#submit");
  await waitForResults();
  assert.match(await page.textContent("#results"), /拖放的影片_0000-0001/);
});

test("YouTube 網址：下載片段並處理", async () => {
  await backToForm();
  await fillAndSubmit({ url: "https://www.youtube.com/watch?v=ok", start: "0:02", end: "0:05" });
  // 進度畫面會顯示階段
  await page.waitForSelector("#progress:not([hidden])");
  await waitForResults();
  assert.match(await page.textContent("#results"), /測試影片_ Fake_Title_0002-0005/);
  await page.screenshot({ path: path.join(SHOTS, "04-youtube-results.png"), fullPage: true });
});

test("YouTube 下載失敗時顯示中文錯誤", async () => {
  await backToForm();
  await fillAndSubmit({ url: "https://www.youtube.com/watch?v=unavailable" });
  await page.waitForSelector("#errors:not([hidden])", { timeout: 30_000 });
  assert.match(await page.textContent("#errors"), /影片無法觀看/);
  assert.equal(await page.locator("#form-fields").evaluate((el) => el.disabled), false);
  await page.screenshot({ path: path.join(SHOTS, "05-error.png"), fullPage: true });
});

test("片段超出影片長度時顯示錯誤", async () => {
  await fillAndSubmit({ file: uploadVideo, start: "0:30" });
  await page.waitForFunction(() => document.getElementById("errors").textContent.includes("超出影片長度"), null, { timeout: 30_000 });
});

test("「在 Finder 中顯示」：非 macOS 時顯示說明", async () => {
  await fillAndSubmit({ file: uploadVideo, start: "", end: "0:01" });
  await waitForResults();
  await page.getByRole("button", { name: "在 Finder 中顯示" }).click();
  await page.waitForFunction(() => document.getElementById("errors").textContent.includes("只支援 macOS"));
});
