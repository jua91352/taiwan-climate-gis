import L from "leaflet";
import type { FeatureCollection, MultiPolygon, Polygon, Position } from "geojson";
import countiesGeoJson from "./data/taiwan-counties.geojson?raw";

/** A real observation: station position + a valid measured value. */
export interface HeatmapSample {
  lat: number;
  lng: number;
  value: number;
}

export type RgbColor = readonly [number, number, number];
/** Color for an interpolated value; null leaves that cell transparent. */
export type ColorScale = (value: number) => RgbColor | null;

// Shared by every weather surface; main layers are exclusive, so one shows at a time.
const SURFACE_PANE = "weather-surface";

// Raster extent: main island, 澎湖, 金門, 馬祖, 綠島, 蘭嶼. The remote 東沙/南沙
// polygons of 高雄市 lie outside it and are simply not painted.
const EXTENT = { west: 118.1, east: 122.1, south: 21.85, north: 26.45 };
// ~1 km grid cells (0.01° of longitude, square in Web Mercator).
const CELL_METERS = 1113.2;
// The field is computed per cell, upscaled with bilinear smoothing, then
// clipped to the county polygons at this finer scale (~250 m coastline).
const UPSCALE = 4;
// Classic IDW over every valid station, weight = 1/d² (power 2). A distance
// cutoff was tried and made flat plateaus with hard edges in sparse mountain
// areas; the global form is smooth everywhere and exact at each station.
const OPACITY = 0.6;

const crs = L.CRS.EPSG3857;
const counties = JSON.parse(countiesGeoJson) as FeatureCollection<Polygon | MultiPolygon>;

function polygonsOf(fc: FeatureCollection<Polygon | MultiPolygon>): Position[][][] {
  return fc.features.flatMap((f) =>
    f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.coordinates,
  );
}
const POLYGONS = polygonsOf(counties);

interface Grid {
  cols: number;
  rows: number;
  /** Web Mercator meters of the grid's north-west corner. */
  x0: number;
  y0: number;
  bounds: L.LatLngBounds;
}

function makeGrid(): Grid {
  const nw = crs.project(L.latLng(EXTENT.north, EXTENT.west));
  const se = crs.project(L.latLng(EXTENT.south, EXTENT.east));
  const cols = Math.ceil((se.x - nw.x) / CELL_METERS);
  const rows = Math.ceil((nw.y - se.y) / CELL_METERS);
  const south = crs.unproject(L.point(nw.x, nw.y - rows * CELL_METERS)).lat;
  const east = crs.unproject(L.point(nw.x + cols * CELL_METERS, nw.y)).lng;
  return { cols, rows, x0: nw.x, y0: nw.y, bounds: L.latLngBounds([south, EXTENT.west], [EXTENT.north, east]) };
}

/** Trace every county ring onto ctx, in grid-cell units × scale. */
function tracePolygons(ctx: CanvasRenderingContext2D, grid: Grid, scale: number): void {
  ctx.beginPath();
  for (const polygon of POLYGONS) {
    for (const ring of polygon) {
      ring.forEach(([lng, lat], i) => {
        const p = crs.project(L.latLng(lat, lng));
        const x = ((p.x - grid.x0) / CELL_METERS) * scale;
        const y = ((grid.y0 - p.y) / CELL_METERS) * scale;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.closePath();
    }
  }
}

function canvas2d(width: number, height: number): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas 2D is not available");
  return [canvas, ctx];
}

/** Cells to compute: land plus a 2-cell coastal margin, so smoothing never blends with empty cells. */
function landMask(grid: Grid): Uint8ClampedArray {
  const [, ctx] = canvas2d(grid.cols, grid.rows);
  tracePolygons(ctx, grid, 1);
  ctx.fill("evenodd");
  ctx.lineWidth = 4;
  ctx.stroke();
  return ctx.getImageData(0, 0, grid.cols, grid.rows).data;
}

let sharedLand: { grid: Grid; mask: Uint8ClampedArray } | null = null;

/** True over Taiwan's counties (plus the ~2 km coastal margin of the heatmap mask). */
export function isOverTaiwan(lat: number, lng: number): boolean {
  if (!sharedLand) {
    const grid = makeGrid();
    sharedLand = { grid, mask: landMask(grid) };
  }
  const { grid, mask } = sharedLand;
  const p = crs.project(L.latLng(lat, lng));
  const col = Math.floor((p.x - grid.x0) / CELL_METERS);
  const row = Math.floor((grid.y0 - p.y) / CELL_METERS);
  if (col < 0 || row < 0 || col >= grid.cols || row >= grid.rows) return false;
  return mask[(row * grid.cols + col) * 4 + 3] > 0;
}

// Local equirectangular km around Taiwan, accurate enough for weighting.
const KM_PER_DEG_LAT = 110.57;
const KM_PER_DEG_LNG = 111.32 * Math.cos((23.7 * Math.PI) / 180);

function interpolate(grid: Grid, mask: Uint8ClampedArray, samples: HeatmapSample[], colorAt: ColorScale): ImageData {
  const n = samples.length;
  const sx = new Float64Array(n);
  const sy = new Float64Array(n);
  const st = new Float64Array(n);
  samples.forEach((s, i) => {
    sx[i] = s.lng * KM_PER_DEG_LNG;
    sy[i] = s.lat * KM_PER_DEG_LAT;
    st[i] = s.value;
  });

  const image = new ImageData(grid.cols, grid.rows);
  const px = image.data;
  for (let row = 0; row < grid.rows; row++) {
    const lat = crs.unproject(L.point(grid.x0, grid.y0 - (row + 0.5) * CELL_METERS)).lat;
    const cy = lat * KM_PER_DEG_LAT;
    for (let col = 0; col < grid.cols; col++) {
      const idx = (row * grid.cols + col) * 4;
      if (mask[idx + 3] === 0) continue;
      const lng = EXTENT.west + ((col + 0.5) * CELL_METERS * 180) / (Math.PI * 6378137);
      const cx = lng * KM_PER_DEG_LNG;

      let wSum = 0;
      let tSum = 0;
      let exact = Number.NaN;
      for (let i = 0; i < n; i++) {
        const dx = sx[i] - cx;
        const dy = sy[i] - cy;
        const d2 = dx * dx + dy * dy;
        if (d2 < 1e-6) {
          exact = st[i];
          break;
        }
        const w = 1 / d2;
        wSum += w;
        tSum += w * st[i];
      }
      const t = Number.isFinite(exact) ? exact : tSum / wSum;
      const color = colorAt(t);
      if (!color) continue;
      const [r, g, b] = color;
      px[idx] = r;
      px[idx + 1] = g;
      px[idx + 2] = b;
      px[idx + 3] = 255;
    }
  }
  return image;
}

export interface HeatmapSurface {
  layer: L.ImageOverlay;
  /** Rebuild the surface from the current valid observations (local only, no requests). */
  update(samples: HeatmapSample[]): void;
}

/**
 * Continuous weather surface: IDW of real station values on a fixed ~1 km Web
 * Mercator grid, clipped to the county GeoJSON. Rendered once per data update
 * into one image overlay that Leaflet scales on zoom/pan, so map movement
 * never recomputes or adds layers. `className` tags the overlay image.
 */
export function createHeatmapSurface(map: L.Map, colorAt: ColorScale, className: string): HeatmapSurface {
  if (!map.getPane(SURFACE_PANE)) {
    // Above base tiles (200), below county lines (overlayPane 400) and markers.
    map.createPane(SURFACE_PANE).style.zIndex = "350";
  }
  const grid = makeGrid();
  const transparentPixel = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==";
  const layer = L.imageOverlay(transparentPixel, grid.bounds, {
    pane: SURFACE_PANE,
    opacity: OPACITY,
    interactive: false,
    className,
  });
  let mask: Uint8ClampedArray | null = null;
  let objectUrl: string | null = null;
  let version = 0;

  const update = (samples: HeatmapSample[]): void => {
    if (samples.length === 0) {
      version++; // drop any render still encoding
      layer.setUrl(transparentPixel);
      return;
    }
    mask ??= landMask(grid);
    const field = interpolate(grid, mask, samples, colorAt);

    const [small, smallCtx] = canvas2d(grid.cols, grid.rows);
    smallCtx.putImageData(field, 0, 0);
    const [out, ctx] = canvas2d(grid.cols * UPSCALE, grid.rows * UPSCALE);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(small, 0, 0, out.width, out.height);
    ctx.globalCompositeOperation = "destination-in";
    tracePolygons(ctx, grid, UPSCALE);
    ctx.fill("evenodd");

    const current = ++version;
    out.toBlob((blob) => {
      if (!blob || current !== version) return;
      const previous = objectUrl;
      objectUrl = URL.createObjectURL(blob);
      layer.setUrl(objectUrl);
      if (previous) URL.revokeObjectURL(previous);
    }, "image/png");
  };

  return { layer, update };
}
