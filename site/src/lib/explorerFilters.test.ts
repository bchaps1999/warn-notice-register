import { describe, expect, it } from "vitest";
import { applyFilters, buildHaystack, DEFAULT_FILTERS } from "./explorerFilters";
import { FLAG_UNDATED, type NoticeIndex } from "./types";

const index: NoticeIndex = {
  states: ["CA"], types: ["unknown"], sectors: [], counties: [], count: 2,
  columns: {
    key: ["dated", "undated"], state: [0, 0],
    // The second timeline date falls back to its action date.
    date: ["2026-01-01", "2026-03-01"],
    effective: ["2026-04-01", "2026-03-01"], effective_end: [null, null],
    employer: ["First", "Second"], location: [null, null], jobs: [10, 20],
    type: [0, 0], flags: [0, FLAG_UNDATED], sector: [-1, -1], county: [-1, -1],
  },
};

describe("explorer date filters", () => {
  it("does not treat an action-date fallback as a notice date", () => {
    const haystack = buildHaystack(index);
    expect(applyFilters(index, haystack, {
      ...DEFAULT_FILTERS, from: "2026-02-01", to: "2026-03-31",
    })).toEqual([]);
    expect(applyFilters(index, haystack, {
      ...DEFAULT_FILTERS, dateBasis: "layoff", from: "2026-02-01", to: "2026-03-31",
    })).toEqual([1]);
  });
});
