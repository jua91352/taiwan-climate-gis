import L from "leaflet";

const LOCATION_PANE = "location";
const LOCATE_MAX_ZOOM = 15;
const MESSAGE_MS = 5000;

// Friendly text per GeolocationPositionError code (0 = not supported, from Leaflet).
const ERROR_MESSAGES: Record<number, string> = {
  1: "無法取得您的位置，請允許瀏覽器使用定位功能。",
  2: "目前無法取得位置，請稍後再試。",
  3: "定位逾時，請再試一次。",
};
const FALLBACK_MESSAGE = "此瀏覽器無法使用定位功能。";

function locationIcon(): L.DivIcon {
  return L.divIcon({
    className: "my-location-icon",
    html: '<span class="my-location-dot"></span>',
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  });
}

/**
 * 定位我的位置: browser geolocation via Leaflet's map.locate(). Coordinates stay
 * in this closure only — never sent anywhere, stored, or logged. Permission is
 * requested only when the returned function is called (a user click).
 * The returned promise always resolves: true when located, false on error
 * (errors become a short message).
 */
export function createLocator(map: L.Map, messageHost: HTMLElement): () => Promise<boolean> {
  if (!map.getPane(LOCATION_PANE)) {
    // Above temperature (460), below tooltips/popups; non-interactive so clicks
    // still reach county polygons and station dots.
    map.createPane(LOCATION_PANE).style.zIndex = "470";
  }

  let marker: L.Marker | null = null;
  let accuracyCircle: L.Circle | null = null;

  const message = document.createElement("p");
  message.className = "locate-message";
  message.setAttribute("role", "status");
  message.hidden = true;
  messageHost.append(message);
  let hideTimer: number | undefined;
  const showMessage = (text: string): void => {
    message.textContent = text;
    message.hidden = false;
    window.clearTimeout(hideTimer);
    hideTimer = window.setTimeout(() => {
      message.hidden = true;
    }, MESSAGE_MS);
  };

  const show = (e: L.LocationEvent): void => {
    message.hidden = true;
    if (marker) {
      marker.setLatLng(e.latlng);
    } else {
      marker = L.marker(e.latlng, {
        icon: locationIcon(),
        pane: LOCATION_PANE,
        interactive: false,
        keyboard: false,
      })
        .bindTooltip("我的位置", { permanent: true, direction: "top", offset: [0, -12], className: "my-location-label" })
        .addTo(map);
    }

    // Only draw the browser's own accuracy radius, and only when it is usable.
    const hasAccuracy = Number.isFinite(e.accuracy) && e.accuracy > 0;
    if (!hasAccuracy) {
      accuracyCircle?.remove();
      accuracyCircle = null;
    } else if (accuracyCircle) {
      accuracyCircle.setLatLng(e.latlng).setRadius(e.accuracy);
    } else {
      accuracyCircle = L.circle(e.latlng, {
        radius: e.accuracy,
        pane: LOCATION_PANE,
        interactive: false,
        color: "#6d4aff",
        weight: 1,
        opacity: 0.6,
        fillColor: "#6d4aff",
        fillOpacity: 0.1,
      }).addTo(map);
    }
  };

  return () =>
    new Promise<boolean>((resolve) => {
      const finish = (found: boolean): void => {
        map.off("locationfound", onFound);
        map.off("locationerror", onError);
        resolve(found);
      };
      const onFound = (e: L.LocationEvent): void => {
        show(e);
        finish(true);
      };
      const onError = (e: L.ErrorEvent): void => {
        showMessage(ERROR_MESSAGES[e.code] ?? FALLBACK_MESSAGE);
        finish(false);
      };
      map.on("locationfound", onFound);
      map.on("locationerror", onError);
      // setView fits the accuracy area, capped at a street-level zoom.
      map.locate({ setView: true, maxZoom: LOCATE_MAX_ZOOM, enableHighAccuracy: true, timeout: 10000 });
    });
}
