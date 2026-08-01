import { describe, test, expect } from "vitest";
import { formatDate, formatDuration, formatSize } from "./format";

describe("formatDate", () => {
  test("returns em dash for null, undefined, and empty string", () => {
    expect(formatDate(null)).toBe("—");
    expect(formatDate(undefined)).toBe("—");
    expect(formatDate("")).toBe("—");
  });

  test("truncates a full ISO timestamp to YYYY-MM-DD", () => {
    expect(formatDate("2024-03-15T12:34:56+00:00")).toBe("2024-03-15");
  });
});

describe("formatDuration", () => {
  test("returns em dash for null and for undefined", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(undefined)).toBe("—");
  });

  test("formats sub-hour durations without an hour segment", () => {
    expect(formatDuration(754)).toBe("12:34");
    expect(formatDuration(59)).toBe("0:59");
    expect(formatDuration(3599)).toBe("59:59");
  });

  test("formats >= 1 hour durations with hour segment and padded minutes", () => {
    expect(formatDuration(3661)).toBe("1:01:01");
  });

  test("boundary at exactly 3600 seconds rolls into the hour format", () => {
    expect(formatDuration(3600)).toBe("1:00:00");
  });
});

describe("formatSize", () => {
  test("returns em dash for null and undefined", () => {
    expect(formatSize(null)).toBe("—");
    expect(formatSize(undefined)).toBe("—");
  });

  test("returns em dash for 0 bytes — zero is falsy, so a zero-byte file renders as missing (current behavior)", () => {
    expect(formatSize(0)).toBe("—");
  });

  test("sub-10MB values get one decimal place", () => {
    expect(formatSize(9961472)).toBe("9.5 MB"); // 9.5 * 1024 * 1024
    expect(formatSize(524288)).toBe("0.5 MB"); // 0.5 * 1024 * 1024
  });

  test("just under 10MB stays in the decimal band, so toFixed displays '10.0 MB'", () => {
    // 10 * 1024 * 1024 - 1: mb = 9.9999990... < 10, but toFixed(1) rounds the display up.
    expect(formatSize(10485759)).toBe("10.0 MB");
  });

  test("at/above 10MB switches to integer MB", () => {
    expect(formatSize(10485760)).toBe("10 MB"); // exactly 10 * 1024 * 1024
  });

  test("just under 1GB stays in the MB band, so Math.round displays '1024 MB' rather than '1.0 GB'", () => {
    // 1024 * 1024 * 1024 - 1: mb = 1023.9999990... < 1024, rounds to 1024 in MB units.
    expect(formatSize(1073741823)).toBe("1024 MB");
  });

  test("at/above 1GB switches to GB with one decimal", () => {
    expect(formatSize(1073741824)).toBe("1.0 GB"); // exactly 1024 * 1024 * 1024
    expect(formatSize(1610612736)).toBe("1.5 GB"); // 1.5 GB
  });
});
