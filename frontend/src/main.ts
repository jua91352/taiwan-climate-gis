import "leaflet/dist/leaflet.css";
import "./style.css";
import { createMap } from "./map";
import { createCountyLayer } from "./gis";
import { api } from "./api";
import { createStationLayer, formatObservationTime, renderStations } from "./stations";
import { createCountyWeatherPanel } from "./countyWeather";
import { createHistoryChart } from "./historyChart";
import { createTemperatureLayer } from "./temperatureLayer";
import { createMapControls, layerToggle, type Toggle } from "./mapControls";
import { createLocator } from "./geolocation";
import { createRainfallLayer } from "./rainfallHeatmap";
import { createSourceStatus, type MainLayerId } from "./dataSource";

const container = document.getElementById("map");
const detailsContainer = document.getElementById("county-details");
const panelContainer = document.getElementById("county-weather");
const historyContainer = document.getElementById("county-history");
if (!container || !detailsContainer || !panelContainer || !historyContainer) {
  throw new Error("Map container #map, #county-details, #county-weather or #county-history not found");
}

const { map, baseMaps } = createMap(container);

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
// On narrow screens the sheet lifts the legend up the right edge; fold the
// map controls panel away so it does not cover it (the user can reopen it).
const setDetailsOpen = (open: boolean): void => {
  detailsContainer.hidden = !open;
  if (open && narrowScreen.matches) mapControls.setCollapsed(true);
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

// Station markers are off by default; 測站點位 is one of the exclusive main layers.
const stationLayer = createStationLayer(map);

// Header "資料來源" line follows whichever main layer is on (see dataSource.ts).
const sourceStatus = createSourceStatus(document.getElementById("data-status"));

// Weather layer, on by default; uses the same /api/weather/latest data.
const temperature = createTemperatureLayer(map);
temperature.layer.addTo(map);

// 雨量: past-1-hour rainfall surface from /api/rainfall/latest, off by default.
const rainfall = createRainfallLayer(map, (detail) => sourceStatus.setDetail("rainfall", detail));

// 氣溫數字標籤: show/hide the numbers drawn by the temperature layer (CSS only).
const temperatureLabels: Toggle = {
  isOn: () => !container.classList.contains("temp-labels-hidden"),
  set: (on) => container.classList.toggle("temp-labels-hidden", !on),
};

// 定位我的位置 (browser only). On narrow screens the located marker sits under
// the expanded panel, so fold it away after a successful fix.
const locateMe = createLocator(map, container.parentElement ?? document.body);

// Right-side panel. The 8 main weather layers are mutually exclusive (at most one
// on); items without a toggle are shown as 即將提供 (later batches).
const mainLayer = (id: MainLayerId) => id;
const mapControls = createMapControls(map, {
  layers: [
    { id: mainLayer("temperature"), label: "氣溫", icon: "temperature", toggle: layerToggle(map, temperature.layer) },
    { id: mainLayer("rainfall"), label: "雨量", icon: "rain", toggle: layerToggle(map, rainfall.layer) },
    { id: mainLayer("radar"), label: "雷達", icon: "radar" },
    { id: mainLayer("typhoon"), label: "颱風", icon: "typhoon" },
    { id: mainLayer("wind"), label: "風速風向", icon: "wind" },
    { id: mainLayer("humidity"), label: "濕度", icon: "humidity" },
    { id: mainLayer("weather"), label: "天氣", icon: "weather" },
    { id: mainLayer("stations"), label: "測站點位", icon: "station", toggle: layerToggle(map, stationLayer) },
  ],
  onMainLayerChange: (id) => sourceStatus.setActive(id),
  options: [
    { label: "縣市界線", icon: "boundary", toggle: layerToggle(map, counties.layer) },
    // Only meaningful with the 氣溫 layer on; the preference survives layer switches.
    { label: "氣溫數字標籤", icon: "label", toggle: temperatureLabels, available: () => map.hasLayer(temperature.layer) },
  ],
  baseMaps,
  locate: async () => {
    if ((await locateMe()) && narrowScreen.matches) mapControls.setCollapsed(true);
  },
  collapsed: window.matchMedia("(max-width: 600px)").matches,
});
container.after(mapControls.element);

// 氣溫 and 測站點位 share this data, so both get the same header detail.
async function loadLatestWeather(): Promise<void> {
  const setDetail = (text: string): void => {
    sourceStatus.setDetail("temperature", text);
    sourceStatus.setDetail("stations", text);
  };

  try {
    const latest = await api.latestWeather();
    const drawn = renderStations(map, stationLayer, latest.data);
    temperature.render(latest.data);
    if (drawn === 0) {
      setDetail("目前沒有可顯示的測站資料");
      return;
    }
    const time = latest.latest_observation_time
      ? formatObservationTime(latest.latest_observation_time)
      : "尚無觀測資料";
    setDetail(`最新觀測：${time} | 測站 ${drawn} 站`);
  } catch {
    setDetail("目前無法取得後端氣象資料");
  }
}

void loadLatestWeather();
