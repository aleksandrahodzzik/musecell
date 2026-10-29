// Plate map (spec §8): doses × replicates on a dark plate. An acquired well shows its first field and the order the
// agent imaged it in; empty wells are recessed into the plate.

import { useState } from "react";
import { imageUrl } from "../../lib/api";
import { uM } from "../../lib/format";
import type { AcquiredWell } from "../../lib/runState";
import type { Dose } from "../../lib/types";

interface Props {
  doses: Dose[];
  nDoses: number;
  wells: AcquiredWell[];
  selected?: string;
  onSelect: (wellId: string) => void;
}

export default function PlateMap({ doses, nDoses, wells, selected, onSelect }: Props) {
  // Most doses have 3 replicates; a plate control can have 20+. Show 3 columns (more only if the agent used them).
  const maxReps = Math.max(1, ...doses.map((d) => d.replicates));
  const reps = Math.max(Math.min(maxReps, 3), ...wells.map((w) => w.replicate));
  const rows: { index: number; label: string; replicates: number }[] = doses.length
    ? doses.map((d) => ({ index: d.concentration_index, label: uM(d.concentration_uM), replicates: d.replicates }))
    : Array.from({ length: nDoses }, (_, i) => ({ index: i, label: "", replicates: reps }));
  const at = (i: number, r: number) => wells.find((w) => w.concentration_index === i && w.replicate === r);
  const latest = wells[wells.length - 1]?.well_id;

  return (
    <div className="flex flex-col h-full min-h-0 on-dark">
      <div className="flex items-center gap-2 px-3 h-10 border-b border-line bg-night-2">
        <h2 className="text-sm font-medium text-white">Plate map</h2>
        <span className="text-xs text-mist">{rows.length} doses</span>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto px-3 py-2.5">
        <table className="w-full text-xs border-separate border-spacing-y-[5px]">
          <thead>
            <tr className="text-mist">
              <th scope="col" colSpan={2} rowSpan={2} className="text-left font-normal align-bottom pb-1">Dose</th>
              <th scope="colgroup" colSpan={reps} className="font-normal">Replicate</th>
              {maxReps > reps && <th rowSpan={2} />}
            </tr>
            <tr className="text-mist">
              {Array.from({ length: reps }, (_, r) => (
                <th key={r} scope="col" className="font-normal pb-1 w-9">{r + 1}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {[...rows].reverse().map((row) => (
              <tr key={row.index}>
                <td className="text-mist/70 text-[10px] w-4 pr-1 font-mono">{row.index + 1}</td>
                <th scope="row" className="text-left font-normal whitespace-nowrap pr-1 text-haze">{row.label || "—"}</th>
                {Array.from({ length: reps }, (_, r) => {
                  const w = at(row.index, r + 1);
                  if (r >= row.replicates) return <td key={r} />;
                  return (
                    <td key={r} className="text-center">
                      {w ? (
                        <WellButton well={w} selected={w.well_id === selected} latest={w.well_id === latest}
                                    label={`Well ${w.order}: dose ${row.index + 1}, replicate ${r + 1}. Show in microscope`}
                                    onClick={() => onSelect(w.well_id)} />
                      ) : (
                        <span role="img" aria-label={`dose ${row.index + 1}, replicate ${r + 1}: not acquired`}
                              className="well mx-auto block h-7 w-7 rounded-full" />
                      )}
                    </td>
                  );
                })}
                {maxReps > reps && (
                  <td className="text-[10px] text-mist whitespace-nowrap pl-0.5">{row.replicates > reps ? `+${row.replicates - reps}` : ""}</td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
        {!doses.length && (
          <p className="mt-2 text-xs text-mist">Concentrations appear once the agent lists the dose series.</p>
        )}
        {doses.length > 0 && wells.length === 0 && <p className="mt-2 text-xs text-mist">No wells acquired yet.</p>}
        {wells.length > 0 && <p className="mt-2 text-[11px] text-mist leading-snug">Numbers give the order the agent acquired wells in.</p>}
        {maxReps > reps && <p className="mt-1 text-[11px] text-mist leading-snug">+n: more replicates at that dose than shown.</p>}
      </div>
    </div>
  );
}

function WellButton({ well, selected, latest, label, onClick }: {
  well: AcquiredWell; selected: boolean; latest: boolean; label: string; onClick: () => void;
}) {
  const [missing, setMissing] = useState(false);
  const field = well.field_ids[0];
  return (
    <button
      onClick={onClick}
      aria-pressed={selected}
      aria-label={label}
      className={`relative mx-auto block h-7 w-7 rounded-full overflow-hidden bg-night-3 transition-shadow
        ${selected ? "ring-2 ring-white ring-offset-2 ring-offset-night" : latest ? "ring-1 ring-white/60" : "ring-1 ring-white/20 hover:ring-white/50"}
        ${latest ? "shadow-[0_0_14px_rgba(255,255,255,0.35)]" : ""}`}
    >
      {field && !missing && (
        <img src={imageUrl(`${field}.composite`)} alt="" loading="lazy" onError={() => setMissing(true)}
             className="stain-tint absolute inset-0 h-full w-full object-cover scale-[2.6] blur-[0.4px]" />
      )}
      {field && !missing && (
        <span aria-hidden className="absolute inset-0 bg-[radial-gradient(circle,rgba(0,0,0,0.65)_0%,rgba(0,0,0,0.15)_70%)]" />
      )}
      <span className="absolute inset-0 flex items-center justify-center text-[12px] font-semibold text-white [text-shadow:0_0_4px_#000]">
        {well.order}
      </span>
    </button>
  );
}
