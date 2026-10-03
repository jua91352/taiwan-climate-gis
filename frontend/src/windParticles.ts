import L from "leaflet";
import { isOverTaiwan, type RgbColor } from "./heatmapSurface";

/** One valid station as a wind vector (m/s) pointing where the wind goes. */
export interface WindVectorSample {
  lat: number;
  lng: number;
  /** East component (m/s). */
  u: number;
  /** North component (m/s). */
  v: number;
}

/**
 * CWA WindDirection is where the wind comes FROM (clockwise from north,
 * 360 ≡ 0). Particles move where it goes: direction + 180°.
 */
export function windVector(speed: number, directionFrom: number): { u: number; v: number } {
  const toward = ((((directionFrom % 360) + 180) % 360) * Math.PI) / 180;
  return { u: speed * Math.sin(toward), v: speed * Math.cos(toward) };
}

// ---------------------------------------------------------------------------
// Geographic vector field: IDW (power 2) of the station u/v components on a
// ~4 km lat/lng grid over Taiwan; sampled bilinearly between nodes.

const FIELD = { west: 118.1, east: 122.1, south: 21.85, north: 26.45, step: 0.04 };
const KM_PER_DEG_LAT = 110.57;
const KM_PER_DEG_LNG = 111.32 * Math.cos((23.7 * Math.PI) / 180);

interface VectorField {
  cols: number;
  rows: number;
  u: Float32Array;
  v: Float32Array;
}

function buildField(samples: WindVectorSample[]): VectorField | null {
  if (samples.length === 0) return null;
  const cols = Math.round((FIELD.east - FIELD.west) / FIELD.step) + 1;
  const rows = Math.round((FIELD.north - FIELD.south) / FIELD.step) + 1;
  const u = new Float32Array(cols * rows);
  const v = new Float32Array(cols * rows);
  const n = samples.length;
  const sx = Float64Array.from(samples, (s) => s.lng * KM_PER_DEG_LNG);
  const sy = Float64Array.from(samples, (s) => s.lat * KM_PER_DEG_LAT);
  for (let r = 0; r < rows; r++) {
    const cy = (FIELD.north - r * FIELD.step) * KM_PER_DEG_LAT;
    for (let c = 0; c < cols; c++) {
      const cx = (FIELD.west + c * FIELD.step) * KM_PER_DEG_LNG;
      let w = 0;
      let su = 0;
      let sv = 0;
      let hit = -1;
      for (let i = 0; i < n; i++) {
        const dx = sx[i] - cx;
        const dy = sy[i] - cy;
        const d2 = dx * dx + dy * dy;
        if (d2 < 1e-6) {
          hit = i;
          break;
        }
        const wi = 1 / d2;
        w += wi;
        su += wi * samples[i].u;
        sv += wi * samples[i].v;
      }
      const k = r * cols + c;
      u[k] = hit >= 0 ? samples[hit].u : su / w;
      v[k] = hit >= 0 ? samples[hit].v : sv / w;
    }
  }
  return { cols, rows, u, v };
}

function sampleField(f: VectorField, lat: number, lng: number): [number, number] | null {
  const fc = (lng - FIELD.west) / FIELD.step;
  const fr = (FIELD.north - lat) / FIELD.step;
  const c0 = Math.floor(fc);
  const r0 = Math.floor(fr);
  if (c0 < 0 || r0 < 0 || c0 + 1 >= f.cols || r0 + 1 >= f.rows) return null;
  const tx = fc - c0;
  const ty = fr - r0;
  const k = r0 * f.cols + c0;
  const lerp2 = (a: Float32Array): number =>
    (a[k] * (1 - tx) + a[k + 1] * tx) * (1 - ty) + (a[k + f.cols] * (1 - tx) + a[k + f.cols + 1] * tx) * ty;
  return [lerp2(f.u), lerp2(f.v)];
}

// ---------------------------------------------------------------------------
// Animation tuning. Screen speed is proportional to the local wind speed.

const PANE = "wind-particles";
const GRID_STEP = 5; // px between screen samples of the field
const PX_PER_FRAME_PER_MS = 0.8; // 1 m/s -> 0.8 px per 60 Hz frame (48 px/s)
const DEFAULT_MAX_PARTICLES = 3000;
const MIN_PARTICLES = 300;
const SPEED_BUCKET = 0.5; // m/s per color bucket (batched strokes)
const BUCKETS = 30;

interface ZoomStyle {
  /** Particles per px² of land on screen. */
  density: number;
  lineWidth: number;
  /** Fraction of the previous frame kept: longer trails when closer to 1. */
  fade: number;
  alpha: number;
}

function zoomStyle(zoom: number, detailZoom: number): ZoomStyle {
  // From detailZoom up the station arrows carry the detail; particles thin out.
  return zoom >= detailZoom
    ? { density: 1 / 120, lineWidth: 1.3, fade: 0.94, alpha: 0.75 }
    : { density: 1 / 60, lineWidth: 1.6, fade: 0.96, alpha: 0.9 };
}

interface ScreenGrid {
  cols: number;
  rows: number;
  du: Float32Array;
  dv: Float32Array;
  speed: Float32Array;
  ok: Uint8Array;
  valid: Int32Array;
}

let runningLoops = 0;
/** Number of live animation loops across all particle layers (for diagnostics). */
export function activeWindLoops(): number {
  return runningLoops;
}

export interface WindParticleOptions {
  colorAt: (ms: number) => RgbColor;
  /** Upper bound; lowered automatically if frames get slow. */
  maxParticles?: number;
  /** Zoom from which particles thin out under the station arrows. */
  detailZoom: number;
}

/**
 * Animated particles advected through the interpolated station wind field.
 * One canvas, one requestAnimationFrame loop; stopped and released on remove.
 * The canvas is redrawn from scratch after every pan/zoom/resize.
 */
export class WindParticleLayer extends L.Layer {
  private readonly opts: WindParticleOptions;
  private field: VectorField | null = null;
  private canvas: HTMLCanvasElement | null = null;
  private ctx: CanvasRenderingContext2D | null = null;
  private frame: number | null = null;
  private screen: ScreenGrid | null = null;
  private px = new Float32Array(0);
  private py = new Float32Array(0);
  private age = new Float32Array(0);
  private life = new Float32Array(0);
  private count = 0;
  private cap: number;
  private style: ZoomStyle = zoomStyle(0, 99);
  private cssW = 0;
  private cssH = 0;
  private lastTime = 0;
  private frameMs = 16.7;
  private framesSinceCheck = 0;
  private bucketColors: string[] = [];
  private segs: number[][] = Array.from({ length: BUCKETS }, () => []);

  constructor(opts: WindParticleOptions) {
    super();
    this.opts = opts;
    this.cap = opts.maxParticles ?? DEFAULT_MAX_PARTICLES;
  }

  /** Rebuild the vector field from real station vectors (no requests). */
  setData(samples: WindVectorSample[]): void {
    this.field = buildField(samples.filter((s) => Number.isFinite(s.u) && Number.isFinite(s.v)));
    if (this._map) this.reset();
  }

  stats(): { particles: number; cap: number; frameMs: number; running: boolean } {
    return { particles: this.count, cap: this.cap, frameMs: this.frameMs, running: this.frame !== null };
  }

  // The loop is never paused for a zoom: Leaflet hides the canvas during the
  // zoom animation (.leaflet-zoom-hide) and the view change re-fits it, so a
  // zoom without a following moveend can never leave the animation stopped.
  getEvents(): Record<string, L.LeafletEventHandlerFn> {
    return { zoomend: this.scheduleReset, moveend: this.scheduleReset, resize: this.scheduleReset };
  }

  onAdd(map: L.Map): this {
    if (!map.getPane(PANE)) map.createPane(PANE).style.zIndex = "455"; // under the arrows (460)
    const canvas = L.DomUtil.create("canvas", "wind-particles leaflet-zoom-hide");
    canvas.setAttribute("aria-hidden", "true");
    map.getPane(PANE)?.appendChild(canvas);
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.reset();
    return this;
  }

  onRemove(): this {
    this.stop();
    if (this.pendingReset !== null) cancelAnimationFrame(this.pendingReset);
    this.pendingReset = null;
    this.canvas?.remove();
    this.canvas = null;
    this.ctx = null;
    this.screen = null;
    this.px = this.py = this.age = this.life = new Float32Array(0);
    this.count = 0;
    return this;
  }

  private pendingReset: number | null = null;

  /** Coalesce zoomend + moveend (+ resize) of one view change into a single reset. */
  private scheduleReset = (): void => {
    if (this.pendingReset !== null) return;
    this.pendingReset = requestAnimationFrame(() => {
      this.pendingReset = null;
      this.reset();
    });
  };

  private stop = (): void => {
    if (this.frame !== null) {
      cancelAnimationFrame(this.frame);
      this.frame = null;
      runningLoops--;
    }
  };

  private start(): void {
    if (this.frame !== null || !this.ctx || this.count === 0) return;
    runningLoops++;
    this.lastTime = performance.now();
    this.frame = requestAnimationFrame(this.tick);
  }

  /** Re-fit the canvas to the view, resample the field, respawn particles. */
  private reset = (): void => {
    this.stop();
    const map = this._map;
    const canvas = this.canvas;
    const ctx = this.ctx;
    if (!map || !canvas || !ctx) return;

    const size = map.getSize();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.cssW = size.x;
    this.cssH = size.y;
    canvas.width = Math.round(size.x * dpr);
    canvas.height = Math.round(size.y * dpr);
    canvas.style.width = `${size.x}px`;
    canvas.style.height = `${size.y}px`;
    L.DomUtil.setPosition(canvas, map.containerPointToLayerPoint([0, 0]));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size.x, size.y);

    this.style = zoomStyle(map.getZoom(), this.opts.detailZoom);
    this.bucketColors = Array.from({ length: BUCKETS }, (_, i) => {
      const [r, g, b] = this.opts.colorAt((i + 0.5) * SPEED_BUCKET);
      return `rgba(${r}, ${g}, ${b}, ${this.style.alpha})`;
    });
    this.screen = this.field ? this.buildScreen(map) : null;
    this.spawnAll();
    this.start();
  };

  private buildScreen(map: L.Map): ScreenGrid {
    const field = this.field as VectorField;
    const cols = Math.ceil(this.cssW / GRID_STEP) + 2;
    const rows = Math.ceil(this.cssH / GRID_STEP) + 2;
    const du = new Float32Array(cols * rows);
    const dv = new Float32Array(cols * rows);
    const speed = new Float32Array(cols * rows);
    const ok = new Uint8Array(cols * rows);
    const valid: number[] = [];
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const ll = map.containerPointToLatLng([c * GRID_STEP, r * GRID_STEP]);
        if (!isOverTaiwan(ll.lat, ll.lng)) continue;
        const uv = sampleField(field, ll.lat, ll.lng);
        if (!uv) continue;
        const k = r * cols + c;
        // North-up map: screen x follows u, screen y is the inverse of v.
        du[k] = uv[0] * PX_PER_FRAME_PER_MS;
        dv[k] = -uv[1] * PX_PER_FRAME_PER_MS;
        speed[k] = Math.hypot(uv[0], uv[1]);
        ok[k] = 1;
        valid.push(k);
      }
    }
    return { cols, rows, du, dv, speed, ok, valid: Int32Array.from(valid) };
  }

  private spawnAll(): void {
    const screen = this.screen;
    if (!screen || screen.valid.length === 0) {
      this.count = 0; // no land in view: nothing to animate
      if (this.canvas) this.canvas.dataset.particles = "0";
      return;
    }
    const landArea = screen.valid.length * GRID_STEP * GRID_STEP;
    this.count = Math.max(Math.min(MIN_PARTICLES, this.cap), Math.min(this.cap, Math.round(landArea * this.style.density)));
    this.px = new Float32Array(this.count);
    this.py = new Float32Array(this.count);
    this.age = new Float32Array(this.count);
    this.life = new Float32Array(this.count);
    for (let i = 0; i < this.count; i++) {
      this.spawn(i);
      this.age[i] = Math.random() * this.life[i]; // stagger so they don't respawn together
    }
    if (this.canvas) this.canvas.dataset.particles = String(this.count);
  }

  /** Respawn at a random land position (position only; motion comes from the field). */
  private spawn(i: number): void {
    const screen = this.screen as ScreenGrid;
    const k = screen.valid[Math.floor(Math.random() * screen.valid.length)];
    this.px[i] = (k % screen.cols) * GRID_STEP + (Math.random() - 0.5) * GRID_STEP;
    this.py[i] = Math.floor(k / screen.cols) * GRID_STEP + (Math.random() - 0.5) * GRID_STEP;
    this.age[i] = 0;
    this.life[i] = 40 + Math.random() * 60;
  }

  private tick = (now: number): void => {
    const ctx = this.ctx;
    const screen = this.screen;
    if (!ctx || !screen) {
      this.stop();
      return;
    }
    const dt = Math.min(now - this.lastTime, 100);
    this.lastTime = now;
    this.frameMs = this.frameMs * 0.95 + dt * 0.05;
    const scale = Math.min(dt / 16.7, 3);

    // Fade the previous frame so each particle leaves a short trail.
    ctx.globalCompositeOperation = "destination-in";
    ctx.fillStyle = `rgba(0, 0, 0, ${this.style.fade})`;
    ctx.fillRect(0, 0, this.cssW, this.cssH);
    ctx.globalCompositeOperation = "source-over";

    const { cols, du, dv, speed, ok } = screen;
    for (const s of this.segs) s.length = 0;
    for (let i = 0; i < this.count; i++) {
      const x = this.px[i];
      const y = this.py[i];
      const fx = x / GRID_STEP;
      const fy = y / GRID_STEP;
      const c = Math.floor(fx);
      const r = Math.floor(fy);
      const k = r * cols + c;
      this.age[i] += scale;
      if (c < 0 || r < 0 || c + 1 >= cols || k + cols + 1 >= ok.length || !ok[k] || !ok[k + 1] || !ok[k + cols] || !ok[k + cols + 1] || this.age[i] > this.life[i]) {
        this.spawn(i);
        continue;
      }
      const tx = fx - c;
      const ty = fy - r;
      const bl = (a: Float32Array): number =>
        (a[k] * (1 - tx) + a[k + 1] * tx) * (1 - ty) + (a[k + cols] * (1 - tx) + a[k + cols + 1] * tx) * ty;
      const nx = x + bl(du) * scale;
      const ny = y + bl(dv) * scale;
      const bucket = Math.min(BUCKETS - 1, Math.floor(bl(speed) / SPEED_BUCKET));
      this.segs[bucket].push(x, y, nx, ny);
      this.px[i] = nx;
      this.py[i] = ny;
    }

    ctx.lineWidth = this.style.lineWidth;
    ctx.lineCap = "round";
    this.segs.forEach((seg, b) => {
      if (seg.length === 0) return;
      ctx.strokeStyle = this.bucketColors[b];
      ctx.beginPath();
      for (let j = 0; j < seg.length; j += 4) {
        ctx.moveTo(seg[j], seg[j + 1]);
        ctx.lineTo(seg[j + 2], seg[j + 3]);
      }
      ctx.stroke();
    });

    // Adapt: if frames stay slow, drop a fifth of the particles (not below MIN).
    if (++this.framesSinceCheck >= 90) {
      this.framesSinceCheck = 0;
      if (this.frameMs > 24 && this.count > MIN_PARTICLES) {
        this.cap = Math.max(MIN_PARTICLES, Math.floor(this.count * 0.8));
        this.count = this.cap;
        if (this.canvas) this.canvas.dataset.particles = String(this.count);
      }
    }
    this.frame = requestAnimationFrame(this.tick);
  };
}
