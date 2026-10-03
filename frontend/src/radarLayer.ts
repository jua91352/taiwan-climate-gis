import L from "leaflet";
import { api, type RadarFrame } from "./api";
import { createRadarTimeline, formatFrameTime } from "./radarTimeline";
import { formatObservationTime } from "./stations";

// Same visual setup as the heatmap surfaces: above base tiles (200), below
// county lines (overlayPane 400) and markers, semi-transparent.
const RADAR_PANE = "radar-surface";
const OPACITY = 0.6;
// 播放: each frame stays this long once its image is on screen.
const FRAME_MS = 1000;

export interface RadarLayer {
  layer: L.LayerGroup;
  /** The 雷達時間軸 card; shown only while the layer is on. */
  timeline: HTMLElement;
}

/**
 * 雷達 main layer: CWA O-A0058-005 frames from /api/radar/history, one image
 * overlay plus the 雷達時間軸. The backend already reprojected every frame to
 * Web Mercator, so images are placed as-is on the bounds the API returns.
 * The history is fetched each time the layer is shown and the newest frame is
 * selected; picking another time only swaps the overlay's image (only that
 * frame's PNG is requested). 播放 steps through the same frames, oldest to
 * newest, and stops at the newest. `onStatus` receives the header detail text.
 */
export function createRadarLayer(map: L.Map, onStatus: (detail: string) => void): RadarLayer {
  if (!map.getPane(RADAR_PANE)) map.createPane(RADAR_PANE).style.zIndex = "350";
  const layer = L.layerGroup();
  let overlay: L.ImageOverlay | null = null;
  let frames: RadarFrame[] = [];
  let selected = -1;
  let stale = "";
  let request = 0;
  let ready = false; // the selected frame's image has loaded
  let lastGood = -1; // newest-shown frame whose image loaded, for falling back
  let errorNote: string | null = null; // shown once the fallback image is back

  // Playback: one pending timeout at most. A step is scheduled only after the
  // shown frame's image has loaded, so every frame is really on screen FRAME_MS.
  let playing = false;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const clearTimer = (): void => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  };
  const stopPlayback = (): void => {
    clearTimer();
    if (!playing) return;
    playing = false;
    timeline.setPlaying(false);
  };
  const scheduleStep = (): void => {
    clearTimer();
    if (playing && ready) timer = setTimeout(step, FRAME_MS);
  };
  function step(): void {
    timer = null;
    if (!playing) return;
    const next = selected + 1;
    if (next < frames.length) select(next);
    // Stop on the newest frame (no looping); otherwise wait for its image.
    if (next >= frames.length - 1) stopPlayback();
    else scheduleStep();
  }
  const togglePlay = (): void => {
    if (playing) {
      stopPlayback();
      return;
    }
    if (frames.length < 2 || !overlay) return;
    playing = true;
    timeline.setPlaying(true);
    // From the selected frame on; on the newest one there is nothing ahead,
    // so play the 2 hours from the oldest frame.
    if (selected >= frames.length - 1) select(0);
    scheduleStep();
  };

  // User input always wins over playback.
  const timeline = createRadarTimeline({
    onSelect: (index) => {
      stopPlayback();
      select(index);
    },
    onTogglePlay: togglePlay,
  });

  const clear = (): void => {
    stopPlayback();
    if (overlay) layer.removeLayer(overlay);
    overlay = null;
    frames = [];
    selected = -1;
    ready = false;
    lastGood = -1;
  };

  const showHeader = (): void => {
    const latest = formatObservationTime(frames[frames.length - 1].timestamp);
    onStatus(
      selected === frames.length - 1
        ? `最新觀測：${latest}${stale}`
        : `顯示時間：${formatObservationTime(frames[selected].timestamp)} | 最新觀測：${latest}${stale}`,
    );
  };

  // Show frames[index]: same overlay, same bounds and opacity, new image.
  // The previous image stays until the new one has loaded (no blank flash).
  function select(index: number): void {
    const frame = frames[index];
    if (!frame || !overlay) return;
    selected = index;
    errorNote = null;
    timeline.setSelected(index);
    showHeader();
    // image_url is an API path like the ones api.ts requests, so it resolves
    // against the same origin (Vite proxies /api to Flask in development).
    if (overlay.getElement()?.getAttribute("src") === frame.image_url) return;
    ready = false;
    timeline.setFrameState("載入中…");
    overlay.setUrl(frame.image_url);
  }

  const createOverlay = (url: string, bounds: L.LatLngBoundsExpression): L.ImageOverlay => {
    const image = L.imageOverlay(url, bounds, {
      pane: RADAR_PANE,
      opacity: OPACITY,
      interactive: false,
      className: "radar-surface",
    });
    // Events fire for whichever image is current; ignore a late one for a frame
    // that is no longer selected.
    const isSelected = (): boolean => image.getElement()?.getAttribute("src") === frames[selected]?.image_url;
    image.on("load", () => {
      if (!isSelected()) return;
      ready = true;
      lastGood = selected;
      timeline.setFrameState(errorNote ?? "", errorNote !== null);
      errorNote = null;
      scheduleStep();
    });
    // A frame whose PNG fails: stop playback and go back to the last frame
    // that did load, saying which time failed.
    image.on("error", () => {
      if (!isSelected()) return;
      stopPlayback();
      const failed = frames[selected];
      console.error(`Radar: image ${failed.image_url} could not be loaded.`);
      const note = `無法載入 ${formatFrameTime(failed.timestamp)} 的雷達圖`;
      if (lastGood >= 0 && lastGood !== selected && frames[lastGood]) {
        select(lastGood);
        errorNote = note;
      } else {
        timeline.setFrameState(note, true);
      }
    });
    return image;
  };

  const load = async (): Promise<void> => {
    const current = ++request;
    stopPlayback();
    onStatus("資料載入中…");
    timeline.setMessage("雷達資料載入中…");
    try {
      const res = await api.radarHistory();
      if (current !== request) return; // a newer load (layer reopened) owns the overlay
      const sorted = [...res.frames].sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
      if (sorted.length === 0) {
        clear();
        console.error("Radar: /api/radar/history returned no frames; no radar image shown.");
        onStatus("暫無雷達資料");
        timeline.setMessage("暫無雷達資料");
        return;
      }
      frames = sorted;
      lastGood = -1;
      // The backend could not get a newer frame from CWA: say the time is not current.
      stale = res.refresh_error ? "（暫時無法更新）" : "";
      if (overlay) {
        overlay.setBounds(L.latLngBounds(res.bounds));
      } else {
        overlay = createOverlay(frames[frames.length - 1].image_url, res.bounds);
        layer.addLayer(overlay);
        timeline.setFrameState("載入中…");
      }
      timeline.setFrames(frames, frames.length - 1);
      select(frames.length - 1);
    } catch (error) {
      if (current !== request) return;
      clear();
      console.error("Radar: /api/radar/history request failed.", error);
      onStatus("目前無法取得雷達資料");
      timeline.setMessage("目前無法取得雷達資料", true);
    }
  };

  layer.on("add", () => {
    timeline.setVisible(true);
    void load();
  });
  layer.on("remove", () => {
    stopPlayback();
    timeline.setVisible(false);
  });

  return { layer, timeline: timeline.element };
}
