import L from "leaflet";
import { api } from "./api";
import { createHeatmapSurface, type HeatmapSample, type RgbColor } from "./heatmapSurface";
import { createGradientLegend, setLegendNote } from "./legend";
import { formatObservationTime, hasValidCoordinates } from "./stations";

// Past-1-hour rainfall color scale: blue -> cyan -> green -> yellow -> orange ->
// red -> purple at fixed mm stops. Checked with the dataviz palette validator
// (lightness band, chroma floor, adjacent normal-vision ΔE >= 15 pass; the CVD
// and contrast warnings are relieved by the legend's numeric ticks).
export const RAINFALL_STOPS: readonly { mm: number; color: string }[] = [
  { mm: 1, color: "#3463c9" },
  { mm: 5, color: "#1e9ec2" },
  { mm: 10, color: "#41a85a" },
  { mm: 20, color: "#c2b51c" },
  { mm: 40, color: "#ec6f1f" },
  { mm: 80, color: "#c62a2a" },
  { mm: 130, color: "#8b43b0" },
];
/** Below this amount a cell is left transparent (no rain shown). */
const MIN_RAIN_MM = RAINFALL_STOPS[0].mm;

function hexToRgb(hex: string): RgbColor {
  const n = Number.parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

const STOPS = RAINFALL_STOPS.map((s) => ({ mm: s.mm, rgb: hexToRgb(s.color) }));

/** Blend linearly between neighbouring stops; null (transparent) below 1 mm. */
export function rainfallRgb(mm: number): RgbColor | null {
  if (!(mm >= MIN_RAIN_MM)) return null;
  for (let i = 1; i < STOPS.length; i++) {
    const hi = STOPS[i];
    if (mm <= hi.mm) {
      const lo = STOPS[i - 1];
      const f = (mm - lo.mm) / (hi.mm - lo.mm);
      const mix = (c: 0 | 1 | 2): number => Math.round(lo.rgb[c] + (hi.rgb[c] - lo.rgb[c]) * f);
      return [mix(0), mix(1), mix(2)];
    }
  }
  return STOPS[STOPS.length - 1].rgb;
}

// Stops sit at equal spacing on the legend bar (the scale is not linear in mm),
// with half a step of flat color at each end so the end labels fit.
const legendPosition = (index: number): number => (index + 0.5) / STOPS.length;

function createLegend(): L.Control {
  return createGradientLegend({
    title: "雨量 mm",
    ariaLabel: `雨量圖例（過去 1 小時）：由藍（${STOPS[0].mm} mm）漸變到紫（${STOPS[STOPS.length - 1].mm} mm 以上），低於 ${MIN_RAIN_MM} mm 不著色`,
    stops: STOPS.map((s, i) => ({ position: legendPosition(i), rgb: s.rgb })),
    ticks: STOPS.map((s, i) => ({ position: legendPosition(i), label: String(s.mm) })),
    className: "rainfall-legend",
  });
}

export interface RainfallLayer {
  layer: L.LayerGroup;
}

/**
 * 雨量 main layer: IDW surface of real past-1-hour rainfall from
 * /api/rainfall/latest (gauges with rainfall !== null only) plus its legend.
 * Data is fetched the first time the layer is shown (and retried after an error).
 * `onStatus` receives the header detail text (observation time / gauge count).
 */
export function createRainfallLayer(map: L.Map, onStatus: (detail: string) => void): RainfallLayer {
  const layer = L.layerGroup();
  const surface = createHeatmapSurface(map, rainfallRgb, "rainfall-surface");
  layer.addLayer(surface.layer);
  const legend = createLegend();
  let state: "idle" | "loading" | "loaded" | "error" = "idle";
  let note = "雨量資料載入中…";

  const showNote = (text: string): void => {
    note = text;
    setLegendNote(legend, text);
  };

  const load = async (): Promise<void> => {
    state = "loading";
    showNote("雨量資料載入中…");
    onStatus("資料載入中…");
    try {
      const res = await api.rainfallLatest();
      const samples: HeatmapSample[] = [];
      for (const s of res.data) {
        if (s.rainfall === null || !Number.isFinite(s.rainfall) || s.rainfall < 0 || !hasValidCoordinates(s)) continue;
        samples.push({ lat: s.latitude, lng: s.longitude, value: s.rainfall });
      }
      surface.update(samples);
      state = "loaded";
      const raining = samples.filter((s) => s.value >= MIN_RAIN_MM).length;
      if (samples.length === 0) {
        showNote("暫無雨量資料");
      } else if (raining === 0) {
        showNote(`無明顯降雨（< ${MIN_RAIN_MM} mm 不著色）`);
      } else {
        showNote(`${raining} / ${samples.length} 站 ≥ ${MIN_RAIN_MM} mm（< ${MIN_RAIN_MM} 不著色）`);
      }
      const time = res.latest_observation_time ? formatObservationTime(res.latest_observation_time) : "尚無觀測資料";
      onStatus(samples.length > 0 ? `最新觀測：${time} | 雨量站 ${samples.length} 站` : "暫無雨量資料");
    } catch {
      surface.update([]);
      state = "error";
      showNote("目前無法取得雨量資料");
      onStatus("目前無法取得雨量資料");
    }
  };

  // The legend is shown only while the rainfall layer is on.
  layer.on("add", () => {
    legend.addTo(map);
    setLegendNote(legend, note);
    if (state === "idle" || state === "error") void load();
  });
  layer.on("remove", () => legend.remove());

  return { layer };
}
