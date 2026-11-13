import type { EChartsCoreOption } from "echarts/core";
import { col, type Channel, type RateBin, type Readings } from "../api";
import { axisBase, C } from "./ui";

type Pair = [number, number | null];

/** Axis range that always includes the data, the setpoint and the process limits. */
function range(series: Pair[], extra: (number | null)[], minSpan: number, digits = 1): { min?: number; max?: number } {
  const vals = series.map((p) => p[1]).filter((v): v is number => v !== null).concat(extra.filter((v): v is number => v !== null));
  if (!vals.length) return {};
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (hi - lo < minSpan) { const c = (hi + lo) / 2; lo = c - minSpan / 2; hi = c + minSpan / 2; }
  const pad = (hi - lo) * 0.06, f = 10 ** digits;
  return { min: Math.floor((lo - pad) * f) / f, max: Math.ceil((hi + pad) * f) / f };
}

const lastVal = (s: Pair[]) => {
  for (let i = s.length - 1; i >= 0; i--) if (s[i][1] !== null) return s[i][1] as number;
  return null;
};

/** pH (left) + base rate bars (right): used on Home cards and the phone view. */
export function phAndRateOption(r: Readings | undefined, bins: RateBin[] | undefined, xMax?: number): EChartsCoreOption {
  const ph = col(r, "ph");
  const lo = lastVal(col(r, "ph_low_limit")), hi = lastVal(col(r, "ph_high_limit")), sp = lastVal(col(r, "ph_setpoint"));
  const bars = (bins ?? []).map((b) => [(b.t_start_h + b.t_end_h) / 2, b.rate_ml_min, b.complete]);
  const marks: object[] = [];
  if (sp !== null) marks.push({ yAxis: sp, lineStyle: { color: C.blue, type: "dashed", opacity: 0.6 }, label: { show: false } });
  if (lo !== null) marks.push({ yAxis: lo, lineStyle: { color: C.blue, type: "dotted", width: 1, opacity: 0.8 }, label: { show: false } });
  if (hi !== null) marks.push({ yAxis: hi, lineStyle: { color: C.blue, type: "dotted", width: 1, opacity: 0.8 }, label: { show: false } });
  return {
    animation: false,
    grid: { left: 44, right: 52, top: 12, bottom: 36 },
    tooltip: { trigger: "axis", valueFormatter: (v: number) => (v == null ? "—" : Number(v).toFixed(3)) },
    xAxis: { ...axisBase, type: "value", min: 0, max: xMax, name: "Time since start (h)", nameLocation: "middle", nameGap: 24,
      axisLine: { lineStyle: { color: "#74777f" } } },
    yAxis: [
      { ...axisBase, type: "value", name: "pH", nameLocation: "middle", nameGap: 32, ...range(ph, [lo, hi, sp], 0.4),
        axisLine: { show: true, lineStyle: { color: C.blue, width: 1.5 } }, axisLabel: { ...axisBase.axisLabel, color: C.blue, formatter: (v: number) => v.toFixed(1) } },
      { ...axisBase, type: "value", name: "mL/min", nameLocation: "middle", nameGap: 40, min: 0,
        axisLine: { show: true, lineStyle: { color: C.greenDark, width: 1.5 } }, axisLabel: { ...axisBase.axisLabel, color: C.greenDark } },
    ],
    series: [
      { type: "bar", yAxisIndex: 1, data: bars.map((b) => ({ value: [b[0], b[1]], itemStyle: { color: C.green, opacity: b[2] ? 0.45 : 0.2 } })),
        barWidth: "60%", name: "Base rate (15 min)", itemStyle: { color: C.green } },
      { type: "line", data: ph, showSymbol: false, lineStyle: { color: C.blue, width: 1.6 }, name: "pH",
        markLine: { symbol: "none", silent: true, data: marks } },
    ],
  };
}

const SOLID = "path://M0,3h30v2h-30z";
const DASHED = "path://M0,3h8v2h-8zM11,3h8v2h-8zM22,3h8v2h-8z";

/** Main fermentation chart: two selectable variables, setpoints, limits, optional partner overlay. */
export interface TrendVar { key: string; label: string; unit: string; setpoint?: string; limits?: [string, string]; digits: number; channel?: Channel }
export const TREND_VARS: TrendVar[] = [
  { key: "ph", label: "pH", unit: "", setpoint: "ph_setpoint", limits: ["ph_low_limit", "ph_high_limit"], digits: 2, channel: "ph" },
  { key: "temperature", label: "Temperature", unit: "°C", setpoint: "temperature_setpoint", limits: ["temperature_low_limit", "temperature_high_limit"], digits: 1, channel: "temperature" },
  { key: "stirrer", label: "Stirrer", unit: "rpm", setpoint: "stirrer_setpoint", digits: 0, channel: "stirrer" },
  { key: "do2", label: "dO2", unit: "%", setpoint: "do2_setpoint", limits: ["do2_low_limit", "do2_high_limit"], digits: 1, channel: "do2" },
  { key: "base_cumulative_ml", label: "Base, cumulative", unit: "mL", digits: 1, channel: "ph" },
  { key: "base_pump_pct", label: "Base pump activation", unit: "%", digits: 0, channel: "ph" },
  { key: "heating_pct", label: "Heating", unit: "%", digits: 0, channel: "temperature" },
  { key: "cooling_pct", label: "Cooling", unit: "%", digits: 0, channel: "temperature" },
  { key: "stirrer_out_rpm", label: "Stirrer motor output", unit: "rpm", digits: 0, channel: "stirrer" },
  { key: "n2_pct", label: "N2 valve", unit: "%", digits: 0, channel: "do2" },
  { key: "air_pct", label: "Air valve", unit: "%", digits: 0, channel: "do2" },
  { key: "acid_cumulative_ml", label: "Acid (Addition 2), cumulative", unit: "mL", digits: 1, channel: "ph" },
  { key: "acid_pump_pct", label: "Acid pump activation", unit: "%", digits: 0, channel: "ph" },
  { key: "level_contact", label: "Level / foam contact (1 = contact)", unit: "", digits: 0, channel: "level" },
  { key: "antifoam_cumulative_ml", label: "Antifoam, cumulative", unit: "mL", digits: 1, channel: "level" },
  { key: "antifoam_pct", label: "Antifoam pump activation", unit: "%", digits: 0, channel: "level" },
  { key: "redox_mv", label: "Redox", unit: "mV", digits: 0, channel: "redox" },
];

/** Variables of the channels in use (all when channels are unknown). */
export const varsFor = (channels?: Channel[]) =>
  TREND_VARS.filter((v) => !channels || !v.channel || channels.includes(v.channel));

/** Pseudo-variable for the right axis: base rate bars per bin (needs opts.bins). */
export const BASE_RATE_VAR: TrendVar = { key: "base_rate", label: "Base rate (15 min)", unit: "mL/min", digits: 3, channel: "ph" };

export function trendsOption(r: Readings | undefined, left: TrendVar, right: TrendVar | null,
  opts: { setpoints: boolean; limits: boolean; partner?: Readings; partnerLabel?: string;
    bins?: RateBin[]; xRange?: { min?: number; max?: number }; compact?: boolean }): EChartsCoreOption {
  const colors = [C.blue, opts.compact ? C.greenDark : C.orange];
  const series: object[] = [];
  const yAxes: object[] = [];
  [left, right].forEach((v, i) => {
    if (!v) return;
    const color = colors[i];
    const textColor = i === 0 ? C.blue : opts.compact ? C.greenDark : C.orangeText;
    if (v.key === "base_rate") {      // bars from the rate bins, on the right axis
      yAxes.push({ ...axisBase, type: "value", min: 0, name: `${v.label} (${v.unit})`, nameLocation: "middle", nameGap: 44,
        position: "right", axisLine: { show: true, lineStyle: { color: C.greenDark, width: 1.5 } },
        axisLabel: { ...axisBase.axisLabel, color: C.greenDark } });
      series.push({ type: "bar", name: v.label, yAxisIndex: i, barWidth: "60%", itemStyle: { color: C.green },
        data: (opts.bins ?? []).map((b) => ({ value: [(b.t_start_h + b.t_end_h) / 2, b.rate_ml_min],
          itemStyle: { color: C.green, opacity: b.complete ? 0.55 : 0.25 } })) });
      return;
    }
    const marks: object[] = [];
    const lo = v.limits ? lastVal(col(r, v.limits[0])) : null, hi = v.limits ? lastVal(col(r, v.limits[1])) : null;
    if (opts.limits && v.limits) {
      // limits in the variable's own colour, thin and dotted (setpoint: dashed; measured value: solid)
      const label = (t: string) => (opts.compact ? { show: false } : { formatter: t, color: textColor, fontSize: 10, position: "insideEndTop" });
      if (lo !== null) marks.push({ yAxis: lo, lineStyle: { color, type: "dotted", width: 1, opacity: 0.85 }, label: label(`${v.label} low ${lo}`) });
      if (hi !== null) marks.push({ yAxis: hi, lineStyle: { color, type: "dotted", width: 1, opacity: 0.85 }, label: label(`${v.label} high ${hi}`) });
    }
    yAxes.push({ ...axisBase, type: "value", name: `${v.label}${v.unit ? ` (${v.unit})` : ""}`, nameLocation: "middle",
      nameGap: 44, position: i ? "right" : "left",
      ...(v.key === "ph" || v.key === "temperature" || v.key === "do2"
        ? range(col(r, v.key).concat(i === 0 && opts.partner ? col(opts.partner, v.key) : []),
            opts.limits ? [lo, hi] : [], v.key === "ph" ? 0.4 : v.key === "do2" ? 10 : 4, v.key === "ph" ? 1 : 0)
        : { min: 0 }),
      axisLine: { show: true, lineStyle: { color: textColor, width: 1.5 } },
      axisLabel: { ...axisBase.axisLabel, color: textColor, formatter: (x: number) => x.toFixed(v.digits === 2 ? 2 : v.digits) } });
    series.push({ type: "line", name: v.label, yAxisIndex: i, data: col(r, v.key), showSymbol: false, symbol: "none",
      lineStyle: { color, width: i ? 2.2 : 1.8 }, itemStyle: { color }, markLine: { symbol: "none", silent: true, data: marks } });
    if (opts.setpoints && v.setpoint) {
      series.push({ type: "line", name: `${v.label} setpoint`, yAxisIndex: i, data: col(r, v.setpoint), showSymbol: false, symbol: "none",
        step: "end", lineStyle: { color, width: 1.4, type: "dashed", opacity: 0.7 }, itemStyle: { color } });
    }
    if (i === 0 && opts.partner) {
      series.push({ type: "line", name: opts.partnerLabel ?? "Paired", yAxisIndex: 0, data: col(opts.partner, v.key),
        showSymbol: false, symbol: "none", lineStyle: { color: C.grey, width: 1.5 }, itemStyle: { color: C.grey }, z: 1 });
    }
  });
  return {
    animation: false,
    grid: opts.compact ? { left: 56, right: right ? 60 : 16, top: 12, bottom: 40 } : { left: 64, right: right ? 64 : 24, top: 28, bottom: 64 },
    // legend icons: solid line = measured value, dashed line = setpoint
    legend: opts.compact ? { show: false } : { bottom: 0, textStyle: { fontSize: 12 }, itemWidth: 28, itemHeight: 8,
      data: (series as { name?: string; type?: string }[]).filter((x) => x.type === "line" && x.name).map((x) => ({
        name: x.name, icon: x.name!.endsWith("setpoint") ? DASHED : SOLID })) },
    tooltip: { trigger: "axis", confine: true,
      formatter: (ps: { axisValue: number; marker: string; seriesName: string; value: [number, number | null] }[]) => {
        const num = (x: number | null) => (x == null ? "—" : Number(x).toFixed(3).replace(/\.?0+$/, ""));
        const t = ps[0]?.axisValue;
        return [`t = ${t == null ? "—" : Number(t).toFixed(2)} h`,
          ...ps.map((q) => `${q.marker}${q.seriesName} <b>${num(Array.isArray(q.value) ? q.value[1] : (q.value as number | null))}</b>`)].join("<br/>");
      } },
    dataZoom: [{ type: "inside", filterMode: "none" }],
    xAxis: { ...axisBase, type: "value", min: opts.xRange?.min ?? 0, max: opts.xRange?.max, name: "Time since start (h)", nameLocation: "middle", nameGap: 26,
      axisLine: { lineStyle: { color: "#74777f" } }, splitLine: { show: true, lineStyle: { color: C.grid } } },
    yAxis: yAxes,
    series,
  };
}

/** Base dosing: rate bins (left) + cumulative staircase from the controller (right). */
export function baseOption(r: Readings | undefined, bins: RateBin[] | undefined, partner?: Readings): EChartsCoreOption {
  const series: object[] = [
    // series-level colour too: the legend takes it from here, not from the bars
    { type: "bar", name: "Rate (mL/min)", yAxisIndex: 0, barWidth: "70%", itemStyle: { color: C.green },
      data: (bins ?? []).map((b) => ({ value: [(b.t_start_h + b.t_end_h) / 2, b.rate_ml_min], itemStyle: { color: C.green, opacity: b.complete ? 0.85 : 0.35 } })) },
    { type: "line", name: "Cumulative (controller)", yAxisIndex: 1, data: col(r, "base_cumulative_ml"), step: "end",
      showSymbol: false, lineStyle: { color: C.ink, width: 2 }, itemStyle: { color: C.ink } },
  ];
  if (partner) series.push({ type: "line", name: "Cumulative R2 (paired)", yAxisIndex: 1, step: "end",
    data: col(partner, "base_cumulative_ml"), showSymbol: false, lineStyle: { color: C.grey, width: 1.5 }, itemStyle: { color: C.grey } });
  return {
    animation: false,
    grid: { left: 64, right: 64, top: 20, bottom: 64 },
    legend: { bottom: 0, textStyle: { fontSize: 12 } },
    tooltip: { trigger: "axis" },
    xAxis: { ...axisBase, type: "value", min: 0, name: "Time since start (h)", nameLocation: "middle", nameGap: 26, axisLine: { lineStyle: { color: "#74777f" } } },
    yAxis: [
      { ...axisBase, type: "value", min: 0, name: "Rate (mL/min)", nameLocation: "middle", nameGap: 46,
        axisLine: { show: true, lineStyle: { color: C.greenDark, width: 1.5 } }, axisLabel: { ...axisBase.axisLabel, color: C.greenDark } },
      { ...axisBase, type: "value", min: 0, name: "Cumulative (mL)", nameLocation: "middle", nameGap: 40,
        axisLine: { show: true, lineStyle: { color: C.ink, width: 1.5 } }, axisLabel: { ...axisBase.axisLabel, color: C.ink } },
    ],
    series,
  };
}
