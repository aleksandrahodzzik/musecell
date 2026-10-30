// Field images for the microscope: per-channel grayscale PNGs composed on a canvas with the stain colours
// (spec §8: DNA, tubulin, actin each have one fixed colour), and the segmentation label maps used for mask
// outlines and hover hit-testing. Everything is cached per field id.

import { imageUrl } from "./api";
import type { CellRow, CellTable } from "./types";

export type Channel = "dna" | "tubulin" | "actin";
export const CHANNELS: Channel[] = ["dna", "tubulin", "actin"];
export const CHANNEL_LABEL: Record<Channel, string> = { dna: "DNA", tubulin: "Tubulin", actin: "Actin" };
export const CHANNEL_RGB: Record<Channel, [number, number, number]> = {
  dna: [0x5b, 0x8c, 0xff],
  tubulin: [0x3f, 0xcf, 0x7f],
  actin: [0xff, 0x54, 0x68],
};

export interface FieldPixels {
  width: number;
  height: number;
  channels: Record<Channel, Uint8ClampedArray>;
  fromComposite: boolean; // channel PNGs missing (some recorded runs): split from the composite instead
}

/** The composite PNGs are DNA→blue, tubulin→green, actin→red (scripts/preprocess.py). */
const COMPOSITE_BYTE: Record<Channel, number> = { actin: 0, tubulin: 1, dna: 2 };

export interface FieldSegmentation {
  table: CellTable;
  byLabel: Map<number, CellRow>;
  lw: number;
  lh: number;
  cells: Uint16Array;
  nuclei: Uint16Array;
}

const pixelCache = new Map<string, Promise<FieldPixels>>();
const segCache = new Map<string, Promise<FieldSegmentation | null>>();

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.decoding = "async";
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`image ${src} not available`));
    img.src = src;
  });
}

function pixelsOf(img: HTMLImageElement): ImageData {
  const c = document.createElement("canvas");
  c.width = img.naturalWidth;
  c.height = img.naturalHeight;
  const ctx = c.getContext("2d", { willReadFrequently: true })!;
  ctx.drawImage(img, 0, 0);
  return ctx.getImageData(0, 0, c.width, c.height);
}

// Without the BBBC021 data (replay-only demo) only composites are shipped: skip the channel requests (no 404s).
let compositeOnly = false;
export const setCompositeOnly = (v: boolean) => { compositeOnly = v; };

async function fetchPixels(fieldId: string): Promise<FieldPixels> {
  try {
    if (compositeOnly) throw new Error("composites only");
    const imgs = await Promise.all(CHANNELS.map((ch) => loadImage(imageUrl(`${fieldId}.${ch}`))));
    const data = imgs.map(pixelsOf);
    const channels = {} as Record<Channel, Uint8ClampedArray>;
    CHANNELS.forEach((ch, i) => (channels[ch] = byteOf(data[i], 0)));
    return { width: data[0].width, height: data[0].height, channels, fromComposite: false };
  } catch {
    compositeOnly = true; // one miss means this server ships composites only: do not ask for channels again
    const composite = pixelsOf(await loadImage(imageUrl(`${fieldId}.composite`)));
    const channels = {} as Record<Channel, Uint8ClampedArray>;
    CHANNELS.forEach((ch) => (channels[ch] = byteOf(composite, COMPOSITE_BYTE[ch])));
    return { width: composite.width, height: composite.height, channels, fromComposite: true };
  }
}

function byteOf(img: ImageData, offset: number): Uint8ClampedArray {
  const out = new Uint8ClampedArray(img.width * img.height);
  for (let p = 0, q = offset; p < out.length; p++, q += 4) out[p] = img.data[q];
  return out;
}

export function loadPixels(fieldId: string): Promise<FieldPixels> {
  let p = pixelCache.get(fieldId);
  if (!p) {
    p = fetchPixels(fieldId);
    p.catch(() => pixelCache.delete(fieldId));
    pixelCache.set(fieldId, p);
  }
  return p;
}

/** Additive composite of the enabled channels, each tinted with its stain colour. */
export function compose(px: FieldPixels, enabled: Record<Channel, boolean>): ImageData {
  const out = new ImageData(px.width, px.height);
  const o = out.data;
  const active = CHANNELS.filter((c) => enabled[c]);
  for (let p = 0, q = 0; p < px.width * px.height; p++, q += 4) {
    let r = 0, g = 0, b = 0;
    for (const ch of active) {
      const v = px.channels[ch][p] / 255;
      const [cr, cg, cb] = CHANNEL_RGB[ch];
      r += v * cr;
      g += v * cg;
      b += v * cb;
    }
    o[q] = r;
    o[q + 1] = g;
    o[q + 2] = b;
    o[q + 3] = 255;
  }
  return out;
}

/**
 * feColorMatrix values that re-tint a composite PNG (R = actin, G = tubulin, B = DNA) with the stain colours,
 * so thumbnails match the microscope's canvas composite exactly. Used by <StainTintFilter/>.
 */
export const STAIN_MATRIX = (() => {
  const n = (c: number) => +(c / 255).toFixed(3);
  const [a, t, d] = [CHANNEL_RGB.actin, CHANNEL_RGB.tubulin, CHANNEL_RGB.dna];
  return [0, 1, 2].map((k) => `${n(a[k])} ${n(t[k])} ${n(d[k])} 0 0`).join("  ") + "  0 0 0 1 0";
})();

async function fetchSegmentation(fieldId: string): Promise<FieldSegmentation | null> {
  if (compositeOnly) return null; // masks need the BBBC021 data, which a replay-only server does not have
  const [tRes, lRes] = await Promise.all([fetch(`/api/fields/${fieldId}/cells`), fetch(`/api/fields/${fieldId}/labels`)]);
  if (!tRes.ok || !lRes.ok) return null;
  const table = (await tRes.json()) as CellTable;
  const lw = Number(lRes.headers.get("X-Label-Width"));
  const lh = Number(lRes.headers.get("X-Label-Height"));
  const buf = await lRes.arrayBuffer(); // the browser has already undone Content-Encoding: gzip
  const all = new Uint16Array(buf);
  if (!lw || !lh || all.length !== 2 * lw * lh) return null;
  return {
    table, lw, lh, byLabel: new Map(table.cells.map((c) => [c.label, c])),
    cells: all.subarray(0, lw * lh), nuclei: all.subarray(lw * lh),
  };
}

export function loadSegmentation(fieldId: string): Promise<FieldSegmentation | null> {
  let p = segCache.get(fieldId);
  if (!p) {
    p = fetchSegmentation(fieldId).catch(() => null);
    segCache.set(fieldId, p);
  }
  return p;
}

/** Outlines of cells (white) and nuclei (light grey), at label-map resolution. Neutral colours: masks are not stains. */
export function outlines(seg: FieldSegmentation): ImageData {
  const { lw, lh, cells, nuclei } = seg;
  const out = new ImageData(lw, lh);
  const o = out.data;
  const edge = (m: Uint16Array, i: number, x: number, y: number) => {
    const v = m[i];
    if (!v) return false;
    return (x > 0 && m[i - 1] !== v) || (x < lw - 1 && m[i + 1] !== v) || (y > 0 && m[i - lw] !== v) || (y < lh - 1 && m[i + lw] !== v);
  };
  for (let y = 0; y < lh; y++) {
    for (let x = 0; x < lw; x++) {
      const i = y * lw + x;
      const q = i * 4;
      if (edge(cells, i, x, y)) {
        o[q] = o[q + 1] = o[q + 2] = 255;
        o[q + 3] = 200;
      } else if (edge(nuclei, i, x, y)) {
        o[q] = o[q + 1] = o[q + 2] = 200;
        o[q + 3] = 150;
      }
    }
  }
  return out;
}

/** The hovered cell, filled translucent white. */
export function highlight(seg: FieldSegmentation, label: number): ImageData {
  const out = new ImageData(seg.lw, seg.lh);
  const o = out.data;
  for (let i = 0; i < seg.cells.length; i++) {
    if (seg.cells[i] === label) {
      o[i * 4] = o[i * 4 + 1] = o[i * 4 + 2] = 255;
      o[i * 4 + 3] = 70;
    }
  }
  return out;
}

export function labelAt(seg: FieldSegmentation, u: number, v: number): number {
  if (u < 0 || v < 0 || u >= 1 || v >= 1) return 0;
  return seg.cells[Math.floor(v * seg.lh) * seg.lw + Math.floor(u * seg.lw)];
}
