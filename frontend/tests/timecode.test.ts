import { describe, expect, it } from "vitest";
import { TimecodeError, parseTimecode, validateRange } from "../src/timecode";

describe("parseTimecode（與後端規則相同）", () => {
  it.each([
    ["", null],
    ["   ", null],
    ["83", 83],
    ["1:23", 83],
    ["01:23", 83],
    ["1:23.5", 83.5],
    ["1:02:03", 3723],
    ["01:02:03.25", 3723.25],
    ["90:00", 5400],
    [" 1:23 ", 83],
  ])("%s → %s", (text, expected) => {
    expect(parseTimecode(text)).toBe(expected);
  });

  it.each(["abc", "1:2:3:4", "1:60", "1:61:00", "1::2", "-5", "1.5:20", "1:23,5", ":30", "1:"])(
    "拒絕 %s",
    (text) => {
      expect(() => parseTimecode(text)).toThrow(TimecodeError);
      expect(() => parseTimecode(text)).toThrow(/時間格式錯誤/);
    },
  );
});

describe("validateRange", () => {
  it("沒有填寫時沒有錯誤", () => {
    expect(validateRange("", "")).toEqual([]);
  });

  it("結束早於或等於開始", () => {
    expect(validateRange("1:00", "0:30")).toEqual(["結束時間必須晚於開始時間"]);
    expect(validateRange("", "0")).toEqual(["結束時間必須晚於開始時間"]);
  });

  it("標示是哪個欄位格式錯誤", () => {
    const errors = validateRange("x", "1:99");
    expect(errors).toHaveLength(2);
    expect(errors[0]).toMatch(/^開始時間：/);
    expect(errors[1]).toMatch(/^結束時間：/);
  });
});
