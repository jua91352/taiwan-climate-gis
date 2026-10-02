import L from "leaflet";

// Small stroke icons (24×24 viewBox, currentColor) so the panel needs no icon library.
const ICON_PATHS = {
  temperature: '<path d="M14 14.76V4.5a2.5 2.5 0 0 0-5 0v10.26a4.5 4.5 0 1 0 5 0Z"/><path d="M11.5 10v7"/>',
  rain: '<path d="M7 15a4.5 4.5 0 1 1 1.6-8.7A5.5 5.5 0 0 1 19 8.5 3.5 3.5 0 0 1 18 15Z"/><path d="M8 18l-1 2.5M12.5 18l-1 2.5M17 18l-1 2.5"/>',
  radar: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><path d="M12 12l6.4-6.4"/><circle cx="12" cy="12" r="1" fill="currentColor"/>',
  typhoon: '<circle cx="12" cy="12" r="2.5"/><path d="M5 10.5A7.5 7.5 0 0 1 17.5 5"/><path d="M19 13.5A7.5 7.5 0 0 1 6.5 19"/>',
  wind: '<path d="M3 8h10.5a2.5 2.5 0 1 0-2.4-3.2"/><path d="M3 12h15.5a2.5 2.5 0 1 1-2.4 3.2"/><path d="M3 16h7"/>',
  humidity: '<path d="M12 3.5s6 6.4 6 10.5a6 6 0 0 1-12 0c0-4.1 6-10.5 6-10.5Z"/>',
  weather: '<circle cx="9" cy="8" r="3"/><path d="M9 2.5v1M3.5 8h1M5.1 4.1l.7.7M12.9 4.1l-.7.7"/><path d="M8 20a4 4 0 0 1-.5-8 5 5 0 0 1 9.6 1.2A3.4 3.4 0 0 1 17 20Z"/>',
  station: '<path d="M19 10c0 5.5-7 11-7 11s-7-5.5-7-11a7 7 0 0 1 14 0Z"/><circle cx="12" cy="10" r="2.5"/>',
  boundary: '<path d="M4 6.5 9 4l6 2.5L20 4v13.5L15 20l-6-2.5L4 20Z" stroke-dasharray="3 2.5"/>',
  label: '<rect x="3" y="6" width="18" height="12" rx="3"/><path d="M8 10v4M11 10h2.5v2H11v2h2.5M16.5 10h-1"/>',
  locate: '<circle cx="12" cy="12" r="6.5"/><circle cx="12" cy="12" r="2"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5Z"/><path d="m3 13 9 5 9-5"/>',
  chevron: '<path d="m6 9 6 6 6-6"/>',
} as const;

export type IconName = keyof typeof ICON_PATHS;

function icon(name: IconName): string {
  return `<svg class="mc-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${ICON_PATHS[name]}</svg>`;
}

/** On/off state the panel reads and writes; usually backed by a Leaflet layer. */
export interface Toggle {
  isOn(): boolean;
  set(on: boolean): void;
}

export function layerToggle(map: L.Map, layer: L.Layer): Toggle {
  return {
    isOn: () => map.hasLayer(layer),
    set: (on) => {
      if (on) map.addLayer(layer);
      else map.removeLayer(layer);
    },
  };
}

export interface ControlItem {
  label: string;
  icon: IconName;
  /** Omitted → shown as 即將提供: visible, focusable, but does nothing. */
  toggle?: Toggle;
}

export interface BaseMapOption {
  label: string;
  layer: L.Layer;
  /** Real tile(s) of this provider covering Taiwan (CSS background layers, top first). */
  previewTiles: string[];
}

export interface MapControlsOptions {
  /** Weather layers, shown as a tile grid. */
  layers: ControlItem[];
  /** Display options, shown as switches. */
  options: ControlItem[];
  baseMaps: BaseMapOption[];
  /** 定位我的位置 handler; omitted → shown as 即將提供. Resolves when finished. */
  locate?: () => Promise<void>;
  /** Start collapsed (used on narrow screens). */
  collapsed?: boolean;
}

const COMING_SOON = "即將提供";

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, html?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (html !== undefined) node.innerHTML = html;
  return node;
}

function sectionTitle(text: string, id: string): HTMLElement {
  const title = el("h2", "mc-section-title");
  title.id = id;
  title.textContent = text;
  return title;
}

/**
 * Right-side floating panel replacing Leaflet's default layer control. It only
 * drives the existing layers (map.addLayer / removeLayer); nothing is created here.
 * Returned as a plain element that floats beside the header (outside the map's
 * stacking context), so an expanded panel on a phone can sit above the header.
 */
export interface MapControls {
  element: HTMLElement;
  setCollapsed(collapsed: boolean): void;
}

export function createMapControls(map: L.Map, opts: MapControlsOptions): MapControls {
  const refreshers: Array<() => void> = [];
  const refreshAll = (): void => refreshers.forEach((fn) => fn());

  const panel = el("section", "map-controls");
  panel.setAttribute("aria-label", "地圖控制");
  if (opts.collapsed) panel.classList.add("is-collapsed");

  const head = el("button", "mc-head", `${icon("layers")}<span>圖層與底圖</span>${icon("chevron")}`);
  head.type = "button";
  head.setAttribute("aria-expanded", String(!opts.collapsed));
  head.setAttribute("aria-controls", "map-controls-body");
  const setCollapsed = (collapsed: boolean): void => {
    panel.classList.toggle("is-collapsed", collapsed);
    head.setAttribute("aria-expanded", String(!collapsed));
  };
  head.addEventListener("click", () => setCollapsed(!panel.classList.contains("is-collapsed")));

  const body = el("div", "mc-body");
  body.id = "map-controls-body";

  // --- Weather layer tiles ---
  const layerGrid = el("div", "mc-layer-grid");
  layerGrid.setAttribute("role", "group");
  layerGrid.setAttribute("aria-labelledby", "mc-title-layers");
  for (const item of opts.layers) {
    const btn = el("button", "mc-layer", `${icon(item.icon)}<span class="mc-layer-label">${item.label}</span>`);
    btn.type = "button";
    const toggle = item.toggle;
    if (!toggle) {
      btn.classList.add("is-soon");
      btn.setAttribute("aria-disabled", "true");
      btn.setAttribute("aria-label", `${item.label}（${COMING_SOON}）`);
      btn.title = `${item.label}：${COMING_SOON}`;
      btn.insertAdjacentHTML("beforeend", `<span class="mc-soon" aria-hidden="true">${COMING_SOON}</span>`);
    } else {
      btn.addEventListener("click", () => {
        toggle.set(!toggle.isOn());
        refreshAll();
      });
      refreshers.push(() => btn.setAttribute("aria-pressed", String(toggle.isOn())));
    }
    layerGrid.append(btn);
  }

  // --- Display option switches ---
  const optionList = el("div", "mc-options");
  for (const item of opts.options) {
    const row = el("label", "mc-option");
    const input = el("input");
    input.type = "checkbox";
    input.setAttribute("role", "switch");
    const toggle = item.toggle;
    if (toggle) {
      input.addEventListener("change", () => {
        toggle.set(input.checked);
        refreshAll();
      });
      refreshers.push(() => {
        input.checked = toggle.isOn();
      });
    } else {
      input.disabled = true;
    }
    row.append(input);
    row.insertAdjacentHTML(
      "beforeend",
      `${icon(item.icon)}<span class="mc-option-label">${item.label}</span><span class="mc-switch" aria-hidden="true"></span>`,
    );
    optionList.append(row);
  }

  // --- Base map cards (native radios: arrow keys move between them) ---
  const baseGrid = el("div", "mc-base-grid");
  baseGrid.setAttribute("role", "radiogroup");
  baseGrid.setAttribute("aria-labelledby", "mc-title-base");
  for (const base of opts.baseMaps) {
    const card = el("label", "mc-base");
    const input = el("input");
    input.type = "radio";
    input.name = "mc-base-map";
    input.addEventListener("change", () => {
      if (!input.checked) return;
      for (const other of opts.baseMaps) {
        if (other !== base && map.hasLayer(other.layer)) map.removeLayer(other.layer);
      }
      map.addLayer(base.layer);
      refreshAll();
    });
    refreshers.push(() => {
      input.checked = map.hasLayer(base.layer);
    });
    const thumb = el("span", "mc-base-thumb");
    thumb.setAttribute("aria-hidden", "true");
    thumb.style.backgroundImage = base.previewTiles.map((url) => `url("${url}")`).join(", ");
    const name = el("span", "mc-base-name");
    name.textContent = base.label;
    card.append(input, thumb, name);
    baseGrid.append(card);
  }

  // --- Locate ---
  const LOCATE_LABEL = "定位我的位置";
  const locate = el("button", "mc-locate", `${icon("locate")}<span class="mc-locate-label">${LOCATE_LABEL}</span>`);
  locate.type = "button";
  const runLocate = opts.locate;
  if (!runLocate) {
    locate.classList.add("is-soon");
    locate.insertAdjacentHTML("beforeend", `<span class="mc-soon" aria-hidden="true">${COMING_SOON}</span>`);
    locate.setAttribute("aria-disabled", "true");
    locate.setAttribute("aria-label", `${LOCATE_LABEL}（${COMING_SOON}）`);
    locate.title = `${LOCATE_LABEL}：${COMING_SOON}`;
  } else {
    const label = locate.querySelector<HTMLElement>(".mc-locate-label");
    locate.addEventListener("click", () => {
      if (locate.getAttribute("aria-busy") === "true") return;
      locate.setAttribute("aria-busy", "true");
      if (label) label.textContent = "定位中…";
      void runLocate().finally(() => {
        locate.removeAttribute("aria-busy");
        if (label) label.textContent = LOCATE_LABEL;
      });
    });
  }

  body.append(
    sectionTitle("圖層", "mc-title-layers"),
    layerGrid,
    optionList,
    sectionTitle("底圖", "mc-title-base"),
    baseGrid,
    locate,
  );
  panel.append(head, body);

  refreshAll();
  return { element: panel, setCollapsed };
}
