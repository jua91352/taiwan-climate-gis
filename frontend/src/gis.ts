import L from "leaflet";
import type { Feature, FeatureCollection, MultiPolygon, Polygon } from "geojson";
import countiesGeoJson from "./data/taiwan-counties.geojson?raw";

export interface CountyProperties {
  COUNTYNAME: string;
  COUNTYENG: string;
  COUNTYCODE: string;
}

type CountyFeature = Feature<Polygon | MultiPolygon, CountyProperties>;

export interface CityCenter {
  lat: number;
  lng: number;
  zoom: number;
}

// Map view for each county/city when clicked. Centers are based on the main
// landmass so remote islands (e.g. 東沙/南沙 for 高雄市, 釣魚臺 for 宜蘭縣,
// 烏坵 for 金門縣) do not pull the view away. Do not replace with fitBounds().
export const CITY_CENTERS: Record<string, CityCenter> = {
  "基隆市": { lat: 25.1147, lng: 121.7175, zoom: 12 },
  "臺北市": { lat: 25.0854, lng: 121.5616, zoom: 11 },
  "新北市": { lat: 24.987, lng: 121.6448, zoom: 10 },
  "桃園市": { lat: 24.8553, lng: 121.231, zoom: 10 },
  "新竹市": { lat: 24.7837, lng: 120.9538, zoom: 12 },
  "新竹縣": { lat: 24.687, lng: 121.1687, zoom: 10 },
  "苗栗縣": { lat: 24.5148, lng: 120.9422, zoom: 10 },
  "臺中市": { lat: 24.2202, lng: 120.9543, zoom: 10 },
  "彰化縣": { lat: 23.9966, lng: 120.4521, zoom: 10 },
  "南投縣": { lat: 23.8412, lng: 120.9829, zoom: 9 },
  "雲林縣": { lat: 23.6854, lng: 120.4252, zoom: 10 },
  "嘉義市": { lat: 23.4789, lng: 120.4493, zoom: 12 },
  "嘉義縣": { lat: 23.4257, lng: 120.5378, zoom: 10 },
  "臺南市": { lat: 23.1515, lng: 120.3416, zoom: 10 },
  "高雄市": { lat: 22.971, lng: 120.6118, zoom: 9 },
  "屏東縣": { lat: 22.3913, lng: 120.6635, zoom: 9 },
  "宜蘭縣": { lat: 24.65, lng: 121.6419, zoom: 9 },
  "花蓮縣": { lat: 23.7358, lng: 121.3803, zoom: 9 },
  "臺東縣": { lat: 22.838, lng: 121.1176, zoom: 9 },
  "澎湖縣": { lat: 23.51, lng: 119.5043, zoom: 10 },
  "金門縣": { lat: 24.4563, lng: 118.3144, zoom: 11 },
  "連江縣": { lat: 26.1625, lng: 120.2101, zoom: 10 },
};

const DEFAULT_STYLE: L.PathOptions = {
  color: "#2563eb",
  weight: 1.2,
  opacity: 0.9,
  fillColor: "#3b82f6",
  fillOpacity: 0.06,
};

const HOVER_STYLE: L.PathOptions = { ...DEFAULT_STYLE, weight: 3, fillOpacity: 0.15 };

const SELECTED_STYLE: L.PathOptions = {
  color: "#c2410c",
  weight: 3.5,
  opacity: 1,
  fillColor: "#f97316",
  fillOpacity: 0.3,
};

const SELECTED_HOVER_STYLE: L.PathOptions = { ...SELECTED_STYLE, weight: 4.5 };

export function getCountyName(feature: CountyFeature): string {
  return feature.properties.COUNTYNAME;
}

function loadCounties(): FeatureCollection<Polygon | MultiPolygon, CountyProperties> {
  const data: FeatureCollection<Polygon | MultiPolygon, CountyProperties> = JSON.parse(countiesGeoJson);
  for (const feature of data.features) {
    const name = getCountyName(feature);
    if (!CITY_CENTERS[name]) {
      console.warn(`No CITY_CENTERS entry for county: ${name}`);
    }
  }
  return data;
}

export interface CountyLayer {
  layer: L.GeoJSON;
  selectCounty(name: string): void;
  getSelectedCounty(): string | null;
}

export interface CountyLayerOptions {
  /** Called after a county is selected and the map has moved to it. */
  onSelect?: (name: string) => void;
}

export function createCountyLayer(map: L.Map, options: CountyLayerOptions = {}): CountyLayer {
  const pathsByName = new Map<string, L.Path>();
  let selectedName: string | null = null;

  const styleFor = (name: string, hovered: boolean): L.PathOptions => {
    if (name === selectedName) return hovered ? SELECTED_HOVER_STYLE : SELECTED_STYLE;
    return hovered ? HOVER_STYLE : DEFAULT_STYLE;
  };

  const selectCounty = (name: string): void => {
    const path = pathsByName.get(name);
    if (!path) return;

    const previous = selectedName;
    selectedName = name;
    if (previous && previous !== name) {
      pathsByName.get(previous)?.setStyle(styleFor(previous, false));
    }
    path.setStyle(styleFor(name, false));
    path.bringToFront();

    const center = CITY_CENTERS[name];
    if (center) {
      map.setView([center.lat, center.lng], center.zoom);
    }
    options.onSelect?.(name);
  };

  const layer = L.geoJSON<CountyProperties, Polygon | MultiPolygon>(loadCounties(), {
    style: DEFAULT_STYLE,
    onEachFeature: (feature, featureLayer) => {
      if (!(featureLayer instanceof L.Path)) return;
      const name = getCountyName(feature);
      pathsByName.set(name, featureLayer);

      featureLayer.bindTooltip(name, { sticky: true, direction: "top", className: "county-tooltip" });
      featureLayer.on({
        mouseover: () => featureLayer.setStyle(styleFor(name, true)),
        mouseout: () => featureLayer.setStyle(styleFor(name, false)),
        click: () => selectCounty(name),
      });
    },
  });

  return {
    layer,
    selectCounty,
    getSelectedCounty: () => selectedName,
  };
}
