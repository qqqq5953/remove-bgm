// 時間格式驗證，規則與後端 remove_bgm/timecode.py 相同

export class TimecodeError extends Error {}

const PART = /^\d+(\.\d+)?$/;

/** 解析 "83"、"1:23"、"01:02:03"、"1:23.5" 成秒數；空字串回傳 null。 */
export function parseTimecode(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;

  const parts = trimmed.split(":");
  if (parts.length > 3 || parts.some((p) => !PART.test(p))) {
    throw new TimecodeError(`時間格式錯誤：「${trimmed}」，請用 分:秒 或 時:分:秒，例如 1:23 或 01:02:03`);
  }
  if (parts.slice(0, -1).some((p) => p.includes("."))) {
    throw new TimecodeError(`時間格式錯誤：「${trimmed}」，只有秒數可以有小數`);
  }
  const values = parts.map(Number);
  if (values.slice(1).some((v) => v >= 60)) {
    throw new TimecodeError(`時間格式錯誤：「${trimmed}」，分和秒必須小於 60`);
  }
  return values.reduce((acc, v) => acc * 60 + v, 0);
}

/** 檢查開始、結束時間，回傳錯誤訊息清單（沒有錯誤時為空陣列）。 */
export function validateRange(startText: string, endText: string): string[] {
  const errors: string[] = [];
  let start: number | null = null;
  let end: number | null = null;
  try {
    start = parseTimecode(startText);
  } catch (e) {
    errors.push(`開始時間：${(e as Error).message}`);
  }
  try {
    end = parseTimecode(endText);
  } catch (e) {
    errors.push(`結束時間：${(e as Error).message}`);
  }
  if (end !== null && end <= (start ?? 0)) {
    errors.push("結束時間必須晚於開始時間");
  }
  return errors;
}
