import { api, ApiError, type CountyWeatherResponse } from "./api";
import { formatObservationTime, formatValue } from "./stations";

export type CountyWeatherState =
  | { kind: "idle" }
  | { kind: "loading"; county: string }
  | { kind: "loaded"; county: string; data: CountyWeatherResponse }
  | { kind: "empty"; county: string }
  | { kind: "error"; county: string };

const MESSAGES = {
  idle: "點擊地圖上的縣市以查看目前天氣",
  loading: "正在取得目前天氣資料…",
  empty: "目前沒有可用的氣象資料",
  error: "目前無法取得該縣市氣象資料",
} as const;

/** Matches the backend's handling of the common 台/臺 variant. */
export function normalizeCountyName(name: string): string {
  return name.trim().replace(/台/g, "臺");
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function stat(label: string, value: string): HTMLElement {
  const item = el("div", "county-stat");
  item.append(el("span", "county-stat-label", label), el("span", "county-stat-value", value));
  if (value.includes("資料不足")) item.classList.add("missing");
  return item;
}

function stationTable(data: CountyWeatherResponse): HTMLElement {
  const details = el("details", "county-stations");
  details.append(el("summary", undefined, `測站明細（${data.data.length}）`));
  const table = el("table");
  const head = table.createTHead().insertRow();
  for (const h of ["測站", "鄉鎮", "氣溫", "相對濕度", "風速"]) head.append(el("th", undefined, h));
  const body = table.createTBody();
  for (const s of data.data) {
    const row = body.insertRow();
    for (const value of [
      s.station_name,
      s.town_name ?? "資料不足",
      formatValue(s.temperature, "°C", ""),
      formatValue(s.humidity, "%", ""),
      formatValue(s.wind_speed, "m/s"),
    ]) {
      row.insertCell().textContent = value;
    }
  }
  const scroll = el("div", "county-stations-scroll");
  scroll.append(table);
  details.append(scroll);
  return details;
}

export interface CountyWeatherPanel {
  show(county: string): Promise<void>;
  getState(): CountyWeatherState;
}

export function createCountyWeatherPanel(container: HTMLElement): CountyWeatherPanel {
  let state: CountyWeatherState = { kind: "idle" };
  let requestId = 0;
  let controller: AbortController | null = null;

  const render = (): void => {
    container.dataset.state = state.kind;
    container.dataset.county = state.kind === "idle" ? "" : state.county;

    const header = el("div", "county-panel-header");
    header.append(el("h2", undefined, state.kind === "idle" ? "縣市目前天氣" : state.county));

    if (state.kind !== "loaded") {
      container.replaceChildren(header, el("p", `county-panel-message ${state.kind}`, MESSAGES[state.kind]));
      return;
    }

    const { data } = state;
    header.append(el("span", "county-panel-time", `最新觀測：${formatObservationTime(data.latest_observation_time)}`));
    const stats = el("div", "county-stats");
    stats.append(
      stat("測站數", `${data.station_count} 站`),
      stat("平均氣溫", formatValue(data.summary.avg_temperature, "°C", "")),
      stat("平均相對濕度", formatValue(data.summary.avg_humidity, "%", "")),
      stat("平均風速", formatValue(data.summary.avg_wind_speed, "m/s")),
    );
    container.replaceChildren(header, stats, stationTable(data));
  };

  const setState = (next: CountyWeatherState): void => {
    state = next;
    render();
  };

  const show = async (countyName: string): Promise<void> => {
    const county = normalizeCountyName(countyName);

    // Cancel the previous request; the request ID also guards against any
    // response that still resolves after a newer county was selected.
    controller?.abort();
    const current = new AbortController();
    controller = current;
    const id = ++requestId;

    setState({ kind: "loading", county });
    try {
      const data = await api.countyWeather(county, current.signal);
      if (id !== requestId) return;
      setState(data.station_count > 0 ? { kind: "loaded", county, data } : { kind: "empty", county });
    } catch (err) {
      if (id !== requestId) return; // superseded or aborted
      const noData = err instanceof ApiError && err.status === 404;
      setState({ kind: noData ? "empty" : "error", county });
    }
  };

  render();
  return { show, getState: () => state };
}
