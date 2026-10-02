import "leaflet/dist/leaflet.css";
import "./style.css";
import { createMap } from "./map";
import { createCountyLayer } from "./gis";
import { api } from "./api";
import { createStationLayer, formatObservationTime, renderStations } from "./stations";

const container = document.getElementById("map");
if (!container) {
  throw new Error("Map container #map not found");
}

const { map, layersControl } = createMap(container);

const counties = createCountyLayer(map);
counties.layer.addTo(map);
layersControl.addOverlay(counties.layer, "縣市邊界");

const stationLayer = createStationLayer(map);
stationLayer.addTo(map);
layersControl.addOverlay(stationLayer, "測站");

async function loadLatestWeather(): Promise<void> {
  const status = document.getElementById("data-status");
  const setStatus = (text: string) => {
    if (status) status.textContent = text;
  };

  try {
    const latest = await api.latestWeather();
    const drawn = renderStations(map, stationLayer, latest.data);
    if (drawn === 0) {
      setStatus(`${latest.source} | 目前沒有可顯示的測站資料`);
      return;
    }
    const time = latest.latest_observation_time
      ? formatObservationTime(latest.latest_observation_time)
      : "尚無觀測資料";
    setStatus(`${latest.source} | 最新觀測：${time} | 測站 ${drawn} 站`);
  } catch {
    setStatus("目前無法取得後端氣象資料");
  }
}

void loadLatestWeather();
