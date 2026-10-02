import {
  Chart,
  Legend,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
  type ChartDataset,
  type ScatterDataPoint,
} from "chart.js";
import { api, type CountyHistoryResponse } from "./api";
import { normalizeCountyName } from "./countyWeather";

Chart.register(LineController, LineElement, PointElement, LinearScale, Tooltip, Legend);

export type HistoryRange = "24H" | "7D" | "30D";

const RANGE_DAYS: Record<HistoryRange, number> = { "24H": 1, "7D": 7, "30D": 30 };
const RANGE_LABEL: Record<HistoryRange, string> = { "24H": "24 小時", "7D": "7 天", "30D": "30 天" };
const DEFAULT_RANGE: HistoryRange = "24H";

// Consecutive points further apart than this are not joined by a line, so
// gaps in the stored observations are shown as gaps rather than implied data.
const MAX_LINE_GAP_MS = 3 * 60 * 60 * 1000;

const MESSAGES = {
  idle: "點擊地圖上的縣市以查看歷史氣溫",
  loading: "正在取得歷史氣象資料…",
  empty: "目前沒有可用的歷史氣象資料",
  error: "目前無法取得歷史氣象資料",
} as const;

export type HistoryChartState =
  | { kind: "idle" }
  | { kind: "loading" | "empty" | "error"; county: string; range: HistoryRange }
  | { kind: "loaded"; county: string; range: HistoryRange; data: CountyHistoryResponse };

type Point = ScatterDataPoint;

function toPoints(data: CountyHistoryResponse, field: "avg_temperature" | "min_temperature" | "max_temperature"): Point[] {
  // Only times present in the API response; null values stay as gaps.
  return data.data.map((p) => ({ x: Date.parse(p.observation_time), y: p[field] ?? Number.NaN }));
}

function formatTime(ms: number, withDate: boolean): string {
  const d = new Date(ms);
  const hm = d.toLocaleTimeString("zh-TW", { hour: "2-digit", minute: "2-digit", hour12: false });
  return withDate ? `${d.getMonth() + 1}/${d.getDate()} ${hm}` : hm;
}

const HOUR_MS = 60 * 60 * 1000;

/** Axis ticks on round local times: every 4 h (24H), daily (7D), every 5 days (30D). */
function buildTicks(min: number, max: number, range: HistoryRange): number[] {
  const start = new Date(min);
  let stepMs: number;
  if (range === "24H") {
    stepMs = 4 * HOUR_MS;
    start.setMinutes(0, 0, 0);
    // first whole local hour at or after `min` that is a multiple of 4
    while (start.getTime() < min || start.getHours() % 4 !== 0) start.setTime(start.getTime() + HOUR_MS);
  } else {
    stepMs = (range === "7D" ? 1 : 5) * 24 * HOUR_MS;
    start.setHours(24, 0, 0, 0); // next local midnight
  }
  const ticks: number[] = [];
  for (let t = start.getTime(); t <= max; t += stepMs) ticks.push(t);
  return ticks;
}

function formatTick(ms: number, range: HistoryRange): string {
  const d = new Date(ms);
  return range === "24H" ? formatTime(ms, false) : `${d.getMonth() + 1}/${d.getDate()}`;
}

function datasets(data: CountyHistoryResponse): ChartDataset<"line", Point[]>[] {
  // clip: let point markers on the window edge draw past the plot area instead of being cut in half.
  const common = { spanGaps: MAX_LINE_GAP_MS, pointRadius: 3, pointHoverRadius: 5, borderWidth: 1.5, clip: 8 };
  return [
    { ...common, label: "平均氣溫", data: toPoints(data, "avg_temperature"), borderColor: "#c2410c", backgroundColor: "#c2410c", borderWidth: 2.5, pointRadius: 4 },
    { ...common, label: "最高氣溫", data: toPoints(data, "max_temperature"), borderColor: "#f59e0b", backgroundColor: "#f59e0b", borderDash: [5, 4] },
    { ...common, label: "最低氣溫", data: toPoints(data, "min_temperature"), borderColor: "#2563eb", backgroundColor: "#2563eb", borderDash: [5, 4] },
  ];
}

export interface HistoryChart {
  show(county: string): Promise<void>;
  setRange(range: HistoryRange): Promise<void>;
  getState(): HistoryChartState;
}

export function createHistoryChart(container: HTMLElement): HistoryChart {
  let state: HistoryChartState = { kind: "idle" };
  let county: string | null = null;
  let range: HistoryRange = DEFAULT_RANGE;
  let requestId = 0;
  let controller: AbortController | null = null;
  let chart: Chart<"line", Point[]> | null = null;
  let chartRange: HistoryRange = DEFAULT_RANGE; // range of the data currently in the chart

  // Static skeleton, built once: header, range buttons, message, one canvas.
  const header = document.createElement("div");
  header.className = "history-header";
  const title = document.createElement("h2");
  const buttons = document.createElement("div");
  buttons.className = "history-ranges";
  buttons.setAttribute("role", "group");
  buttons.setAttribute("aria-label", "歷史資料範圍");
  const rangeButtons = (Object.keys(RANGE_DAYS) as HistoryRange[]).map((r) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = r;
    b.dataset.range = r;
    b.addEventListener("click", () => void setRange(r));
    buttons.append(b);
    return b;
  });
  header.append(title, buttons);
  const message = document.createElement("p");
  message.className = "history-message";
  const caption = document.createElement("p");
  caption.className = "history-caption";
  const canvasBox = document.createElement("div");
  canvasBox.className = "history-canvas";
  const canvas = document.createElement("canvas");
  canvas.setAttribute("role", "img");
  canvasBox.append(canvas);
  container.replaceChildren(header, message, canvasBox, caption);

  const destroyChart = (): void => {
    chart?.destroy();
    chart = null;
  };

  const renderChart = (data: CountyHistoryResponse, r: HistoryRange): void => {
    chartRange = r;
    const sinceMs = data.since ? Date.parse(data.since) : undefined;
    const untilMs = data.until ? Date.parse(data.until) : undefined;
    const ds = datasets(data);

    if (chart) {
      // Reuse the single chart instance: swap data and axis window.
      chart.data.datasets = ds;
      const x = chart.options.scales?.x;
      if (x) {
        x.min = sinceMs;
        x.max = untilMs;
      }
      chart.update();
      return;
    }

    chart = new Chart<"line", Point[]>(canvas, {
      type: "line",
      data: { datasets: ds },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        parsing: false,
        interaction: { mode: "nearest", intersect: false, axis: "x" },
        scales: {
          x: {
            type: "linear",
            min: sinceMs,
            max: untilMs,
            afterBuildTicks: (axis) => {
              axis.ticks = buildTicks(axis.min, axis.max, chartRange).map((value) => ({ value }));
            },
            ticks: {
              autoSkip: false,
              callback: (value) => formatTick(Number(value), chartRange),
            },
            title: { display: true, text: "觀測時間" },
          },
          y: {
            title: { display: true, text: "氣溫 (°C)" },
            ticks: { callback: (value) => `${value}°C` },
          },
        },
        plugins: {
          legend: { position: "top", labels: { boxWidth: 14, boxHeight: 2 } },
          tooltip: {
            callbacks: {
              title: (items) => (items[0] ? formatTime(items[0].parsed.x ?? 0, true) : ""),
              label: (item) => `${item.dataset.label}：${item.parsed.y}°C`,
            },
          },
        },
      },
    });
  };

  const render = (): void => {
    container.dataset.state = state.kind;
    container.dataset.county = state.kind === "idle" ? "" : state.county;
    container.dataset.range = range;
    for (const b of rangeButtons) b.setAttribute("aria-pressed", String(b.dataset.range === range));

    title.textContent = state.kind === "idle" ? "歷史氣溫" : `${state.county} 歷史氣溫（${RANGE_LABEL[state.range]}）`;

    if (state.kind !== "loaded") {
      destroyChart();
      canvasBox.hidden = true;
      caption.hidden = true;
      message.hidden = false;
      message.className = `history-message ${state.kind}`;
      message.textContent = MESSAGES[state.kind];
      return;
    }

    message.hidden = true;
    canvasBox.hidden = false;
    caption.hidden = false;
    const n = state.data.available_points;
    caption.textContent = `共 ${n} 個觀測時間點（SQLite 實際資料）${n === 1 ? "，僅一筆資料，無法顯示趨勢線" : ""}`;
    canvas.setAttribute("aria-label", `${state.county} ${RANGE_LABEL[state.range]}歷史氣溫，共 ${n} 個觀測時間點`);
    renderChart(state.data, state.range);
  };

  const setState = (next: HistoryChartState): void => {
    state = next;
    render();
  };

  const load = async (): Promise<void> => {
    if (!county) return;
    const c = county;
    const r = range;

    // Abort the in-flight request; the request ID also discards any response
    // that resolves after a newer county or range was chosen.
    controller?.abort();
    const current = new AbortController();
    controller = current;
    const id = ++requestId;

    setState({ kind: "loading", county: c, range: r });
    try {
      const data = await api.countyHistory(c, RANGE_DAYS[r], current.signal);
      if (id !== requestId) return;
      setState(data.data.length > 0 ? { kind: "loaded", county: c, range: r, data } : { kind: "empty", county: c, range: r });
    } catch {
      if (id !== requestId) return; // superseded or aborted
      setState({ kind: "error", county: c, range: r });
    }
  };

  const show = (name: string): Promise<void> => {
    county = normalizeCountyName(name);
    return load();
  };

  const setRange = (next: HistoryRange): Promise<void> => {
    range = next;
    if (!county) {
      render();
      return Promise.resolve();
    }
    return load();
  };

  render();
  return { show, setRange, getState: () => state };
}
