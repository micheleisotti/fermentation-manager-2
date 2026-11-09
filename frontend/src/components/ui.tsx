import { useEffect, useRef, type ReactNode } from "react";
import * as echarts from "echarts/core";
import { LineChart, BarChart } from "echarts/charts";
import { GridComponent, MarkLineComponent, MarkAreaComponent, TooltipComponent, DataZoomComponent, LegendComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { Status } from "../api";

echarts.use([LineChart, BarChart, GridComponent, MarkLineComponent, MarkAreaComponent, TooltipComponent,
  DataZoomComponent, LegendComponent, CanvasRenderer]);

const PATHS: Record<string, string> = {
  home: "M3 10.5 12 3l9 7.5M5 9.5V21h14V9.5",
  flask: "M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4a2 2 0 0 0 1.8-3l-5-9V3",
  chart: "M3 3v18h18M7 15l4-5 3 3 5-7",
  list: "M4 6h16M4 12h16M4 18h10",
  gear: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM12 2v3M12 19v3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1 7 17M17 7l2.1-2.1",
  plus: "M12 5v14M5 12h14",
  back: "M19 12H5M12 19l-7-7 7-7",
  bell: "M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.9 1.9 0 0 0 3.4 0",
  warn: "M12 3 2 21h20L12 3zM12 10v5M12 18v.5",
  check: "m5 12 5 5L20 7",
  stop: "M7 7h10v10H7z",
  play: "M8 5v14l11-7z",
  expand: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",
  drop: "M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z",
  sliders: "M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M14 4v4M8 10v4M16 16v4",
  chevron: "m6 9 6 6 6-6",
  close: "M6 6l12 12M18 6 6 18",
};

export function Icon({ name, size = 22, color = "currentColor", width = 1.8 }: { name: string; size?: number; color?: string; width?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={width}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={PATHS[name]} />
    </svg>
  );
}

export function DataValue({ value, unit, label, color }: { value: ReactNode; unit?: string; label: ReactNode; color?: string }) {
  return (
    <div className="dv">
      <span className="v" style={{ color: color && THEMED[color] ? THEMED[color] : color }}>{value}{unit && <span className="u">{unit}</span>}</span>
      <span className="l">{label}</span>
    </div>
  );
}

const STATUS_LABEL: Record<Status, string> = { planned: "Planned", fermenting: "Fermenting", completed: "Completed", aborted: "Aborted" };
export function StatusChip({ status }: { status: Status }) {
  return (
    <span className={`status ${status}`}>
      {status === "fermenting" && <span className="dot" style={{ background: "#fff", width: 6, height: 6 }} />}
      {STATUS_LABEL[status]}
    </span>
  );
}

export function Segmented<T extends string>({ options, value, onChange, label }:
  { options: { value: T; label: string }[]; value: T; onChange: (v: T) => void; label: string }) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} className={o.value === value ? "on" : ""} aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** ECharts wrapper: keeps one instance per element, resizes with the container. */
export function EChart({ option, className = "chart", ariaLabel }: { option: echarts.EChartsCoreOption; className?: string; ariaLabel: string }) {
  const el = useRef<HTMLDivElement>(null);
  const inst = useRef<echarts.ECharts | null>(null);
  useEffect(() => {
    if (!el.current) return;
    inst.current = echarts.init(el.current, undefined, { renderer: "canvas" });
    const ro = new ResizeObserver(() => inst.current?.resize());
    ro.observe(el.current);
    return () => { ro.disconnect(); inst.current?.dispose(); inst.current = null; };
  }, []);
  useEffect(() => { inst.current?.setOption(option, { replaceMerge: ["series", "yAxis", "xAxis", "grid"], lazyUpdate: true }); }, [option]);
  return <div ref={el} className={`${className} echart`} role="img" aria-label={ariaLabel} />;
}

/** Palette colours that must follow the theme when used as text (dark theme: lighter variants). */
const THEMED: Record<string, string> = {
  "#22294d": "var(--ink)", "#1d3786": "var(--data-blue-text, #1d3786)", "#1f7a63": "var(--data-green-text, #1f7a63)",
  "#8a5a00": "var(--data-orange-text)", "#455e6f": "var(--data-slate-text, #455e6f)",
};

export const C = {
  blue: "#1d3786", orange: "#f9b112", orangeText: "#8a5a00", green: "#38b292", greenDark: "#1f7a63",
  slate: "#455e6f", grey: "#9a9ca0", alert: "#ea0029", ink: "#22294d", soft: "#54565a", grid: "#eef0f4",
};

export const axisBase = {
  axisLine: { show: true, lineStyle: { width: 1.5 } },
  axisTick: { show: true },
  axisLabel: { fontFamily: "IBM Plex Mono", fontSize: 11 },
  nameTextStyle: { fontFamily: "IBM Plex Sans", fontSize: 12 },
  splitLine: { show: false },
};
