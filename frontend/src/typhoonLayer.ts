import L from "leaflet";
import { api, type Typhoon, type TyphoonAnalysisPoint, type TyphoonForecastPoint, type TyphoonWindCircle } from "./api";
import { formatObservationTime, formatValue } from "./stations";

const MISSING = "資料不足";
const TRACK_COLOR = "#b91c1c";
const PROBABILITY_COLOR = "#d97706";
const WIND15_COLOR = "#ea580c";
const WIND25_COLOR = "#b91c1c";
// Same swirl as the 颱風 tile icon (mapControls.ts).
const TYPHOON_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="2.5"/><path d="M5 10.5A7.5 7.5 0 0 1 17.5 5"/><path d="M19 13.5A7.5 7.5 0 0 1 6.5 19"/></svg>';

const COMPASS: Record<string, string> = {
  N: "北", NNE: "北北東", NE: "東北", ENE: "東北東", E: "東", ESE: "東南東", SE: "東南", SSE: "南南東",
  S: "南", SSW: "南南西", SW: "西南", WSW: "西南西", W: "西", WNW: "西北西", NW: "西北", NNW: "北北西",
};
const QUADRANT_LABELS = { NE: "東北", SE: "東南", SW: "西南", NW: "西北" } as const;

const direction = (code: string | null): string => (code ? (COMPASS[code] ? `${COMPASS[code]}（${code}）` : code) : MISSING);

/** CWA time as CWA wrote it (its own +08:00): "2026-10-06 02:00". */
function cwaTime(iso: string): string {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(iso);
  return m ? `${m[1]} ${m[2]}` : MISSING;
}

const position = (p: { latitude: number; longitude: number }): string =>
  `${Math.abs(p.latitude).toFixed(1)}°${p.latitude >= 0 ? "N" : "S"} ${Math.abs(p.longitude).toFixed(1)}°${p.longitude >= 0 ? "E" : "W"}`;

/** Wind radius text; per-quadrant radii are listed as CWA sent them. */
function windCircleText(circle: TyphoonWindCircle | null): string {
  if (!circle) return MISSING;
  const radius = formatValue(circle.radius, "km");
  const quadrants = circle.quadrants
    ? (Object.keys(QUADRANT_LABELS) as (keyof typeof QUADRANT_LABELS)[])
        .filter((q) => circle.quadrants?.[q] !== undefined)
        .map((q) => `${QUADRANT_LABELS[q]} ${circle.quadrants?.[q]}`)
        .join("、")
    : "";
  return quadrants ? `${radius}（各象限：${quadrants} km）` : radius;
}

interface Names {
  zh: string;
  en: string | null;
  number: string | null;
}

function names(t: Typhoon): Names {
  return {
    zh: t.cwa_typhoon_name ?? (t.cwa_ty_no === null ? "熱帶性低氣壓" : "颱風"),
    en: t.typhoon_name,
    number: t.cwa_ty_no !== null ? `CWA #${t.cwa_ty_no}` : t.cwa_td_no !== null ? `TD #${t.cwa_td_no}` : null,
  };
}

/** Popup body built with textContent, so API strings are never interpreted as HTML. */
function popup(title: string, subtitle: string, rows: [string, string][], note: string | null): HTMLElement {
  const box = document.createElement("div");
  box.className = "typhoon-popup";
  const h = document.createElement("strong");
  h.className = "typhoon-popup-title";
  h.textContent = title;
  const sub = document.createElement("div");
  sub.className = "typhoon-popup-subtitle";
  sub.textContent = subtitle;
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
  box.append(h, sub, table);
  if (note) {
    const p = document.createElement("p");
    p.className = "typhoon-popup-note";
    p.textContent = note;
    box.append(p);
  }
  return box;
}

const titleOf = (n: Names): string => [n.zh, n.en].filter(Boolean).join(" ");

function currentPopup(t: Typhoon, p: TyphoonAnalysisPoint): HTMLElement {
  const n = names(t);
  return popup(titleOf(n), "目前位置（最新定位）", [
    ["颱風編號", t.cwa_ty_no !== null ? `CWA 第 ${t.cwa_ty_no} 號` : MISSING],
    ["熱帶性低氣壓編號", t.cwa_td_no !== null ? `第 ${t.cwa_td_no} 號` : MISSING],
    ["定位時間", cwaTime(p.datetime)],
    ["中心位置", position(p)],
    ["最大風速", formatValue(p.max_wind_speed, "m/s")],
    ["瞬間最大陣風", formatValue(p.max_gust_speed, "m/s")],
    ["中心氣壓", formatValue(p.pressure, "hPa")],
    ["移動速度", formatValue(p.moving_speed, "km/h")],
    ["移動方向", direction(p.moving_direction)],
    ["15 m/s 風圈半徑", windCircleText(p.circle15ms)],
    ["25 m/s 風圈半徑", windCircleText(p.circle25ms)],
  ], p.moving_prediction?.["zh-hant"] ? `預測動態：${p.moving_prediction["zh-hant"]}` : null);
}

function forecastPopup(t: Typhoon, p: TyphoonForecastPoint): HTMLElement {
  return popup(titleOf(names(t)), `預測 +${p.forecast_hour} 小時`, [
    ["有效時間", cwaTime(p.valid_time)],
    ["預測位置", position(p)],
    ["中心氣壓", formatValue(p.pressure, "hPa")],
    ["最大風速", formatValue(p.max_wind_speed, "m/s")],
    ["瞬間最大陣風", formatValue(p.max_gust_speed, "m/s")],
    ["移動速度", formatValue(p.moving_speed, "km/h")],
    ["移動方向", direction(p.moving_direction)],
    ["70% 機率半徑", formatValue(p.radius70_probability, "km")],
  ], p.state_transfer?.["zh-hant"] ? `型態變化：${p.state_transfer["zh-hant"]}` : null);
}

function centerIcon(n: Names): L.DivIcon {
  const root = document.createElement("div");
  root.className = "typhoon-center";
  root.innerHTML = `<span class="typhoon-center-symbol">${TYPHOON_SVG}</span>`;
  const label = document.createElement("span");
  label.className = "typhoon-center-label";
  for (const [text, cls] of [[n.zh, "typhoon-center-zh"], [n.en, ""], [n.number, ""]] as const) {
    if (!text) continue;
    const line = document.createElement("span");
    if (cls) line.className = cls;
    line.textContent = text;
    label.append(line);
  }
  root.append(label);
  return L.divIcon({ className: "typhoon-center-icon", html: root, iconSize: [30, 30], iconAnchor: [15, 15], popupAnchor: [0, -14] });
}

function forecastIcon(hour: number): L.DivIcon {
  const root = document.createElement("div");
  root.className = "typhoon-forecast";
  root.innerHTML = '<span class="typhoon-forecast-dot"></span>';
  const label = document.createElement("span");
  label.className = "typhoon-forecast-label";
  label.textContent = `+${hour}h`;
  root.append(label);
  return L.divIcon({ className: "typhoon-forecast-icon", html: root, iconSize: [12, 12], iconAnchor: [6, 6], popupAnchor: [0, -6] });
}

const latLng = (p: { latitude: number; longitude: number }): L.LatLngTuple => [p.latitude, p.longitude];
const kmCircle = (p: { latitude: number; longitude: number }, km: number, style: L.CircleMarkerOptions): L.Circle =>
  L.circle(latLng(p), { radius: km * 1000, interactive: false, ...style });

function drawTyphoon(group: L.LayerGroup, t: Typhoon): void {
  const n = names(t);
  const current = t.analysis.at(-1);

  // Forecast first so the observed track and markers sit on top of it.
  for (const f of t.forecast) {
    // CWA's 70% probability radius around each forecast position (no cone is drawn between them).
    if (f.radius70_probability !== null && f.radius70_probability > 0) {
      kmCircle(f, f.radius70_probability, { color: PROBABILITY_COLOR, weight: 1, dashArray: "4 4", fillColor: PROBABILITY_COLOR, fillOpacity: 0.06 }).addTo(group);
    }
  }
  const forecastLine = [...(current ? [latLng(current)] : []), ...t.forecast.map(latLng)];
  if (forecastLine.length >= 2) {
    L.polyline(forecastLine, { color: TRACK_COLOR, weight: 2.5, dashArray: "8 7", interactive: false }).addTo(group);
  }
  if (t.analysis.length >= 2) {
    L.polyline(t.analysis.map(latLng), { color: TRACK_COLOR, weight: 3.5, interactive: false }).addTo(group);
  }
  for (const f of t.forecast) {
    L.marker(latLng(f), { icon: forecastIcon(f.forecast_hour), title: `${n.zh} 預測 +${f.forecast_hour} 小時` })
      .bindPopup(forecastPopup(t, f), { maxWidth: 280 })
      .addTo(group);
  }
  if (current) {
    // Wind radii at the current position only. CWA also gives per-quadrant radii
    // (listed in the popup); the circle uses CWA's single overall radius and does
    // not try to draw the quadrant shape.
    if (current.circle15ms?.radius) {
      kmCircle(current, current.circle15ms.radius, { color: WIND15_COLOR, weight: 1.5, fillColor: WIND15_COLOR, fillOpacity: 0.1 }).addTo(group);
    }
    if (current.circle25ms?.radius) {
      kmCircle(current, current.circle25ms.radius, { color: WIND25_COLOR, weight: 1.5, fillColor: WIND25_COLOR, fillOpacity: 0.16 }).addTo(group);
    }
    L.marker(latLng(current), { icon: centerIcon(n), title: `${titleOf(n)} 目前位置`, zIndexOffset: 1000 })
      .bindPopup(currentPopup(t, current), { maxWidth: 300 })
      .addTo(group);
  }
}

/** Every drawn position, including the 70% probability circles' extent. */
function trackBounds(typhoons: Typhoon[]): L.LatLngBounds {
  const bounds = L.latLngBounds([]);
  for (const t of typhoons) {
    for (const p of t.analysis) bounds.extend(latLng(p));
    for (const f of t.forecast) {
      bounds.extend(latLng(f));
      if (f.radius70_probability) bounds.extend(L.latLng(latLng(f)).toBounds(f.radius70_probability * 2000));
    }
  }
  return bounds;
}

function createLegend(): L.Control {
  const legend = new L.Control({ position: "bottomright" });
  legend.onAdd = () => {
    const box = L.DomUtil.create("div", "map-legend typhoon-legend");
    L.DomUtil.create("div", "map-legend-title", box).textContent = "颱風路徑";
    const item = (svg: string, text: string): void => {
      const row = L.DomUtil.create("div", "typhoon-legend-item", box);
      row.innerHTML = `<svg viewBox="0 0 28 12" aria-hidden="true">${svg}</svg>`;
      row.append(document.createTextNode(text));
    };
    item(`<line x1="2" y1="6" x2="26" y2="6" stroke="${TRACK_COLOR}" stroke-width="3"/>`, "過去路徑");
    item(`<line x1="2" y1="6" x2="26" y2="6" stroke="${TRACK_COLOR}" stroke-width="2" stroke-dasharray="5 4"/>`, "預測路徑");
    item(`<circle cx="14" cy="6" r="5" fill="${PROBABILITY_COLOR}" fill-opacity="0.15" stroke="${PROBABILITY_COLOR}" stroke-dasharray="2 2"/>`, "70% 機率半徑");
    item(`<circle cx="14" cy="6" r="5" fill="${WIND15_COLOR}" fill-opacity="0.25" stroke="${WIND15_COLOR}"/>`, "目前 15 / 25 m/s 風圈");
    L.DomUtil.create("div", "map-legend-note", box).textContent = "點選颱風或預測點查看詳細資料";
    L.DomEvent.disableClickPropagation(box);
    return box;
  };
  return legend;
}

export interface TyphoonLayer {
  layer: L.LayerGroup;
}

/**
 * 颱風 main layer: every active tropical cyclone from /api/typhoon/latest (CWA
 * W-C0034-005): observed track (solid), forecast track (dashed), current
 * position with its 15 / 25 m/s wind radii, forecast points with CWA's 70%
 * probability circles, and popups. Fetched once each time the layer is shown
 * (the backend caches CWA for 10 minutes); the map fits the tracks once per
 * opening, since cyclones are often far from Taiwan. `onStatus` receives the
 * header detail text.
 */
export function createTyphoonLayer(
  map: L.Map,
  onStatus: (detail: string) => void,
  fitPadding: () => Pick<L.FitBoundsOptions, "paddingTopLeft" | "paddingBottomRight"> = () => ({}),
): TyphoonLayer {
  const layer = L.layerGroup();
  const legend = createLegend();
  let request = 0;

  const clear = (): void => {
    layer.clearLayers();
    legend.remove();
  };

  const load = async (): Promise<void> => {
    const current = ++request;
    clear();
    onStatus("資料載入中…");
    try {
      const res = await api.typhoonLatest();
      if (current !== request) return; // closed (or reopened) meanwhile
      // The backend could not refresh from CWA and serves its last good data.
      const stale = res.refresh_error ? "（暫時無法更新）" : "";
      if (res.typhoons.length === 0) {
        onStatus(`目前沒有活動中的颱風${stale}`);
        return;
      }
      for (const t of res.typhoons) drawTyphoon(layer, t);
      legend.addTo(map);
      onStatus(`最新定位：${formatObservationTime(res.updated_at)}${stale} | 熱帶氣旋 ${res.typhoons.length} 個`);
      const bounds = trackBounds(res.typhoons);
      if (bounds.isValid()) map.fitBounds(bounds, { maxZoom: 7, ...fitPadding() });
    } catch (error) {
      if (current !== request) return;
      clear();
      console.error("Typhoon: /api/typhoon/latest request failed.", error);
      onStatus("目前無法取得颱風資料");
    }
  };

  layer.on("add", () => void load());
  layer.on("remove", () => {
    request++; // drop a response still on its way
    clear();
  });

  return { layer };
}
