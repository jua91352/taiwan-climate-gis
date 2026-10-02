import L from "leaflet";
import { api, type StationObservation } from "./api";
import { CITY_CENTERS } from "./gis";
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
  const legend = new L.Control({ position: "bottomright" });
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

/** Pill showing a county's average, colored by the same bins as the station circles. */
function countyBadge(county: string, avg: number): L.DivIcon {
  const pill = document.createElement("div");
  pill.className = binIndex(avg) >= 3 ? "county-temp county-temp-dark" : "county-temp";
  pill.style.background = temperatureColor(avg);
  pill.textContent = `${avg.toFixed(1)}°`;
  pill.title = `${county} 平均氣溫 ${avg}°C`;
  return L.divIcon({ className: "county-temp-icon", html: pill, iconSize: undefined });
}

export interface TemperatureLayer {
  layer: L.LayerGroup;
  /** Replace the station-level circles; returns how many stations have a valid temperature. */
  render(stations: StationObservation[]): number;
}

export function createTemperatureLayer(map: L.Map): TemperatureLayer {
  // Above the station dots (450) so the temperature color is fully visible,
  // below tooltips/popups. Everything here is non-interactive, so clicks pass
  // through: a circle center hits the station dot (popup), anything else hits
  // the county polygon underneath.
  if (!map.getPane(TEMPERATURE_PANE)) {
    map.createPane(TEMPERATURE_PANE).style.zIndex = "460";
  }
  const layer = L.layerGroup();
  const stationGroup = L.layerGroup();
  const countyGroup = L.layerGroup();
  const legend = createLegend();
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
    const radius = radiusForZoom(map.getZoom());
    stationGroup.eachLayer((circle) => {
      if (circle instanceof L.CircleMarker) circle.setRadius(radius);
    });
    if (map.hasLayer(layer)) applyMode();
  });

  const render = (stations: StationObservation[]): number => {
    stationGroup.clearLayers();
    const radius = radiusForZoom(map.getZoom());
    drawn = 0;
    total = stations.length;
    for (const s of stations) {
      if (!hasValidCoordinates(s) || !hasValidTemperature(s)) continue;
      // Non-interactive: clicks reach the station dot / county polygon beneath;
      // full details are in that station's popup.
      L.circleMarker([s.latitude, s.longitude], {
        pane: TEMPERATURE_PANE,
        interactive: false,
        radius,
        color: "#ffffff",
        weight: 1,
        fillColor: temperatureColor(s.temperature),
        fillOpacity: 0.85,
      })
        .bindTooltip(`${s.temperature.toFixed(1)}°`, {
          permanent: true,
          direction: "right",
          offset: [8, 0],
          className: "station-temp-label",
        })
        .addTo(stationGroup);
      drawn++;
    }
    updateLegendNote();
    return drawn;
  };

  return { layer, render };
}
