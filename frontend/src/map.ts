import L from "leaflet";
import iconUrl from "leaflet/dist/images/marker-icon.png";
import iconRetinaUrl from "leaflet/dist/images/marker-icon-2x.png";
import shadowUrl from "leaflet/dist/images/marker-shadow.png";
import type { BaseMapOption } from "./mapControls";

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
const OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const ESRI_DARK_BASE_URL = `${ESRI_CANVAS_URL}/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`;
const ESRI_DARK_REFERENCE_URL = `${ESRI_CANVAS_URL}/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}`;
const ESRI_IMAGERY_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";

// The single zoom-6 tile that contains all of Taiwan, used for base-map previews.
const PREVIEW_TILE = { z: 6, x: 53, y: 27 };
const previewTile = (template: string): string =>
  L.Util.template(template, { ...PREVIEW_TILE, s: "a" });

function createBaseLayers(): BaseMapOption[] {
  const standard = L.tileLayer(OSM_TILE_URL, {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  });

  // Esri Dark Gray Canvas: base + label reference layer, no API key required.
  const dark = L.layerGroup([
    L.tileLayer(ESRI_DARK_BASE_URL, {
      maxZoom: 19,
      maxNativeZoom: 16,
      attribution: "Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap contributors, and the GIS user community",
    }),
    L.tileLayer(ESRI_DARK_REFERENCE_URL, {
      maxZoom: 19,
      maxNativeZoom: 16,
    }),
  ]);

  const satellite = L.tileLayer(ESRI_IMAGERY_URL, {
    maxZoom: 19,
    attribution: "Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community",
  });

  // Previews are real tiles from each layer's own provider (reference labels on top).
  return [
    { label: "標準地圖", layer: standard, previewTiles: [previewTile(OSM_TILE_URL)] },
    { label: "衛星地圖", layer: satellite, previewTiles: [previewTile(ESRI_IMAGERY_URL)] },
    { label: "暗色地圖", layer: dark, previewTiles: [previewTile(ESRI_DARK_REFERENCE_URL), previewTile(ESRI_DARK_BASE_URL)] },
  ];
}

export interface MapContext {
  map: L.Map;
  /** Base maps in panel order; the first one is on initially. */
  baseMaps: BaseMapOption[];
}

export function createMap(container: HTMLElement): MapContext {
  // The header card floats over the top-left of the full-screen map, so the
  // zoom buttons move to the bottom-right corner.
  const map = L.map(container, { zoomControl: false }).setView(TAIWAN_CENTER, TAIWAN_ZOOM);
  L.control.zoom({ position: "bottomright" }).addTo(map);

  const baseMaps = createBaseLayers();
  baseMaps[0].layer.addTo(map);

  // Leaflet only re-measures on window resize. Re-measure whenever the map
  // container itself resizes too; invalidateSize() keeps the current center
  // (e.g. a CITY_CENTERS view).
  new ResizeObserver(() => map.invalidateSize()).observe(container);

  return { map, baseMaps };
}
