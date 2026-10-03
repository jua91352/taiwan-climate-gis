import L from "leaflet";
import type { RgbColor } from "./heatmapSurface";
import { createGradientLegend, setLegendNote } from "./legend";

// Colors of the CWA O-A0058-005 radar image, weakest echo first.
//
// Sampled from the collected frames (data/radar/*.png): every opaque pixel is
// one of exactly these 60 colors (the only other pixels are semi-transparent
// black, which is not echo). The order is taken from the images themselves:
// #00ffff lies on echo edges (most often next to transparent pixels), the
// cyan→blue, green, yellow→orange and red→magenta runs of 15 shades each meet
// end to end (#0000ff↔#00ff00, #009600→#33ab00…#ccea00↔#ffff00,
// #ff1800↔#ff0000, #960000→#ab0033), and the rarest colors are the strongest.
//
// LIMITATION: the PNG carries no values, so which dBZ each color stands for
// cannot be recovered from O-A0058-005 alone. These are NOT an official CWA
// dBZ→RGB table. The legend spreads the colors evenly under the official
// 5–75 dBZ tick labels, so a color's position against the ticks is approximate.
const O_A0058_005_COLORS = [
  "00ffff", "00ecff", "00daff", "00c8ff", "00b6ff", "00a3ff", "0091ff", "007fff",
  "006dff", "005bff", "0048ff", "0036ff", "0024ff", "0012ff", "0000ff",
  "00ff00", "00f400", "00e900", "00de00", "00d300", "00c800", "00be00", "00b400",
  "00aa00", "00a000", "009600", "33ab00", "66c000", "99d500", "ccea00",
  "ffff00", "fff400", "ffe900", "ffde00", "ffd300", "ffc800", "ffb800", "ffa800",
  "ff9800", "ff8800", "ff7800", "ff6000", "ff4800", "ff3000", "ff1800",
  "ff0000", "f40000", "e90000", "de0000", "d30000", "c80000", "be0000", "b40000",
  "aa0000", "a00000", "960000", "ab0033", "c00066", "d50099", "ea00cc",
] as const;

/** Official CWA radar reflectivity tick labels (dBZ). */
export const RADAR_DBZ_TICKS = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75] as const;

const toRgb = (hex: string): RgbColor => {
  const n = Number.parseInt(hex, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
};

function createRadarLegend(): L.Control {
  const last = O_A0058_005_COLORS.length - 1;
  const lastTick = RADAR_DBZ_TICKS.length - 1;
  return createGradientLegend({
    title: "雷達 回波強度",
    unit: "dBZ",
    ariaLabel: `雷達回波強度圖例（dBZ）：由淺藍（${RADAR_DBZ_TICKS[0]}）經綠、黃、紅到紫（${RADAR_DBZ_TICKS[lastTick]}），色階取自 CWA O-A0058-005 影像，刻度位置為近似`,
    stops: O_A0058_005_COLORS.map((hex, i) => ({ position: i / last, rgb: toRgb(hex) })),
    ticks: RADAR_DBZ_TICKS.map((dbz, i) => ({ position: i / lastTick, label: String(dbz) })),
    className: "radar-legend",
  });
}

/** Show the dBZ legend exactly while the 雷達 layer is on the map (UI only). */
export function attachRadarLegend(map: L.Map, radarLayer: L.Layer): void {
  const legend = createRadarLegend();
  radarLayer.on("add", () => {
    legend.addTo(map);
    setLegendNote(legend, "色階取自實際雷達影像，刻度為近似");
  });
  radarLayer.on("remove", () => legend.remove());
}
