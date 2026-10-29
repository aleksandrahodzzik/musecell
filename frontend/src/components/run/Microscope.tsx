// The microscope (spec §8): true-black panes, channel toggles, masks, same-plate DMSO side by side,
// shared zoom/pan, and the measured values of the cell under the pointer.

import { Maximize2, Minus, Plus } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import {
  CHANNEL_LABEL, CHANNELS, compose, highlight, labelAt, loadPixels, loadSegmentation, outlines,
  type Channel, type FieldPixels, type FieldSegmentation,
} from "../../lib/fieldImage";
import { uM } from "../../lib/format";
import type { AcquiredWell } from "../../lib/runState";
import type { CellRow } from "../../lib/types";

interface View {
  s: number;
  tx: number;
  ty: number;
}
const FIT: View = { s: 1, tx: 0, ty: 0 };
const MAX_ZOOM = 8;

export const SWATCH: Record<Channel, string> = { dna: "bg-dna", tubulin: "bg-tubulin", actin: "bg-actin" };

export default function Microscope({ well }: { well?: AcquiredWell }) {
  const [fieldIdx, setFieldIdx] = useState(0);
  const [enabled, setEnabled] = useState<Record<Channel, boolean>>({ dna: true, tubulin: true, actin: true });
  const [masks, setMasks] = useState(false);
  const [compare, setCompare] = useState(true);
  const [view, setView] = useState<View>(FIT);
  const [caps, setCaps] = useState({ channels: true, seg: true });

  useEffect(() => {
    setFieldIdx(0);
    setView(FIT);
  }, [well?.well_id]);

  if (!well) {
    return (
      <div className="h-full flex flex-col on-dark">
        <Toolbar disabled enabled={enabled} setEnabled={setEnabled} masks={masks} setMasks={setMasks}
                 compare={compare} setCompare={setCompare} setView={setView} caps={caps} />
        <div className="flex-1 bg-darkfield flex items-center justify-center p-6">
          <p className="text-sm text-white/70 max-w-[38ch] text-center">
            No wells imaged yet. Each well appears here as soon as the agent acquires it.
          </p>
        </div>
      </div>
    );
  }

  const fid = well.field_ids[fieldIdx] ?? well.field_ids[0];
  const dmso = well.dmso_field_ids.length ? well.dmso_field_ids[fieldIdx % well.dmso_field_ids.length] : undefined;

  return (
    <div className="h-full flex flex-col min-h-0 on-dark">
      <Toolbar enabled={enabled} setEnabled={setEnabled} masks={masks} setMasks={setMasks} compare={compare}
               setCompare={setCompare} setView={setView} caps={caps} hasDmso={!!dmso} />
      <div className="flex items-center gap-2 px-3 h-8 border-b border-line text-xs bg-night-2">
        <span className="font-medium text-white">Well {well.order}</span>
        <span className="text-mist">dose {well.concentration_index + 1} · {uM(well.concentration_uM)} · replicate {well.replicate}</span>
        <span className="ml-auto text-mist">Field</span>
        <div role="group" aria-label="Field" className="flex gap-1">
          {well.field_ids.map((f, i) => (
            <button key={f} aria-pressed={i === fieldIdx} onClick={() => setFieldIdx(i)}
                    className={`h-6 w-6 rounded-control text-xs border ${i === fieldIdx ? "bg-haze text-night border-haze" : "border-line bg-night-3 text-haze hover:border-white/30"}`}>
              {i + 1}
            </button>
          ))}
        </div>
      </div>
      <div className={`flex-1 min-h-0 grid gap-px bg-night-3 ${compare && dmso ? "grid-cols-2" : "grid-cols-1"}`}>
        <Pane fieldId={fid} title={`Treated · field ${fieldIdx + 1} of ${well.field_ids.length}`} enabled={enabled}
              masks={masks} view={view} setView={setView} onCaps={setCaps} />
        {compare && dmso && (
          <Pane fieldId={dmso} title="DMSO control · same plate" enabled={enabled} masks={masks} view={view} setView={setView} />
        )}
      </div>
      {(!caps.seg || !caps.channels) && (
        <p className="px-3 py-1 text-[11px] text-mist bg-night-2 border-t border-line">
          {!caps.channels && "Channels are split from the 8-bit composite for this field (per-channel images are not on this server). "}
          {!caps.seg && "Masks and per-cell measurements need the BBBC021 segmentation cache on this machine."}
        </p>
      )}
    </div>
  );
}

function Toolbar(props: {
  enabled: Record<Channel, boolean>;
  setEnabled: (f: (e: Record<Channel, boolean>) => Record<Channel, boolean>) => void;
  masks: boolean;
  setMasks: (f: (m: boolean) => boolean) => void;
  compare: boolean;
  setCompare: (f: (m: boolean) => boolean) => void;
  setView: (f: View | ((v: View) => View)) => void;
  caps: { channels: boolean; seg: boolean };
  hasDmso?: boolean;
  disabled?: boolean;
}) {
  const { enabled, setEnabled, masks, setMasks, compare, setCompare, setView, caps, disabled } = props;
  const toggle = "h-7 px-2 rounded-control text-xs border inline-flex items-center gap-1.5 transition-colors disabled:opacity-40";
  const on = "bg-haze text-night border-haze";
  const off = "bg-night-3 text-haze border-line hover:border-white/30";
  const zoomBy = (k: number) => setView((v) => clampView({ s: v.s * k, tx: v.tx * k, ty: v.ty * k }));
  return (
    <div className="flex items-center gap-1.5 px-3 h-10 border-b border-line bg-night-2">
      <h2 className="text-sm font-medium mr-2 text-white">Microscope</h2>
      <div role="group" aria-label="Channels" className="flex gap-1">
        {CHANNELS.map((ch) => (
          <button key={ch} className={`${toggle} ${enabled[ch] ? on : off}`} aria-pressed={enabled[ch]}
                  disabled={disabled}
                  onClick={() => setEnabled((e) => ({ ...e, [ch]: !e[ch] }))}>
            <span aria-hidden className={`h-2.5 w-2.5 rounded-full ${SWATCH[ch]} ${enabled[ch] ? "" : "opacity-40"}`} />
            {CHANNEL_LABEL[ch]}
          </button>
        ))}
      </div>
      <button className={`${toggle} ${masks ? on : off}`} aria-pressed={masks} disabled={disabled || !caps.seg}
              onClick={() => setMasks((m) => !m)}>
        Masks
      </button>
      <button className={`${toggle} ${compare ? on : off}`} aria-pressed={compare} disabled={disabled || !props.hasDmso}
              onClick={() => setCompare((c) => !c)}>
        Same-plate DMSO
      </button>
      <div role="group" aria-label="Zoom" className="ml-auto flex gap-1">
        <button className={`${toggle} ${off} w-7 justify-center px-0`} aria-label="Zoom out" disabled={disabled} onClick={() => zoomBy(1 / 1.5)}>
          <Minus aria-hidden className="h-3.5 w-3.5" />
        </button>
        <button className={`${toggle} ${off} w-7 justify-center px-0`} aria-label="Zoom in" disabled={disabled} onClick={() => zoomBy(1.5)}>
          <Plus aria-hidden className="h-3.5 w-3.5" />
        </button>
        <button className={`${toggle} ${off} w-7 justify-center px-0`} aria-label="Fit image" disabled={disabled} onClick={() => setView(FIT)}>
          <Maximize2 aria-hidden className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}

function clampView(v: View): View {
  const s = Math.min(MAX_ZOOM, Math.max(1, v.s));
  return s === 1 ? FIT : { ...v, s };
}

function Pane(props: {
  fieldId: string;
  title: string;
  enabled: Record<Channel, boolean>;
  masks: boolean;
  view: View;
  setView: (f: View | ((v: View) => View)) => void;
  onCaps?: (c: { channels: boolean; seg: boolean }) => void;
}) {
  const { fieldId, title, enabled, masks, view, setView, onCaps } = props;
  const box = useRef<HTMLDivElement>(null);
  const imgCanvas = useRef<HTMLCanvasElement>(null);
  const maskCanvas = useRef<HTMLCanvasElement>(null);
  const hiCanvas = useRef<HTMLCanvasElement>(null);
  const [px, setPx] = useState<FieldPixels | null>(null);
  const [seg, setSeg] = useState<FieldSegmentation | null | undefined>(undefined);
  const [failed, setFailed] = useState(false);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [hover, setHover] = useState<{ cell: CellRow; x: number; y: number } | null>(null);
  const drag = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);

  useEffect(() => {
    let live = true;
    setPx(null);
    setSeg(undefined);
    setFailed(false);
    setHover(null);
    loadPixels(fieldId).then((p) => live && setPx(p)).catch(() => live && setFailed(true));
    loadSegmentation(fieldId).then((s) => live && setSeg(s));
    return () => {
      live = false;
    };
  }, [fieldId]);

  useEffect(() => {
    if (onCaps && px && seg !== undefined) onCaps({ channels: !px.fromComposite, seg: !!seg });
  }, [px, seg, onCaps]);

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setSize({ w: e.contentRect.width, h: e.contentRect.height }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const c = imgCanvas.current;
    if (!c || !px) return;
    c.width = px.width;
    c.height = px.height;
    c.getContext("2d")!.putImageData(compose(px, enabled), 0, 0);
  }, [px, enabled]);

  useEffect(() => {
    const c = maskCanvas.current;
    if (!c || !seg) return;
    c.width = seg.lw;
    c.height = seg.lh;
    c.getContext("2d")!.putImageData(outlines(seg), 0, 0);
  }, [seg]);

  useEffect(() => {
    const c = hiCanvas.current;
    if (!c || !seg) return;
    c.width = seg.lw;
    c.height = seg.lh;
    const ctx = c.getContext("2d")!;
    if (hover) ctx.putImageData(highlight(seg, hover.cell.label), 0, 0);
    else ctx.clearRect(0, 0, c.width, c.height);
  }, [seg, hover?.cell.label]); // eslint-disable-line react-hooks/exhaustive-deps

  // "contain" fit of the image in the pane, then the shared zoom/pan on top
  const aspect = px ? px.width / px.height : 1.25;
  const bw = Math.min(size.w, size.h * aspect);
  const bh = bw / aspect;
  const ox = (size.w - bw) / 2;
  const oy = (size.h - bh) / 2;

  const toImage = useCallback((clientX: number, clientY: number) => {
    const r = box.current!.getBoundingClientRect();
    const x = clientX - r.left, y = clientY - r.top;
    return { x, y, u: (x - ox - view.tx) / (bw * view.s), v: (y - oy - view.ty) / (bh * view.s) };
  }, [ox, oy, bw, bh, view]);

  const onWheel = (e: globalThis.WheelEvent) => {
    e.preventDefault();
    const r = box.current!.getBoundingClientRect();
    const px0 = e.clientX - r.left - ox, py0 = e.clientY - r.top - oy;
    const k = Math.exp(-e.deltaY * 0.0015);
    setView((v) => {
      const s = Math.min(MAX_ZOOM, Math.max(1, v.s * k));
      const f = s / v.s;
      return clampView({ s, tx: px0 - (px0 - v.tx) * f, ty: py0 - (py0 - v.ty) * f });
    });
  };

  // React's wheel listeners are passive; zooming must not also scroll the page, so listen natively.
  const wheelRef = useRef(onWheel);
  wheelRef.current = onWheel;
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const h = (e: globalThis.WheelEvent) => wheelRef.current(e);
    el.addEventListener("wheel", h, { passive: false });
    return () => el.removeEventListener("wheel", h);
  }, []);

  const onPointerDown = (e: PointerEvent) => {
    if (view.s === 1) return;
    drag.current = { x: e.clientX, y: e.clientY, tx: view.tx, ty: view.ty };
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: PointerEvent) => {
    if (drag.current) {
      const d = drag.current;
      setView((v) => ({ ...v, tx: d.tx + e.clientX - d.x, ty: d.ty + e.clientY - d.y }));
      return;
    }
    if (!seg) return;
    const p = toImage(e.clientX, e.clientY);
    const label = labelAt(seg, p.u, p.v);
    const cell = label ? seg.byLabel.get(label) : undefined;
    setHover(cell ? { cell, x: p.x, y: p.y } : null);
  };
  const onPointerUp = () => {
    drag.current = null;
  };

  const onKey = (e: KeyboardEvent) => {
    const step = 40;
    const keys: Record<string, (v: View) => View> = {
      "+": (v) => clampView({ s: v.s * 1.5, tx: v.tx * 1.5, ty: v.ty * 1.5 }),
      "=": (v) => clampView({ s: v.s * 1.5, tx: v.tx * 1.5, ty: v.ty * 1.5 }),
      "-": (v) => clampView({ s: v.s / 1.5, tx: v.tx / 1.5, ty: v.ty / 1.5 }),
      "0": () => FIT,
      ArrowLeft: (v) => ({ ...v, tx: v.tx + step }),
      ArrowRight: (v) => ({ ...v, tx: v.tx - step }),
      ArrowUp: (v) => ({ ...v, ty: v.ty + step }),
      ArrowDown: (v) => ({ ...v, ty: v.ty - step }),
    };
    const f = keys[e.key];
    if (f) {
      e.preventDefault();
      setView((v) => (v.s === 1 && e.key.startsWith("Arrow") ? v : f(v)));
    }
  };

  const layer = "absolute inset-0 w-full h-full";
  return (
    <div className="relative bg-darkfield min-h-0 min-w-0 overflow-hidden on-dark">
      <div
        ref={box}
        tabIndex={0}
        role="img"
        aria-label={`${title}. Scroll or press plus and minus to zoom, drag or use arrow keys to pan, 0 to fit.`}
        className={`absolute inset-0 overflow-hidden touch-none ${view.s > 1 ? "cursor-grab active:cursor-grabbing" : "cursor-crosshair"}`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={() => {
          drag.current = null;
          setHover(null);
        }}
        onKeyDown={onKey}
      >
        <div
          className="absolute origin-top-left"
          style={{ left: ox, top: oy, width: bw, height: bh, transform: `translate(${view.tx}px, ${view.ty}px) scale(${view.s})` }}
        >
          <canvas ref={imgCanvas} className={layer} style={{ imageRendering: view.s >= 3 ? "pixelated" : "auto" }} />
          <canvas ref={maskCanvas} className={`${layer} ${masks && seg ? "" : "hidden"}`} />
          <canvas ref={hiCanvas} className={layer} />
        </div>
      </div>
      <span className="pointer-events-none absolute left-2 top-2 px-1.5 py-0.5 text-[11px] text-white/90 bg-black/60">{title}</span>
      {!px && !failed && <span className="absolute inset-0 flex items-center justify-center text-xs text-white/60">Loading field…</span>}
      {failed && (
        <span className="absolute inset-0 flex items-center justify-center p-4 text-center text-xs text-white/70">
          This field's images are not on this server.
        </span>
      )}
      {hover && <CellCard cell={hover.cell} x={hover.x} y={hover.y} w={size.w} h={size.h} />}
    </div>
  );
}

const FLAG_LABEL: Record<string, string> = {
  mitotic_like: "Mitotic-like",
  large_nucleus: "Large nucleus",
  multinucleate_proxy: "Clumped nuclei",
};

function CellCard({ cell, x, y, w, h }: { cell: CellRow; x: number; y: number; w: number; h: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [cardH, setCardH] = useState(200);
  useLayoutEffect(() => setCardH(ref.current?.offsetHeight ?? 200), [cell.label]);
  const cardW = 196;
  const preferred = x + 14 + cardW > w ? x - 14 - cardW : x + 14;
  const left = Math.min(Math.max(4, preferred), Math.max(4, w - cardW - 4)); // stay inside the pane
  const top = Math.min(Math.max(4, y - 20), Math.max(4, h - cardH - 4));
  const rows: [string, string, Channel?][] = [
    ["Nucleus area", `${cell.nuc_area.toFixed(0)} px`],
    ["DNA content", cell.nuc_dna_integrated.toFixed(0), "dna"],
    ["DNA intensity", cell.nuc_dna_mean.toFixed(3), "dna"],
    ["Cell area", `${cell.cell_area.toFixed(0)} px`],
    ["Roundness", cell.cell_roundness.toFixed(2)],
    ["Tubulin intensity", cell.tub_mean.toFixed(3), "tubulin"],
    ["Actin intensity", cell.act_mean.toFixed(3), "actin"],
  ];
  return (
    <div ref={ref} className="pointer-events-none absolute z-10 w-[196px] rounded-control bg-sheet text-ink shadow-lg border border-rule p-2 text-[11px]"
         style={{ left, top }} role="status">
      <p className="font-medium text-xs mb-1">Cell {cell.label}</p>
      <dl className="grid grid-cols-[1fr_auto] gap-x-2 gap-y-0.5">
        {rows.map(([k, v, ch]) => (
          <div key={k} className="contents">
            <dt className="text-graphite flex items-center gap-1">
              {ch && <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${SWATCH[ch]}`} />}
              {k}
            </dt>
            <dd className="text-right">{v}</dd>
          </div>
        ))}
      </dl>
      {(cell.flags.length > 0 || cell.touches_border) && (
        <p className="mt-1 text-graphite">
          {[...cell.flags.map((f) => FLAG_LABEL[f] ?? f), cell.touches_border ? "Touches the border (not in medians)" : ""]
            .filter(Boolean).join(" · ")}
        </p>
      )}
      <p className="mt-1 text-graphite">Intensities relative to this plate's DMSO range.</p>
    </div>
  );
}
