// Typed client for the Flask REST API. Weather data comes from the backend's
// SQLite database; rainfall is fetched from CWA by the backend (live, cached);
// radar frames are collected and reprojected by the backend.
// The frontend never calls CWA directly.

export interface Station {
  station_id: string;
  station_name: string;
  county_name: string | null;
  town_name: string | null;
  latitude: number | null;
  longitude: number | null;
}

// Measurements are null when CWA reported them as missing/invalid.
export interface Observation {
  observation_time: string;
  temperature: number | null;
  humidity: number | null;
  wind_speed: number | null;
  wind_direction: number | null;
  uv_index: number | null;
  precipitation: number | null;
  /** CWA Weather text (e.g. "晴", "陰有雨"); null when missing. Not in /weather/station. */
  weather?: string | null;
}

export type StationObservation = Station & Observation;

export interface HealthResponse {
  status: string;
  service: string;
}

export interface LatestWeatherResponse {
  source: string;
  latest_observation_time: string | null;
  count: number;
  data: StationObservation[];
  /** The backend fetched a new O-A0003-001 batch from CWA for this request. */
  data_updated?: boolean;
  /** 10+ minutes old because the last CWA refresh failed (shown as not current). */
  data_stale?: boolean;
  refresh_error?: string | null;
}

export interface CountyWeatherResponse {
  source: string;
  county: string;
  latest_observation_time: string | null;
  station_count: number;
  summary: {
    avg_temperature: number | null;
    avg_humidity: number | null;
    avg_wind_speed: number | null;
    temperature_station_count: number;
  };
  data: StationObservation[];
}

export interface CountyHistoryPoint {
  observation_time: string;
  station_count: number;
  temperature_count: number;
  avg_temperature: number | null;
  min_temperature: number | null;
  max_temperature: number | null;
  avg_humidity: number | null;
  avg_wind_speed: number | null;
  avg_precipitation: number | null;
}

// The window is (since, until], where `until` is the newest observation stored
// in SQLite (not the current clock). All three are null when SQLite is empty.
export interface CountyHistoryResponse {
  source: string;
  county: string;
  days: number;
  latest_observation_time: string | null;
  since: string | null;
  until: string | null;
  available_points: number;
  count: number;
  data: CountyHistoryPoint[];
}

export interface StationsResponse {
  count: number;
  data: Station[];
}

export interface StationWeatherResponse {
  source: string;
  station: Station;
  observation: Observation | null;
}

// Past-1-hour rainfall per CWA rain gauge (O-A0002-001). rainfall is null when
// CWA reported trace (T), malfunction (X) or a missing value; see rainfall_status.
export interface RainfallStation {
  station_id: string;
  station_name: string;
  county_name: string | null;
  town_name: string | null;
  latitude: number | null;
  longitude: number | null;
  rainfall: number | null;
  rainfall_status: "ok" | "trace" | "malfunction" | "missing";
  unit: string;
  observation_time: string;
}

export interface RainfallLatestResponse {
  success: boolean;
  source: string;
  unit: string;
  latest_observation_time: string | null;
  count: number;
  valid_count: number;
  data: RainfallStation[];
}

// One stored CWA O-A0058-005 radar frame. image_url is an API path to the PNG,
// already reprojected to Web Mercator by the backend.
export interface RadarFrame {
  timestamp: string;
  image_url: string;
  source: string;
}

// Frames of the last 2 hours, oldest first; empty when none has been collected.
export interface RadarHistoryResponse {
  source: string;
  projection: string;
  /** Leaflet ImageOverlay bounds: [[south, west], [north, east]]. */
  bounds: [[number, number], [number, number]];
  latest_timestamp: string | null;
  count: number;
  frames: RadarFrame[];
  /** Why the backend could not collect a newer frame (null when fine). */
  refresh_error: string | null;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" }, signal });
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const body: { error?: string } = await response.json();
      if (body.error) message = body.error;
    } catch {
      // Non-JSON error body; keep the status message.
    }
    throw new ApiError(message, response.status);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => getJson<HealthResponse>("/api/health"),
  latestWeather: () => getJson<LatestWeatherResponse>("/api/weather/latest"),
  countyWeather: (county: string, signal?: AbortSignal) =>
    getJson<CountyWeatherResponse>(`/api/weather/county/${encodeURIComponent(county)}`, signal),
  countyHistory: (county: string, days: number, signal?: AbortSignal) =>
    getJson<CountyHistoryResponse>(
      `/api/weather/history?${new URLSearchParams({ county, days: String(days) })}`,
      signal,
    ),
  stations: () => getJson<StationsResponse>("/api/stations"),
  stationWeather: (stationId: string) =>
    getJson<StationWeatherResponse>(`/api/weather/station/${encodeURIComponent(stationId)}`),
  rainfallLatest: () => getJson<RainfallLatestResponse>("/api/rainfall/latest"),
  radarHistory: () => getJson<RadarHistoryResponse>("/api/radar/history"),
};
