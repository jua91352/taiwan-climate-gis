import L from "leaflet";
import type { StationObservation } from "./api";
import { CITY_CENTERS } from "./gis";
import { formatObservationTime } from "./stations";

// ---------------------------------------------------------------------------
// Classification of CWA O-A0003-001 Weather text into a few display classes.

export type WeatherClass =
  | "thunder"
  | "heavyRain"
  | "rain"
  | "snow"
  | "fog"
  | "overcast"
  | "cloudy"
  | "clear"
  | "other"
  | "nodata";

interface WeatherClassInfo {
  icon: string;
  label: string;
  /** Tie-breaker: on equal station counts the more severe class wins. */
  severity: number;
}

export const WEATHER_CLASSES: Record<WeatherClass, WeatherClassInfo> = {
  thunder: { icon: "⛈️", label: "雷雨", severity: 8 },
  heavyRain: { icon: "☔", label: "大雨", severity: 7 },
  rain: { icon: "🌧️", label: "有雨", severity: 6 },
  snow: { icon: "❄️", label: "雪", severity: 5 },
  fog: { icon: "🌫️", label: "霧", severity: 4 },
  overcast: { icon: "☁️", label: "陰", severity: 3 },
  cloudy: { icon: "🌤️", label: "多雲", severity: 2 },
  clear: { icon: "☀️", label: "晴", severity: 1 },
  other: { icon: "❔", label: "其他", severity: 0 },
  nodata: { icon: "⚪", label: "資料不足", severity: -1 },
};

/**
 * Map a CWA Weather text to a class. Checked from most to least significant,
 * so a compound text takes its strongest part ("陰有雨" -> 有雨, "陰有雷雨" ->
 * 雷雨). Haze (靄/霾) is reduced visibility on top of the sky state, so
 * "晴有靄" stays 晴; only real fog (霧) is the 霧 class. Unrecognised text is 其他.
 */
export function classifyWeather(text: string | null | undefined): WeatherClass {
  const t = (text ?? "").trim();
  if (!t) return "nodata";
  if (t.includes("雷")) return "thunder";
  if (/[雪霰冰]/.test(t)) return "snow";
  if (/大雨|豪雨/.test(t)) return "heavyRain";
  if (t.includes("雨")) return "rain";
  if (t.includes("霧")) return "fog";
  if (t.includes("陰")) return "overcast";
  if (t.includes("多雲")) return "cloudy";
  if (t.includes("晴")) return "clear";
  return "other";
}

// ---------------------------------------------------------------------------
// County aggregation.

export interface CountyWeather {
  county: string;
  weather: WeatherClass;
  /** Stations in the county with a usable Weather text. */
  validStations: number;
  /** Station count per class (valid stations only). */
  counts: Partial<Record<WeatherClass, number>>;
}

/**
 * Representative weather of one county: the class reported by the most of its
 * stations with a valid Weather; on a tie the more severe class wins
 * (WEATHER_CLASSES.severity), so the result never depends on station order.
 * No valid station -> "nodata".
 */
export function aggregateCounty(county: string, stations: StationObservation[]): CountyWeather {
  const counts: Partial<Record<WeatherClass, number>> = {};
  let validStations = 0;
  for (const s of stations) {
    const c = classifyWeather(s.weather);
    if (c === "nodata") continue;
    counts[c] = (counts[c] ?? 0) + 1;
    validStations++;
  }
  let weather: WeatherClass = "nodata";
  let best = 0;
  for (const [c, n] of Object.entries(counts) as [WeatherClass, number][]) {
    if (n > best || (n === best && WEATHER_CLASSES[c].severity > WEATHER_CLASSES[weather].severity)) {
      weather = c;
      best = n;
    }
  }
  return { county, weather, validStations, counts };
}

/** One entry per county in CITY_CENTERS (all 22), whether or not it has data. */
export function aggregateCounties(stations: StationObservation[]): CountyWeather[] {
  const byCounty = new Map<string, StationObservation[]>();
  for (const s of stations) {
    if (!s.county_name) continue;
    const list = byCounty.get(s.county_name) ?? [];
    list.push(s);
    byCounty.set(s.county_name, list);
  }
  return Object.keys(CITY_CENTERS).map((county) => aggregateCounty(county, byCounty.get(county) ?? []));
}

// ---------------------------------------------------------------------------
// Layer: one marker per county at its CITY_CENTERS point, popup, legend.

const LEGEND_CLASSES: WeatherClass[] = ["clear", "cloudy", "overcast", "rain", "heavyRain", "thunder", "fog", "snow", "nodata"];

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className: string, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function markerIcon(cw: CountyWeather): L.DivIcon {
  const info = WEATHER_CLASSES[cw.weather];
  const pill = el("div", cw.weather === "nodata" ? "weather-pill weather-pill-nodata" : "weather-pill");
  pill.append(el("span", "weather-pill-icon", info.icon), el("span", "weather-pill-name", cw.county));
  return L.divIcon({ className: "weather-pill-anchor", html: pill, iconSize: undefined });
}

function popupContent(cw: CountyWeather, observationTime: string | null): HTMLElement {
  const box = el("div", "weather-popup");
  box.append(el("div", "weather-popup-title", cw.county));
  const info = WEATHER_CLASSES[cw.weather];
  if (cw.weather === "nodata") {
    box.append(el("div", "weather-popup-main", `${info.icon} ${info.label}`), el("div", "weather-popup-note", "該縣市目前沒有有效天氣資料。"));
    return box;
  }
  box.append(
    el("div", "weather-popup-main", `${info.icon} ${info.label}`),
    el("div", "weather-popup-row", `${cw.validStations} 個有效測站`),
    el("div", "weather-popup-row", `多數測站天氣：${info.label}（${cw.counts[cw.weather]} 站）`),
  );
  if (observationTime) box.append(el("div", "weather-popup-note", `觀測時間：${formatObservationTime(observationTime)}`));
  return box;
}

function createLegend(): L.Control {
  const legend = new L.Control({ position: "bottomright" });
  legend.onAdd = () => {
    const box = L.DomUtil.create("div", "map-legend weather-legend");
    box.setAttribute("role", "img");
    box.setAttribute("aria-label", `天氣圖例：${LEGEND_CLASSES.map((c) => WEATHER_CLASSES[c].label).join("、")}`);
    L.DomUtil.create("div", "map-legend-title", box).textContent = "天氣";
    const grid = L.DomUtil.create("div", "weather-legend-grid", box);
    for (const c of LEGEND_CLASSES) {
      const row = L.DomUtil.create("span", "weather-legend-item", grid);
      row.append(el("span", "weather-legend-icon", WEATHER_CLASSES[c].icon), document.createTextNode(WEATHER_CLASSES[c].label));
    }
    L.DomUtil.create("div", "map-legend-note", box).textContent = "各縣市多數測站的天氣";
    L.DomEvent.disableClickPropagation(box);
    return box;
  };
  return legend;
}

export interface WeatherLayer {
  layer: L.LayerGroup;
  /** Rebuild county markers from the existing /api/weather/latest data (no requests). */
  render(stations: StationObservation[], observationTime: string | null): CountyWeather[];
}

/**
 * 天氣 main layer: one representative-weather marker per county + legend.
 * Where pills would overlap at the current zoom (the northern county centers
 * are close together), lower-priority ones show only their icon; the county
 * name stays in the tooltip/popup and returns once there is room.
 */
export function createWeatherLayer(map: L.Map): WeatherLayer {
  const layer = L.layerGroup();
  const markers = L.layerGroup();
  layer.addLayer(markers);
  const legend = createLegend();
  // Declutter priority: more valid stations first, then county name (stable).
  let placed: { cw: CountyWeather; marker: L.Marker }[] = [];

  const declutter = (): void => {
    if (!map.hasLayer(layer)) return;
    const taken: DOMRect[] = [];
    const overlaps = (r: DOMRect): boolean =>
      taken.some((t) => r.left < t.right + 2 && r.right > t.left - 2 && r.top < t.bottom + 2 && r.bottom > t.top - 2);
    for (const { marker } of placed) {
      const pill = marker.getElement()?.querySelector<HTMLElement>(".weather-pill");
      if (!pill) continue;
      pill.classList.remove("weather-pill-compact");
      if (overlaps(pill.getBoundingClientRect())) pill.classList.add("weather-pill-compact");
      taken.push(pill.getBoundingClientRect());
    }
  };

  layer.on("add", () => {
    legend.addTo(map);
    declutter();
  });
  layer.on("remove", () => legend.remove());
  map.on("zoomend", declutter);

  const render = (stations: StationObservation[], observationTime: string | null): CountyWeather[] => {
    markers.clearLayers();
    placed = [];
    const counties = aggregateCounties(stations);
    for (const cw of counties) {
      const center = CITY_CENTERS[cw.county];
      const info = WEATHER_CLASSES[cw.weather];
      const marker = L.marker([center.lat, center.lng], { icon: markerIcon(cw), title: `${cw.county}：${info.label}`, alt: `${cw.county} ${info.label}` })
        .bindPopup(() => popupContent(cw, observationTime), { closeButton: true, autoPanPadding: [24, 24] })
        .addTo(markers);
      placed.push({ cw, marker });
    }
    placed.sort((a, b) => b.cw.validStations - a.cw.validStations || a.cw.county.localeCompare(b.cw.county, "zh-Hant"));
    declutter();
    return counties;
  };

  return { layer, render };
}
