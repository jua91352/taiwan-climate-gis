// Data source shown in the header for whichever main weather layer is on.
// Layers without data yet stay null (即將提供); add their entry when built.

export type MainLayerId = "temperature" | "rainfall" | "radar" | "typhoon" | "wind" | "humidity" | "weather" | "stations";

export interface LayerSource {
  provider: string;
  dataset: string;
  description: string;
}

const CWA = "中央氣象署";

export const MAIN_LAYER_SOURCES: Record<MainLayerId, LayerSource | null> = {
  temperature: { provider: CWA, dataset: "O-A0003-001", description: "氣溫" },
  rainfall: { provider: CWA, dataset: "O-A0002-001", description: "過去 1 小時雨量" },
  radar: null,
  typhoon: null,
  wind: null,
  humidity: null,
  weather: null,
  // 測站點位 draws the same /api/weather/latest stations as 氣溫.
  stations: { provider: CWA, dataset: "O-A0003-001", description: "測站點位" },
};

const isMainLayerId = (id: string): id is MainLayerId => id in MAIN_LAYER_SOURCES;

export interface SourceStatus {
  /** The main layer now shown (null = none). */
  setActive(id: string | null): void;
  /** Per-layer detail such as observation time / station count. */
  setDetail(id: MainLayerId, text: string): void;
}

/** Renders "資料來源：…" into the existing header status line. */
export function createSourceStatus(element: HTMLElement | null): SourceStatus {
  const details = new Map<MainLayerId, string>();
  let active: MainLayerId | null = null;

  const render = (): void => {
    if (!element) return;
    if (!active) {
      element.textContent = "目前未開啟主氣象圖層";
      return;
    }
    const source = MAIN_LAYER_SOURCES[active];
    if (!source) {
      element.textContent = "資料來源：即將提供";
      return;
    }
    const detail = details.get(active) ?? "資料載入中…";
    element.textContent = `資料來源：${source.provider} ${source.dataset}（${source.description}）| ${detail}`;
  };

  return {
    setActive(id) {
      active = id !== null && isMainLayerId(id) ? id : null;
      render();
    },
    setDetail(id, text) {
      details.set(id, text);
      if (id === active) render();
    },
  };
}
