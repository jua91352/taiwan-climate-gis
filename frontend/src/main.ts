import "leaflet/dist/leaflet.css";
import "./style.css";
import { createMap } from "./map";

const container = document.getElementById("map");
if (!container) {
  throw new Error("Map container #map not found");
}

createMap(container);
