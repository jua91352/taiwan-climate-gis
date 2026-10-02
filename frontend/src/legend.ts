import L from "leaflet";
import type { RgbColor } from "./heatmapSurface";

export interface LegendStop {
  /** 0–1 along the bar. */
  position: number;
  rgb: RgbColor;
}

export interface LegendTick {
  /** 0–1 along the bar. */
  position: number;
  label: string;
}

export interface GradientLegendOptions {
  title: string;
  ariaLabel: string;
  stops: LegendStop[];
  ticks: LegendTick[];
  /** Extra class identifying the legend (e.g. temperature-legend). */
  className: string;
}

const percent = (p: number): string => `${(p * 100).toFixed(2)}%`;

/** Compact bottom-right legend: title, continuous gradient bar, ticks, one note line. */
export function createGradientLegend(opts: GradientLegendOptions): L.Control {
  const legend = new L.Control({ position: "bottomright" });
  legend.onAdd = () => {
    const box = L.DomUtil.create("div", `map-legend ${opts.className}`);
    box.setAttribute("role", "img");
    box.setAttribute("aria-label", opts.ariaLabel);
    L.DomUtil.create("div", "map-legend-title", box).textContent = opts.title;
    const bar = L.DomUtil.create("div", "map-legend-bar", box);
    bar.style.background = `linear-gradient(to right, ${opts.stops
      .map((stop) => `rgb(${stop.rgb.join(", ")}) ${percent(stop.position)}`)
      .join(", ")})`;
    const scale = L.DomUtil.create("div", "map-legend-ticks", box);
    for (const t of opts.ticks) {
      const tick = L.DomUtil.create("span", undefined, scale);
      tick.style.left = percent(t.position);
      tick.textContent = t.label;
    }
    L.DomUtil.create("div", "map-legend-note", box).dataset.role = "count";
    L.DomEvent.disableClickPropagation(box);
    return box;
  };
  return legend;
}

/** Set the legend's note line (no-op while the legend is not on the map). */
export function setLegendNote(legend: L.Control, text: string): void {
  const note = legend.getContainer()?.querySelector<HTMLElement>("[data-role=count]");
  if (note) note.textContent = text;
}
