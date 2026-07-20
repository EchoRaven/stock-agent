"use client";

import { useEffect, useRef } from "react";
import { ColorType, createChart, type IChartApi } from "lightweight-charts";

export function PriceChart({ data }: { data: { date: string; close: number | null }[] }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: 320,
      layout: {
        background: { type: ColorType.Solid, color: "#ffffff" },
        textColor: "#334155",
      },
      grid: {
        vertLines: { color: "#f1f5f9" },
        horzLines: { color: "#f1f5f9" },
      },
      rightPriceScale: { borderColor: "#e2e8f0" },
      timeScale: { borderColor: "#e2e8f0" },
    });
    chartRef.current = chart;

    const series = chart.addAreaSeries({
      lineColor: "#1d4ed8",
      topColor: "rgba(29, 78, 216, 0.25)",
      bottomColor: "rgba(29, 78, 216, 0.02)",
      lineWidth: 2,
    });

    series.setData(
      data
        .filter((d) => d.close !== null && d.close !== undefined)
        .map((d) => ({ time: d.date, value: d.close as number }))
    );
    chart.timeScale().fitContent();

    const handleResize = () => {
      chart.applyOptions({ width: container.clientWidth });
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
      chartRef.current = null;
    };
  }, [data]);

  return <div ref={containerRef} className="w-full" />;
}
