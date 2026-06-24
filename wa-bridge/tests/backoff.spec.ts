import { describe, it, expect } from "vitest";
import { nextBackoffMs } from "../src/socket.js";

describe("nextBackoffMs (reconnect backoff schedule)", () => {
  it("returns 1000ms on the first attempt", () => {
    expect(nextBackoffMs(1, 60_000)).toBe(1000);
  });

  it("returns 2000ms on the second attempt", () => {
    expect(nextBackoffMs(2, 60_000)).toBe(2000);
  });

  it("returns 5000ms on the third attempt", () => {
    expect(nextBackoffMs(3, 60_000)).toBe(5000);
  });

  it("returns 60000ms once we hit the cap (attempt 7+)", () => {
    expect(nextBackoffMs(7, 60_000)).toBe(60_000);
    expect(nextBackoffMs(20, 60_000)).toBe(60_000);
  });

  it("caps at maxMs when the configured cap is lower than 60000", () => {
    // attempt 7 would be 60000, but maxMs is 30000 — should clamp
    expect(nextBackoffMs(7, 30_000)).toBe(30_000);
    // attempt 5 = 40000 in the table, but maxMs 20000
    expect(nextBackoffMs(5, 20_000)).toBe(20_000);
  });

  it("clamps attempt 0 to the first step (defensive)", () => {
    expect(nextBackoffMs(0, 60_000)).toBe(1000);
  });

  it("clamps negative attempt to the first step (defensive)", () => {
    expect(nextBackoffMs(-5, 60_000)).toBe(1000);
  });

  it("monotonically non-decreasing across the schedule", () => {
    const values = [1, 2, 3, 4, 5, 6, 7].map((n) => nextBackoffMs(n, 60_000));
    for (let i = 1; i < values.length; i++) {
      const prev = values[i - 1]!;
      const cur = values[i]!;
      expect(cur).toBeGreaterThanOrEqual(prev);
    }
  });

  it("respects custom maxMs without ever going above it", () => {
    for (let i = 1; i <= 10; i++) {
      const v = nextBackoffMs(i, 15_000);
      expect(v).toBeLessThanOrEqual(15_000);
    }
  });
});
