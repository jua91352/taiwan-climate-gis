import "leaflet/dist/leaflet.css";
import "./style.css";
import { createMap } from "./map";
import { createCountyLayer } from "./gis";

const container = document.getElementById("map");
if (!container) {
  throw new Error("Map container #map not found");
}

const { map, layersControl } = createMap(container);

const counties = createCountyLayer(map);
counties.layer.addTo(map);
layersControl.addOverlay(counties.layer, "縣市邊界");
