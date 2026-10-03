import L from "leaflet";
import type { StationObservation } from "./api";
import { createHeatmapSurface, type HeatmapSample, type RgbColor } from "./heatmapSurface";
import { createGradientLegend, setLegendNote } from "./legend";
import { hasValidCoordinates } from "./stations";

// Relative humidity (%) color scale, dry -> humid: sand, olive, green, teal,
// blue, indigo at 50/60/…/100 %, blended linearly. The fixed 50–100 % range
// (not the day's min/max, so maps stay comparable over time) spreads Taiwan's
// usual 65–100 % readings across the ramp. It only maps colors: the IDW runs on
// the real values, and anything below 50 % (still valid data) is drawn in the
// 50 % color. Built in OKLCH with lightness
// stepping down evenly (0.76 -> 0.41), so it also reads by lightness alone;
// checked with the dataviz validator (--ordinal: monotone lightness, adjacent
// ΔL >= 0.06, light-end contrast >= 2:1). Distinct from the blue -> red
// temperature/rainfall ramps so "high humidity" does not read as "hot".
export const HUMIDITY_STOPS: readonly { pct: number; color: string }[] = [
  { pct: 50, color: "#dda552" },
  { pct: 60, color: "#99a445" },
  { pct: 70, color: "#4a9a5e" },
  { pct: 80, color: "#008585" },
  { pct: 90, color: "#00649a" },
  { pct: 100, color: "#244395" },
];

function hexToRgb(hex: string): RgbColor {
  const n = Number.parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

const STOPS = HUMIDITY_STOPS.map((s) => ({ pct: s.pct, rgb: hexToRgb(s.color) }));

/** Color for an (interpolated) humidity; clamped to the 50–100 % ramp. */
export function humidityRgb(pct: number): RgbColor {
  if (pct <= STOPS[0].pct) return STOPS[0].rgb;
  for (let i = 1; i < STOPS.length; i++) {
    const hi = STOPS[i];
    if (pct <= hi.pct) {
      const lo = STOPS[i - 1];
      const f = (pct - lo.pct) / (hi.pct - lo.pct);
      const mix = (c: 0 | 1 | 2): number => Math.round(lo.rgb[c] + (hi.rgb[c] - lo.rgb[c]) * f);
      return [mix(0), mix(1), mix(2)];
    }
  }
  return STOPS[STOPS.length - 1].rgb;
}

/** A real relative humidity reading: finite and within 0–100 %. */
function isValidHumidity(h: number | null): h is number {
  return h !== null && Number.isFinite(h) && h >= 0 && h <= 100;
}

// Linear 50–100 % scale, padded 3 % at each end so the "≤50" and "100" labels fit.
const legendPosition = (pct: number): number => (pct - 47) / 56;

function createLegend(): L.Control {
  return createGradientLegend({
    title: "相對濕度 %",
    ariaLabel: "相對濕度圖例：由沙色（50 % 以下）漸變到深藍（100 %）",
    stops: STOPS.map((s) => ({ position: legendPosition(s.pct), rgb: s.rgb })),
    // The first color also stands for everything below 50 %.
    ticks: STOPS.map((s, i) => ({ position: legendPosition(s.pct), label: i === 0 ? `≤${s.pct}` : String(s.pct) })),
    className: "humidity-legend",
  });
}

export interface HumidityLayer {
  layer: L.LayerGroup;
  /** Rebuild the surface from the existing /api/weather/latest data (no requests). */
  render(stations: StationObservation[]): number;
}

/**
 * 濕度 main layer: IDW surface of the real CWA O-A0003-001 RelativeHumidity
 * (stations with a valid 0–100 % value and position only) plus its legend.
 * Computed once per data update; Leaflet scales the image on pan/zoom.
 */
export function createHumidityLayer(map: L.Map): HumidityLayer {
  const layer = L.layerGroup();
  const surface = createHeatmapSurface(map, humidityRgb, "humidity-surface");
  layer.addLayer(surface.layer);
  const legend = createLegend();
  let note = "濕度資料載入中…";

  layer.on("add", () => {
    legend.addTo(map);
    setLegendNote(legend, note);
  });
  layer.on("remove", () => legend.remove());

  const render = (stations: StationObservation[]): number => {
    const samples: HeatmapSample[] = [];
    for (const s of stations) {
      if (!hasValidCoordinates(s) || !isValidHumidity(s.humidity)) continue;
      samples.push({ lat: s.latitude, lng: s.longitude, value: s.humidity });
    }
    surface.update(samples); // empty -> transparent (heatmapSurface guard)
    note = samples.length > 0 ? `${samples.length} / ${stations.length} 站有有效濕度` : "暫無濕度資料";
    setLegendNote(legend, note);
    return samples.length;
  };

  return { layer, render };
}
