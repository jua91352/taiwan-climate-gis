import L from "leaflet";
import iconUrl from "leaflet/dist/images/marker-icon.png";
import iconRetinaUrl from "leaflet/dist/images/marker-icon-2x.png";
import shadowUrl from "leaflet/dist/images/marker-shadow.png";

export const TAIWAN_CENTER: L.LatLngTuple = [23.7, 120.9];
export const TAIWAN_ZOOM = 7;

// Leaflet resolves its default marker images from CSS paths, which break
// under Vite bundling; point the default icon at the bundled assets instead.
L.Marker.prototype.options.icon = L.icon({
  iconUrl,
  iconRetinaUrl,
  shadowUrl,
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  tooltipAnchor: [16, -28],
  shadowSize: [41, 41],
});

const ESRI_CANVAS_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas";

function createBaseLayers(): Record<string, L.Layer> {
  const standard = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  });

  // Esri Dark Gray Canvas: base + label reference layer, no API key required.
  const dark = L.layerGroup([
    L.tileLayer(`${ESRI_CANVAS_URL}/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`, {
      maxZoom: 19,
      maxNativeZoom: 16,
      attribution: "Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap contributors, and the GIS user community",
    }),
    L.tileLayer(`${ESRI_CANVAS_URL}/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}`, {
      maxZoom: 19,
      maxNativeZoom: 16,
    }),
  ]);

  const satellite = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    {
      maxZoom: 19,
      attribution: "Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community",
    },
  );

  return {
    "標準地圖": standard,
    "暗色地圖": dark,
    "衛星地圖": satellite,
  };
}

export interface MapContext {
  map: L.Map;
  layersControl: L.Control.Layers;
}

export function createMap(container: HTMLElement): MapContext {
  const map = L.map(container).setView(TAIWAN_CENTER, TAIWAN_ZOOM);

  const baseLayers = createBaseLayers();
  baseLayers["標準地圖"].addTo(map);

  const layersControl = L.control.layers(baseLayers, {}, { collapsed: false }).addTo(map);

  // Leaflet only re-measures on window resize. The panels below the map change
  // height as county data loads, so re-measure whenever the container resizes;
  // invalidateSize() keeps the current center (e.g. a CITY_CENTERS view).
  new ResizeObserver(() => map.invalidateSize()).observe(container);

  return { map, layersControl };
}
