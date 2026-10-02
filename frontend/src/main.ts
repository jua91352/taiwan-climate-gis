import "leaflet/dist/leaflet.css";
import "./style.css";
import { createMap } from "./map";
import { createCountyLayer } from "./gis";
import { api } from "./api";
import { createStationLayer, formatObservationTime, renderStations } from "./stations";
import { createCountyWeatherPanel } from "./countyWeather";
import { createHistoryChart } from "./historyChart";
import { createTemperatureLayer } from "./temperatureLayer";

const container = document.getElementById("map");
const detailsContainer = document.getElementById("county-details");
const panelContainer = document.getElementById("county-weather");
const historyContainer = document.getElementById("county-history");
if (!container || !detailsContainer || !panelContainer || !historyContainer) {
  throw new Error("Map container #map, #county-details, #county-weather or #county-history not found");
}

const { map, layersControl } = createMap(container);

// On narrow screens the panels open as a bottom sheet; lift Leaflet's
// bottom-right controls (zoom, legend, attribution) to sit just above it.
const narrowScreen = window.matchMedia("(max-width: 900px)");
const updateSheetOffset = (): void => {
  const offset = !detailsContainer.hidden && narrowScreen.matches ? detailsContainer.offsetHeight + 8 : 0;
  container.style.setProperty("--sheet-offset", `${offset}px`);
};
new ResizeObserver(updateSheetOffset).observe(detailsContainer);
narrowScreen.addEventListener("change", updateSheetOffset);

// The weather + history panels float over the map only while a county is selected.
const setDetailsOpen = (open: boolean): void => {
  detailsContainer.hidden = !open;
  updateSheetOffset();
};
const countyWeather = createCountyWeatherPanel(panelContainer, {
  onClose: () => {
    setDetailsOpen(false);
    counties.clearSelection();
  },
});
const historyChart = createHistoryChart(historyContainer);
const counties = createCountyLayer(map, {
  onSelect: (name) => {
    setDetailsOpen(true);
    void countyWeather.show(name);
    void historyChart.show(name);
  },
});
counties.layer.addTo(map);
layersControl.addOverlay(counties.layer, "縣市邊界");

const stationLayer = createStationLayer(map);
stationLayer.addTo(map);
layersControl.addOverlay(stationLayer, "測站");

// Weather layer, off by default; uses the same /api/weather/latest data.
const temperature = createTemperatureLayer(map);
layersControl.addOverlay(temperature.layer, "氣溫");

async function loadLatestWeather(): Promise<void> {
  const status = document.getElementById("data-status");
  const setStatus = (text: string) => {
    if (status) status.textContent = text;
  };

  try {
    const latest = await api.latestWeather();
    const drawn = renderStations(map, stationLayer, latest.data);
    temperature.render(latest.data);
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
