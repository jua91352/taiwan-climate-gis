import L from "leaflet";
import type { StationObservation } from "./api";
import { hasValidCoordinates } from "./stations";

const TEMPERATURE_PANE = "temperature";

// One-hue (orange) ordinal ramp, light -> dark = cool -> hot, in fixed 5 °C bins.
// Validated with the dataviz palette checker (--ordinal) against light #fcfcfb
// and dark #1a1a19 surfaces: monotone lightness, adjacent ΔL >= 0.06, end-step
// contrast >= 2:1. Fixed bins keep colors comparable across observation times.
export const TEMPERATURE_BINS: readonly { min: number; color: string; label: string }[] = [
  { min: Number.NEGATIVE_INFINITY, color: "#efa077", label: "< 10" },
  { min: 10, color: "#e28755", label: "10 – 15" },
  { min: 15, color: "#d56d2f", label: "15 – 20" },
  { min: 20, color: "#c25500", label: "20 – 25" },
  { min: 25, color: "#a94500", label: "25 – 30" },
  { min: 30, color: "#8e3600", label: "≥ 30" },
];

export function temperatureColor(t: number): string {
  let color = TEMPERATURE_BINS[0].color;
  for (const bin of TEMPERATURE_BINS) if (t >= bin.min) color = bin.color;
  return color;
}

/** A real measurement: finite and not a CWA sentinel (-99, -990, ...). */
function hasValidTemperature(s: StationObservation): s is StationObservation & { temperature: number } {
  return typeof s.temperature === "number" && Number.isFinite(s.temperature) && s.temperature > -90;
}

function radiusForZoom(zoom: number): number {
  if (zoom <= 7) return 5;
  if (zoom <= 9) return 7;
  return 9;
}

function createLegend(): L.Control {
  const legend = new L.Control({ position: "bottomleft" });
  legend.onAdd = () => {
    const box = L.DomUtil.create("div", "temperature-legend");
    box.setAttribute("role", "img");
    box.setAttribute("aria-label", `氣溫圖例：${TEMPERATURE_BINS.map((b) => b.label).join("、")} °C`);
    const title = L.DomUtil.create("div", "temperature-legend-title", box);
    title.textContent = "氣溫 (°C)";
    for (const bin of [...TEMPERATURE_BINS].reverse()) {
      const row = L.DomUtil.create("div", "temperature-legend-row", box);
      const swatch = L.DomUtil.create("span", "temperature-legend-swatch", row);
      swatch.style.background = bin.color;
      L.DomUtil.create("span", undefined, row).textContent = bin.label;
    }
    L.DomUtil.create("div", "temperature-legend-note", box).dataset.role = "count";
    L.DomEvent.disableClickPropagation(box);
    return box;
  };
  return legend;
}

export interface TemperatureLayer {
  layer: L.LayerGroup;
  /** Replace circles with the given stations' temperatures; returns how many were drawn. */
  render(stations: StationObservation[]): number;
}

export function createTemperatureLayer(map: L.Map): TemperatureLayer {
  // Above the station dots (450) so the temperature color is fully visible,
  // below tooltips/popups. The circles are non-interactive, so clicks pass
  // through: the circle center hits the station dot (popup), the ring hits
  // the county polygon underneath.
  if (!map.getPane(TEMPERATURE_PANE)) {
    map.createPane(TEMPERATURE_PANE).style.zIndex = "460";
  }
  const layer = L.layerGroup();
  const legend = createLegend();
  let drawn = 0;
  let total = 0;

  const updateLegendCount = (): void => {
    const note = legend.getContainer()?.querySelector<HTMLElement>("[data-role=count]");
    if (note) note.textContent = drawn > 0 ? `${drawn} / ${total} 站有有效氣溫` : "目前沒有可用的氣溫資料";
  };

  // On narrow maps the attribution (bottom-right) can wrap and run under the
  // legend (bottom-left). Raise the legend just enough to clear it, only when
  // they actually collide; otherwise keep Leaflet's default position.
  const clearAttribution = (): void => {
    const box = legend.getContainer();
    const attribution = map.getContainer().querySelector<HTMLElement>(".leaflet-control-attribution");
    if (!box?.isConnected || !attribution) return;
    box.style.marginBottom = "";
    const l = box.getBoundingClientRect();
    const a = attribution.getBoundingClientRect();
    if (l.right > a.left && l.bottom > a.top) {
      const base = parseFloat(getComputedStyle(box).marginBottom) || 0;
      box.style.marginBottom = `${base + (l.bottom - a.top) + 4}px`;
    }
  };
  const scheduleClear = (): void => {
    requestAnimationFrame(clearAttribution);
  };

  // The legend is shown only while the temperature layer is on.
  layer.on("add", () => {
    legend.addTo(map);
    updateLegendCount();
    scheduleClear();
  });
  layer.on("remove", () => legend.remove());
  map.on("baselayerchange resize", scheduleClear);

  map.on("zoomend", () => {
    const radius = radiusForZoom(map.getZoom());
    layer.eachLayer((circle) => {
      if (circle instanceof L.CircleMarker) circle.setRadius(radius);
    });
  });

  const render = (stations: StationObservation[]): number => {
    layer.clearLayers();
    const radius = radiusForZoom(map.getZoom());
    drawn = 0;
    total = stations.length;
    for (const s of stations) {
      if (!hasValidCoordinates(s) || !hasValidTemperature(s)) continue;
      // Non-interactive: clicks reach the station dot / county polygon beneath;
      // exact values are in that station's popup.
      L.circleMarker([s.latitude, s.longitude], {
        pane: TEMPERATURE_PANE,
        interactive: false,
        radius,
        color: "#ffffff",
        weight: 1,
        fillColor: temperatureColor(s.temperature),
        fillOpacity: 0.85,
      }).addTo(layer);
      drawn++;
    }
    updateLegendCount();
    return drawn;
  };

  return { layer, render };
}
