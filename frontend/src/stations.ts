import L from "leaflet";
import type { StationObservation } from "./api";

const STATION_PANE = "stations";
const MISSING = "資料不足";

// Keep auto-panned popups clear of the floating header card (top-left) and the
// expanded layer control (top-right, ~100 px wide), which are drawn above popups.
const POPUP_OPTIONS: L.PopupOptions = {
  autoPanPaddingTopLeft: L.point(10, 140),
  autoPanPaddingBottomRight: L.point(120, 10),
};

// Small markers above the county polygons (overlayPane z-index 400) so that a
// selected county's bringToFront() never covers them, but below tooltips/popups.
const STATION_STYLE: L.CircleMarkerOptions = {
  pane: STATION_PANE,
  radius: 5,
  color: "#ffffff",
  weight: 1,
  fillColor: "#0f766e",
  fillOpacity: 0.85,
};

export function formatObservationTime(value: string | null): string {
  if (!value) return MISSING;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? MISSING : date.toLocaleString("zh-TW", { hour12: false });
}

export function formatValue(value: number | null, unit: string, separator = " "): string {
  return value === null || !Number.isFinite(value) ? MISSING : `${value}${separator}${unit}`;
}

export function hasValidCoordinates<T extends { latitude: number | null; longitude: number | null }>(
  s: T,
): s is T & { latitude: number; longitude: number } {
  return (
    s.latitude !== null && s.longitude !== null &&
    Number.isFinite(s.latitude) && Number.isFinite(s.longitude) &&
    Math.abs(s.latitude) <= 90 && Math.abs(s.longitude) <= 180
  );
}

function createPopupContent(s: StationObservation): HTMLElement {
  const rows: [string, string][] = [
    ["測站名稱", s.station_name],
    ["測站編號", s.station_id],
    ["縣市", s.county_name ?? MISSING],
    ["鄉鎮", s.town_name ?? MISSING],
    ["觀測時間", formatObservationTime(s.observation_time)],
    ["氣溫", formatValue(s.temperature, "°C", "")],
    ["相對濕度", formatValue(s.humidity, "%", "")],
    ["風速", formatValue(s.wind_speed, "m/s")],
    ["風向", formatValue(s.wind_direction, "°", "")],
    ["UV", formatValue(s.uv_index, "UV")],
    ["降雨量", formatValue(s.precipitation, "mm")],
  ];

  // Built with textContent so API strings are never interpreted as HTML.
  const table = document.createElement("table");
  table.className = "station-popup";
  for (const [label, value] of rows) {
    const tr = table.insertRow();
    const th = document.createElement("th");
    th.textContent = label;
    const td = tr.insertCell();
    td.textContent = value;
    if (value === MISSING) td.className = "missing";
    tr.prepend(th);
  }
  return table;
}

// Smaller markers when zoomed out so dense stations don't hide county boundaries.
function radiusForZoom(zoom: number): number {
  if (zoom <= 7) return 2.5;
  if (zoom <= 9) return 3.5;
  return STATION_STYLE.radius ?? 5;
}

export function createStationLayer(map: L.Map): L.LayerGroup {
  if (!map.getPane(STATION_PANE)) {
    map.createPane(STATION_PANE).style.zIndex = "450";
  }
  const layer = L.layerGroup();
  map.on("zoomend", () => {
    const radius = radiusForZoom(map.getZoom());
    layer.eachLayer((marker) => {
      if (marker instanceof L.CircleMarker) marker.setRadius(radius);
    });
  });
  return layer;
}

/** Replace the layer's markers with the given stations; returns how many were drawn. */
export function renderStations(map: L.Map, layer: L.LayerGroup, stations: StationObservation[]): number {
  layer.clearLayers();
  const style = { ...STATION_STYLE, radius: radiusForZoom(map.getZoom()) };
  let drawn = 0;
  for (const station of stations) {
    if (!hasValidCoordinates(station)) continue;
    L.circleMarker([station.latitude, station.longitude], style)
      .bindPopup(() => createPopupContent(station), POPUP_OPTIONS)
      .addTo(layer);
    drawn++;
  }
  return drawn;
}
