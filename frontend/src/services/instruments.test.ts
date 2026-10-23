import { describe, expect, it } from "vitest";
import { instrumentQuery, specificationValue } from "./instruments";

describe("historical product rule queries", () => {
  it("keeps product codes and explicit UTC cutoffs without browser-local conversion", () => {
    const query = new URLSearchParams(instrumentQuery("xtks:0012", "2026-10-04T15:00", "2026-10-22T09:12:03"));
    expect(query.get("asset_id")).toBe("xtks:0012");
    expect(query.get("effective_at")).toBe("2026-10-04T15:00Z");
    expect(query.get("known_at")).toBe("2026-10-22T09:12:03Z");
    expect(new URLSearchParams(instrumentQuery("us:NVDA", "", "")).has("known_at")).toBe(false);
    expect(() => instrumentQuery("xtks:1321", "invalid", "")).toThrow();
  });
  it("does not turn an unknown unit into one or discard an exact zero", () => {
    expect(specificationValue(null)).toBe("未核验");
    expect(specificationValue("UNKNOWN")).toBe("未核验");
    expect(specificationValue("0.000000000000000001")).toBe("0.000000000000000001");
    expect(specificationValue("0")).toBe("0");
  });
});
