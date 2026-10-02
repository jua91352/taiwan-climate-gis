import "leaflet/dist/leaflet.css";
import "./style.css";
import { createMap } from "./map";
import { createCountyLayer } from "./gis";
import { api } from "./api";

const container = document.getElementById("map");
if (!container) {
  throw new Error("Map container #map not found");
}

const { map, layersControl } = createMap(container);

const counties = createCountyLayer(map);
counties.layer.addTo(map);
layersControl.addOverlay(counties.layer, "縣市邊界");

async function showDataStatus(): Promise<void> {
  const status = document.getElementById("data-status");
  if (!status) return;
  try {
    const latest = await api.latestWeather();
    const time = latest.latest_observation_time
      ? new Date(latest.latest_observation_time).toLocaleString("zh-TW", { hour12: false })
      : "尚無觀測資料";
    status.textContent = `${latest.source} | 最新觀測：${time} | 測站 ${latest.count} 站`;
  } catch {
    status.textContent = "目前無法取得後端氣象資料";
  }
}

void showDataStatus();
