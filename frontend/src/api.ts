// Typed client for the Flask REST API. All weather data comes from the
// backend's SQLite database; the frontend never calls CWA directly.

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
}

export interface CountyHistoryResponse {
  source: string;
  county: string;
  days: number;
  since: string;
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

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
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
  countyWeather: (county: string) =>
    getJson<CountyWeatherResponse>(`/api/weather/county/${encodeURIComponent(county)}`),
  countyHistory: (county: string, days: number) =>
    getJson<CountyHistoryResponse>(
      `/api/weather/history?${new URLSearchParams({ county, days: String(days) })}`,
    ),
  stations: () => getJson<StationsResponse>("/api/stations"),
  stationWeather: (stationId: string) =>
    getJson<StationWeatherResponse>(`/api/weather/station/${encodeURIComponent(stationId)}`),
};
