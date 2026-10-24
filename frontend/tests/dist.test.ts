// @vitest-environment node
import { mkdtempSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative, resolve } from "node:path";
import { build } from "vite";
import { expect, it } from "vitest";

const root = resolve(__dirname, "..");

function listFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true, recursive: true })
    .filter((d) => d.isFile())
    .map((d) => relative(dir, join(d.parentPath, d.name)))
    .sort();
}

// dist/ 會 commit 進 repo（使用者不需要 Node.js），這裡確認它跟原始碼一致
it("dist/ 與目前原始碼的建置結果相同（不一致時請執行 npm run build）", async () => {
  const out = mkdtempSync(join(tmpdir(), "remove-bgm-dist-"));
  try {
    await build({ root, logLevel: "silent", build: { outDir: out, emptyOutDir: true } });
    const dist = resolve(root, "dist");
    expect(listFiles(dist)).toEqual(listFiles(out));
    for (const file of listFiles(out)) {
      expect(readFileSync(join(dist, file), "utf8"), file).toBe(readFileSync(join(out, file), "utf8"));
    }
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
}, 60_000);
