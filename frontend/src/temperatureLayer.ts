import L from "leaflet";
import { api, type StationObservation } from "./api";
import { CITY_CENTERS } from "./gis";
import { hasValidCoordinates } from "./stations";
import { createTemperatureHeatmap, type RgbColor, type TemperatureSample } from "./temperatureHeatmap";

const TEMPERATURE_PANE = "temperature";

// Cool -> warm ramp (blue, cyan, green, yellow, orange, red) in fixed 5 °C bins,
// shared by the legend, county badges and the heatmap.
// Checked with the dataviz palette validator: lightness band, chroma floor and
// adjacent normal-vision ΔE >= 15 pass; the CVD/contrast warnings are relieved
// by the numeric labels. Fixed bins keep colors comparable across times.
export const TEMPERATURE_BINS: readonly { min: number; color: string; label: string }[] = [
  { min: Number.NEGATIVE_INFINITY, color: "#3463c9", label: "< 10" },
  { min: 10, color: "#1e9ec2", label: "10 – 15" },
  { min: 15, color: "#41a85a", label: "15 – 20" },
  { min: 20, color: "#c2b51c", label: "20 – 25" },
  { min: 25, color: "#ec6f1f", label: "25 – 30" },
  { min: 30, color: "#b8232b", label: "≥ 30" },
];

// Station-level display from this zoom up; county averages below it. It is the
// smallest CITY_CENTERS zoom, so every county click lands in station mode while
// the whole-Taiwan view (zoom 7-8) shows one value per county.
export const STATION_MODE_MIN_ZOOM = Math.min(...Object.values(CITY_CENTERS).map((c) => c.zoom));

function binIndex(t: number): number {
  let index = 0;
  TEMPERATURE_BINS.forEach((bin, i) => {
    if (t >= bin.min) index = i;
  });
  return index;
}

export function temperatureColor(t: number): string {
  return TEMPERATURE_BINS[binIndex(t)].color;
}

function hexToRgb(hex: string): RgbColor {
  const n = Number.parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

// Continuous version for the heatmap: each bin color sits at its bin's middle
// (7.5, 12.5, … 32.5 °C) and colors blend linearly in between, so the surface
// is smooth yet matches the legend swatch at every bin center.
const RAMP_STOPS = TEMPERATURE_BINS.map((bin, i) => ({ t: 7.5 + i * 5, rgb: hexToRgb(bin.color) }));

export function temperatureRgb(t: number): RgbColor {
  if (t <= RAMP_STOPS[0].t) return RAMP_STOPS[0].rgb;
  for (let i = 1; i < RAMP_STOPS.length; i++) {
    const hi = RAMP_STOPS[i];
    if (t <= hi.t) {
      const lo = RAMP_STOPS[i - 1];
      const f = (t - lo.t) / (hi.t - lo.t);
      const mix = (c: 0 | 1 | 2): number => Math.round(lo.rgb[c] + (hi.rgb[c] - lo.rgb[c]) * f);
      return [mix(0), mix(1), mix(2)];
    }
  }
  return RAMP_STOPS[RAMP_STOPS.length - 1].rgb;
}

/** White text on the dark ends of the ramp (blue, red), dark text elsewhere. */
function needsLightText(hex: string): boolean {
  const [r, g, b] = hexToRgb(hex).map((c) => {
    const v = c / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.18;
}

/** A real measurement: finite and not a CWA sentinel (-99, -990, ...). */
function hasValidTemperature(s: StationObservation): s is StationObservation & { temperature: number } {
  return typeof s.temperature === "number" && Number.isFinite(s.temperature) && s.temperature > -90;
}

// Legend domain: half a bin beyond the first/last ramp stop. Outside the stops
// the heatmap color is clamped, so the bar's ends are flat like the map.
const LEGEND_MIN = RAMP_STOPS[0].t - 2.5;
const LEGEND_MAX = RAMP_STOPS[RAMP_STOPS.length - 1].t + 2.5;
const legendPosition = (t: number): string => `${(((t - LEGEND_MIN) / (LEGEND_MAX - LEGEND_MIN)) * 100).toFixed(2)}%`;

/** Compact horizontal gradient built from the same stops temperatureRgb() blends. */
function createLegend(): L.Control {
  const legend = new L.Control({ position: "bottomright" });
  legend.onAdd = () => {
    const box = L.DomUtil.create("div", "temperature-legend");
    const ticks = TEMPERATURE_BINS.slice(1).map((bin) => bin.min);
    box.setAttribute("role", "img");
    box.setAttribute("aria-label", `氣溫圖例：由藍（低於 ${ticks[0]} °C）漸變到紅（${ticks[ticks.length - 1]} °C 以上）`);
    L.DomUtil.create("div", "temperature-legend-title", box).textContent = "氣溫 °C";
    const bar = L.DomUtil.create("div", "temperature-legend-bar", box);
    bar.style.background = `linear-gradient(to right, ${RAMP_STOPS.map(
      (stop) => `rgb(${stop.rgb.join(", ")}) ${legendPosition(stop.t)}`,
    ).join(", ")})`;
    const scale = L.DomUtil.create("div", "temperature-legend-ticks", box);
    for (const t of ticks) {
      const tick = L.DomUtil.create("span", undefined, scale);
      tick.style.left = legendPosition(t);
      tick.textContent = String(t);
    }
    L.DomUtil.create("div", "temperature-legend-note", box).dataset.role = "count";
    L.DomEvent.disableClickPropagation(box);
    return box;
  };
  return legend;
}

/** Pill showing a county's average, colored by its temperature bin. */
function countyBadge(county: string, avg: number): L.DivIcon {
  const pill = document.createElement("div");
  pill.className = needsLightText(temperatureColor(avg)) ? "county-temp county-temp-dark" : "county-temp";
  pill.style.background = temperatureColor(avg);
  pill.textContent = `${avg.toFixed(1)}°`;
  pill.title = `${county} 平均氣溫 ${avg}°C`;
  return L.divIcon({ className: "county-temp-icon", html: pill, iconSize: undefined });
}

export interface TemperatureLayer {
  layer: L.LayerGroup;
  /** Replace the station-level labels; returns how many stations have a valid temperature. */
  render(stations: StationObservation[]): number;
}

export function createTemperatureLayer(map: L.Map): TemperatureLayer {
  // Above the station dots (450), below tooltips/popups. Everything here is
  // non-interactive, so clicks pass through to the station dot (popup) or the
  // county polygon underneath.
  if (!map.getPane(TEMPERATURE_PANE)) {
    map.createPane(TEMPERATURE_PANE).style.zIndex = "460";
  }
  const layer = L.layerGroup();
  const stationGroup = L.layerGroup();
  const countyGroup = L.layerGroup();
  const legend = createLegend();
  // Continuous surface under the circles/badges; part of the 氣溫 layer itself.
  const heatmap = createTemperatureHeatmap(map, temperatureRgb);
  layer.addLayer(heatmap.layer);
  let drawn = 0;
  let total = 0;
  let countyState: "idle" | "loading" | "loaded" | "error" = "idle";
  let countyCount = 0;

  const isStationMode = (): boolean => map.getZoom() >= STATION_MODE_MIN_ZOOM;

  const updateLegendNote = (): void => {
    const note = legend.getContainer()?.querySelector<HTMLElement>("[data-role=count]");
    if (!note) return;
    if (isStationMode()) {
      note.textContent = drawn > 0 ? `${drawn} / ${total} 站有有效氣溫` : "目前沒有可用的氣溫資料";
    } else if (countyState === "loading" || countyState === "idle") {
      note.textContent = "縣市平均氣溫載入中…";
    } else {
      note.textContent = countyCount > 0 ? `縣市平均氣溫（${countyCount} 縣市）` : "目前沒有可用的氣溫資料";
    }
  };

  // Show exactly one of the two groups, depending on zoom.
  const applyMode = (): void => {
    const station = isStationMode();
    if (station !== layer.hasLayer(stationGroup)) {
      if (station) layer.addLayer(stationGroup);
      else layer.removeLayer(stationGroup);
    }
    if (!station !== layer.hasLayer(countyGroup)) {
      if (!station) layer.addLayer(countyGroup);
      else layer.removeLayer(countyGroup);
    }
    updateLegendNote();
  };

  // County averages come from the existing /api/weather/county/<name>, so each
  // badge equals the 平均氣溫 shown in that county's weather panel.
  const loadCountyAverages = async (): Promise<void> => {
    if (countyState === "loading" || countyState === "loaded") return;
    countyState = "loading";
    updateLegendNote();
    const names = Object.keys(CITY_CENTERS);
    const results = await Promise.allSettled(names.map((n) => api.countyWeather(n)));
    countyGroup.clearLayers();
    countyCount = 0;
    results.forEach((result, i) => {
      if (result.status !== "fulfilled") return;
      const avg = result.value.summary.avg_temperature;
      const center = CITY_CENTERS[names[i]];
      if (avg === null || !Number.isFinite(avg) || !center) return;
      L.marker([center.lat, center.lng], {
        icon: countyBadge(names[i], avg),
        pane: TEMPERATURE_PANE,
        interactive: false,
        keyboard: false,
      }).addTo(countyGroup);
      countyCount++;
    });
    countyState = results.some((r) => r.status === "fulfilled") ? "loaded" : "error";
    updateLegendNote();
  };

  // The legend is shown only while the temperature layer is on.
  layer.on("add", () => {
    legend.addTo(map);
    applyMode();
    void loadCountyAverages();
  });
  layer.on("remove", () => legend.remove());

  map.on("zoomend", () => {
    if (map.hasLayer(layer)) applyMode();
  });

  const render = (stations: StationObservation[]): number => {
    stationGroup.clearLayers();
    drawn = 0;
    total = stations.length;
    const samples: TemperatureSample[] = [];
    for (const s of stations) {
      if (!hasValidCoordinates(s) || !hasValidTemperature(s)) continue;
      samples.push({ lat: s.latitude, lng: s.longitude, temperature: s.temperature });
      // Just the number, centered on the station; the heatmap carries the color.
      // Non-interactive: clicks reach the station dot / county polygon beneath.
      const label = document.createElement("span");
      label.className = "station-temp-label";
      label.textContent = `${s.temperature.toFixed(1)}°`;
      L.marker([s.latitude, s.longitude], {
        icon: L.divIcon({ className: "station-temp-icon", html: label, iconSize: undefined }),
        pane: TEMPERATURE_PANE,
        interactive: false,
        keyboard: false,
      }).addTo(stationGroup);
      drawn++;
    }
    heatmap.update(samples);
    updateLegendNote();
    return drawn;
  };

  return { layer, render };
}
