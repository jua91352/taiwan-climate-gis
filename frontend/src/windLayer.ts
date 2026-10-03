import L from "leaflet";
import type { StationObservation } from "./api";
import type { RgbColor } from "./heatmapSurface";
import { createGradientLegend, setLegendNote } from "./legend";
import { hasValidCoordinates } from "./stations";
import { STATION_MODE_MIN_ZOOM } from "./temperatureLayer";
import { WindParticleLayer, windVector, type WindVectorSample } from "./windParticles";

const WIND_PANE = "wind";

// Wind speed (m/s, CWA O-A0003-001 WindSpeed) colors at the Beaufort scale
// boundaries (force 0/1 … 7), blended linearly in between. Same validated
// blue -> purple ramp as the rainfall layer.
export const WIND_STOPS: readonly { ms: number; color: string }[] = [
  { ms: 0, color: "#3463c9" },
  { ms: 1.6, color: "#1e9ec2" },
  { ms: 3.4, color: "#41a85a" },
  { ms: 5.5, color: "#c2b51c" },
  { ms: 8.0, color: "#ec6f1f" },
  { ms: 10.8, color: "#c62a2a" },
  { ms: 13.9, color: "#8b43b0" },
];

function hexToRgb(hex: string): RgbColor {
  const n = Number.parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

const STOPS = WIND_STOPS.map((s) => ({ ms: s.ms, rgb: hexToRgb(s.color) }));

export function windSpeedRgb(ms: number): RgbColor {
  if (ms <= STOPS[0].ms) return STOPS[0].rgb;
  for (let i = 1; i < STOPS.length; i++) {
    const hi = STOPS[i];
    if (ms <= hi.ms) {
      const lo = STOPS[i - 1];
      const f = (ms - lo.ms) / (hi.ms - lo.ms);
      const mix = (c: 0 | 1 | 2): number => Math.round(lo.rgb[c] + (hi.rgb[c] - lo.rgb[c]) * f);
      return [mix(0), mix(1), mix(2)];
    }
  }
  return STOPS[STOPS.length - 1].rgb;
}

/**
 * CSS rotation for the arrow. WindDirection is where the wind blows FROM,
 * clockwise from north (0 = 北風, 90 = 東風). The arrow points where the wind
 * goes, i.e. direction + 180°. The SVG arrow points up (north) unrotated and
 * CSS rotate() is clockwise on screen, so the angle is used directly.
 */
export function arrowRotation(windDirection: number): number {
  return (windDirection + 180) % 360;
}

const ARROW_SVG =
  '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 2 19 14h-4.5v8h-5v-8H5Z"/></svg>';

// Equal spacing on the legend bar (Beaufort steps are not linear in m/s).
const legendPosition = (index: number): number => (index + 0.5) / STOPS.length;

function createLegend(): L.Control {
  return createGradientLegend({
    title: "風速 m/s",
    ariaLabel: `風速圖例：由藍（${STOPS[0].ms} m/s）漸變到紫（${STOPS[STOPS.length - 1].ms} m/s 以上）；箭頭指向風吹去的方向`,
    stops: STOPS.map((s, i) => ({ position: legendPosition(i), rgb: s.rgb })),
    ticks: STOPS.map((s, i) => ({ position: legendPosition(i), label: String(s.ms) })),
    className: "wind-legend",
  });
}

export interface WindStats {
  /** Valid stations: position + speed (and direction unless calm). */
  drawn: number;
  calm: number;
}

export interface WindLayer {
  layer: L.LayerGroup;
  particles: WindParticleLayer;
  /** Rebuild field + arrows from the existing /api/weather/latest data (no requests). */
  render(stations: StationObservation[]): WindStats;
}

/**
 * 風速風向 main layer, all from the real CWA O-A0003-001 WindSpeed /
 * WindDirection of each station:
 * - animated particles advected through the interpolated wind field (all zooms;
 *   thinned out from station zoom up), and
 * - from station zoom up, one arrow + speed per station (calm = small ring).
 * Stations missing speed, direction or position are skipped everywhere.
 */
export function createWindLayer(map: L.Map): WindLayer {
  if (!map.getPane(WIND_PANE)) {
    // Same level as the temperature labels; non-interactive so county clicks pass through.
    map.createPane(WIND_PANE).style.zIndex = "460";
  }
  const layer = L.layerGroup();
  const arrows = L.layerGroup();
  const particles = new WindParticleLayer({ colorAt: windSpeedRgb, detailZoom: STATION_MODE_MIN_ZOOM });
  layer.addLayer(particles);
  const legend = createLegend();
  let stats: WindStats = { drawn: 0, calm: 0 };
  let total = 0;

  const updateNote = (): void => {
    setLegendNote(
      legend,
      stats.drawn > 0 ? `${stats.drawn} / ${total} 站插值 · 靜風 ${stats.calm} · 流向＝風的去向` : "目前沒有可用的風速風向資料",
    );
  };
  // Arrows only from station zoom up, so they never crowd the particle view.
  const applyZoom = (): void => {
    const detail = map.getZoom() >= STATION_MODE_MIN_ZOOM;
    if (detail !== layer.hasLayer(arrows)) {
      if (detail) layer.addLayer(arrows);
      else layer.removeLayer(arrows);
    }
  };

  layer.on("add", () => {
    legend.addTo(map);
    updateNote();
    applyZoom();
  });
  layer.on("remove", () => legend.remove());
  map.on("zoomend", () => {
    if (map.hasLayer(layer)) applyZoom();
  });

  const render = (stations: StationObservation[]): WindStats => {
    arrows.clearLayers();
    total = stations.length;
    const vectors: WindVectorSample[] = [];
    let drawn = 0;
    let calm = 0;
    for (const s of stations) {
      const speed = s.wind_speed;
      if (!hasValidCoordinates(s) || speed === null || !Number.isFinite(speed) || speed < 0) continue;
      const isCalm = speed === 0;
      const dir = s.wind_direction;
      const direction = dir !== null && Number.isFinite(dir) && dir >= 0 && dir <= 360 ? dir : null;
      if (!isCalm && direction === null) continue; // never draw a guessed direction
      vectors.push({ lat: s.latitude, lng: s.longitude, ...(isCalm || direction === null ? { u: 0, v: 0 } : windVector(speed, direction)) });

      const [r, g, b] = windSpeedRgb(speed);
      const icon = document.createElement("div");
      icon.className = isCalm ? "wind-mark wind-calm" : "wind-mark";
      icon.style.color = `rgb(${r}, ${g}, ${b})`;
      if (isCalm || direction === null) {
        icon.innerHTML = '<span class="wind-calm-dot"></span>';
      } else {
        const arrow = document.createElement("span");
        arrow.className = "wind-arrow";
        arrow.style.transform = `rotate(${arrowRotation(direction)}deg)`;
        arrow.innerHTML = ARROW_SVG;
        icon.append(arrow);
      }
      const value = document.createElement("span");
      value.className = "wind-value";
      value.textContent = speed.toFixed(1);
      icon.append(value);
      icon.dataset.speed = String(speed);
      if (direction !== null) icon.dataset.direction = String(direction);

      L.marker([s.latitude, s.longitude], {
        icon: L.divIcon({ className: "wind-icon", html: icon, iconSize: undefined }),
        pane: WIND_PANE,
        interactive: false,
        keyboard: false,
      }).addTo(arrows);
      drawn++;
      if (isCalm) calm++;
    }
    particles.setData(vectors);
    stats = { drawn, calm };
    updateNote();
    return stats;
  };

  return { layer, particles, render };
}
