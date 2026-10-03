import type { RadarFrame } from "./api";

// Radar frames span at most this long (the backend keeps 2 hours). Ticks sit at
// their real time on this window, so a missing frame shows as a gap.
const WINDOW_MS = 2 * 60 * 60 * 1000;

const ICON_PREV = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m15 6-6 6 6 6"/></svg>';
const ICON_NEXT = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m9 6 6 6-6 6"/></svg>';
const ICON_PLAY = '<svg class="rt-fill" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M8 5.5v13l10.5-6.5Z"/></svg>';
const ICON_PAUSE = '<svg class="rt-fill" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M7 5.5h3.5v13H7ZM13.5 5.5H17v13h-3.5Z"/></svg>';

/** "HH:MM" in the viewer's time zone, like the header's observation times. */
export function formatFrameTime(timestamp: string): string {
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleTimeString("zh-TW", { hour: "2-digit", minute: "2-digit", hour12: false });
}

export interface RadarTimeline {
  element: HTMLElement;
  setVisible(visible: boolean): void;
  /** Replace the message area (loading / no data / error); hides the axis. */
  setMessage(text: string, isError?: boolean): void;
  /** Draw one tick per frame (frames sorted oldest first) and mark `selected`. */
  setFrames(frames: RadarFrame[], selected: number): void;
  setSelected(index: number): void;
  /** Short state of the shown frame's image, e.g. 載入中… ("" = none). */
  setFrameState(text: string, isError?: boolean): void;
  /** Show the Play button as 播放 (false) or 暫停 (true). */
  setPlaying(playing: boolean): void;
}

export interface RadarTimelineHandlers {
  /** The user picked a frame (tick, 上一個 / 下一個, arrow keys). */
  onSelect(index: number): void;
  /** The user pressed 播放 / 暫停. */
  onTogglePlay(): void;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className: string, html?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  node.className = className;
  if (html !== undefined) node.innerHTML = html;
  return node;
}

/**
 * Bottom-of-map card listing the radar frames on a 2-hour axis. Only the view:
 * it reports user input through `handlers` and never fetches or plays anything.
 */
export function createRadarTimeline(handlers: RadarTimelineHandlers): RadarTimeline {
  const root = el("section", "radar-timeline");
  root.setAttribute("aria-label", "雷達時間軸");
  root.hidden = true;

  const head = el("div", "rt-head");
  const title = el("h2", "rt-title");
  title.textContent = "雷達時間軸";
  const current = el("p", "rt-current");
  current.setAttribute("aria-live", "polite");
  const currentTime = el("strong", "rt-current-time");
  const frameState = el("span", "rt-state");
  current.append("目前 frame：", currentTime, frameState);
  const play = el("button", "rt-play");
  play.type = "button";
  play.addEventListener("click", () => handlers.onTogglePlay());
  head.append(title, current, play);

  const message = el("p", "rt-message");

  const body = el("div", "rt-body");
  const prev = el("button", "rt-step", ICON_PREV);
  prev.type = "button";
  prev.setAttribute("aria-label", "上一個時間");
  const next = el("button", "rt-step", ICON_NEXT);
  next.type = "button";
  next.setAttribute("aria-label", "下一個時間");
  const axis = el("div", "rt-axis");
  const ends = el("div", "rt-ends", '<span>2 小時前</span><span>最新</span>');
  ends.setAttribute("aria-hidden", "true");
  const track = el("div", "rt-track");
  track.setAttribute("role", "radiogroup");
  track.setAttribute("aria-label", "雷達觀測時間");
  axis.append(ends, track);
  body.append(prev, axis, next);
  root.append(head, message, body);

  let ticks: HTMLButtonElement[] = [];
  let labels: string[] = [];
  let selected = -1;

  const choose = (index: number): void => {
    // Re-picking the shown frame still counts as input (it stops playback).
    if (index < 0 || index >= ticks.length) return;
    handlers.onSelect(index);
  };
  prev.addEventListener("click", () => choose(selected - 1));
  next.addEventListener("click", () => choose(selected + 1));
  // Radio group keys: arrows / Home / End move the selection and focus with it.
  track.addEventListener("keydown", (event) => {
    const target = { ArrowLeft: selected - 1, ArrowDown: selected - 1, ArrowRight: selected + 1, ArrowUp: selected + 1, Home: 0, End: ticks.length - 1 }[event.key];
    if (target === undefined) return;
    event.preventDefault();
    choose(Math.max(0, Math.min(ticks.length - 1, target)));
    ticks[selected]?.focus();
  });

  const showMessage = (text: string | null, isError = false): void => {
    message.hidden = text === null;
    message.textContent = text ?? "";
    message.classList.toggle("is-error", isError);
    body.hidden = text !== null;
    current.hidden = text !== null;
    play.hidden = text !== null;
  };

  const setPlaying = (playing: boolean): void => {
    play.innerHTML = `${playing ? ICON_PAUSE : ICON_PLAY}<span>${playing ? "暫停" : "播放"}</span>`;
    play.setAttribute("aria-pressed", String(playing));
  };
  setPlaying(false);

  const timeline: RadarTimeline = {
    element: root,
    setVisible(visible) {
      root.hidden = !visible;
    },
    setMessage(text, isError = false) {
      showMessage(text, isError);
    },
    setFrames(frames, index) {
      showMessage(null);
      const newest = new Date(frames[frames.length - 1].timestamp).getTime();
      labels = frames.map((f) => formatFrameTime(f.timestamp));
      ticks = frames.map((frame, i) => {
        const offset = (newest - new Date(frame.timestamp).getTime()) / WINDOW_MS;
        const tick = el("button", "rt-tick", `<span class="rt-dot"></span><span class="rt-label">${labels[i]}</span>`);
        tick.type = "button";
        tick.setAttribute("role", "radio");
        tick.setAttribute("aria-label", new Date(frame.timestamp).toLocaleString("zh-TW", { hour12: false }));
        tick.style.left = `${(1 - Math.min(Math.max(offset, 0), 1)) * 100}%`;
        tick.addEventListener("click", () => choose(i));
        return tick;
      });
      track.replaceChildren(el("div", "rt-line"), ...ticks);
      selected = -1;
      timeline.setSelected(index);
    },
    setSelected(index) {
      selected = index;
      ticks.forEach((tick, i) => {
        tick.setAttribute("aria-checked", String(i === index));
        tick.tabIndex = i === index ? 0 : -1;
      });
      currentTime.textContent = labels[index] ?? "—";
      prev.disabled = index <= 0;
      next.disabled = index >= ticks.length - 1;
      play.disabled = ticks.length < 2;
    },
    setFrameState(text, isError = false) {
      frameState.textContent = text;
      frameState.classList.toggle("is-error", isError);
    },
    setPlaying,
  };
  return timeline;
}
