import { describe, expect, it } from "vitest";
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import "./PriceChart";

echarts.use([SVGRenderer]);

describe("PriceChart ECharts registrations", () => {
  it("registers scatter support required by all technical event overlays", () => {
    const chart = echarts.init(null, undefined, {
      renderer: "svg",
      ssr: true,
      width: 800,
      height: 500,
    });
    try {
      chart.setOption({
        xAxis: { type: "category", data: ["2026-10-19"] },
        yAxis: { type: "value" },
        series: [{ name: "结构事件", type: "scatter", data: [["2026-10-19", 1]] }],
      });
      expect(chart.renderToSVGString()).toContain("<svg");
    } finally {
      chart.dispose();
    }
  });
});
