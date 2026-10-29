// Plate map (spec §8): doses × replicates; acquired wells are filled and numbered in acquisition order.

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
    <div className="flex flex-col h-full min-h-0">
      <div className="flex items-center gap-2 px-3 h-10 border-b border-rule">
        <h2 className="text-sm font-medium">Plate map</h2>
        <span className="label">{rows.length} doses</span>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto px-3 py-2">
        <table className="w-full text-xs border-separate border-spacing-y-[3px]">
          <thead>
            <tr className="text-graphite">
              <th scope="col" colSpan={2} rowSpan={2} className="text-left font-normal align-bottom pb-1">Dose</th>
              <th scope="colgroup" colSpan={reps} className="font-normal">Replicate</th>
              {maxReps > reps && <th rowSpan={2} />}
            </tr>
            <tr className="text-graphite">
              {Array.from({ length: reps }, (_, r) => (
                <th key={r} scope="col" className="font-normal pb-1 w-8">{r + 1}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {[...rows].reverse().map((row) => (
              <tr key={row.index}>
                <td className="text-graphite text-[11px] w-4 pr-1">{row.index + 1}</td>
                <th scope="row" className="text-left font-normal whitespace-nowrap pr-1 text-ink">{row.label || "—"}</th>
                {Array.from({ length: reps }, (_, r) => {
                  const w = at(row.index, r + 1);
                  if (r >= row.replicates) return <td key={r} />;
                  return (
                    <td key={r} className="text-center">
                      {w ? (
                        <button
                          onClick={() => onSelect(w.well_id)}
                          aria-pressed={w.well_id === selected}
                          aria-label={`Well ${w.order}: dose ${row.index + 1}, replicate ${r + 1}. Show in microscope`}
                          className={`mx-auto flex h-6 w-6 items-center justify-center rounded-full bg-ink text-sheet text-[11px] font-medium
                            ${w.well_id === selected ? "outline outline-2 outline-offset-2 outline-ink" : ""}
                            ${w.well_id === latest ? "shadow-[0_0_0_5px_rgba(23,32,42,0.14)]" : ""}`}
                        >
                          {w.order}
                        </button>
                      ) : (
                        <span role="img" aria-label={`dose ${row.index + 1}, replicate ${r + 1}: not acquired`}
                              className="mx-auto block h-6 w-6 rounded-full border border-ink/25 bg-white/50" />
                      )}
                    </td>
                  );
                })}
                {maxReps > reps && (
                  <td className="text-[11px] text-graphite whitespace-nowrap pl-0.5">{row.replicates > reps ? `+${row.replicates - reps}` : ""}</td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
        {!doses.length && (
          <p className="mt-2 text-xs text-graphite">Concentrations appear once the agent lists the dose series.</p>
        )}
        {doses.length > 0 && wells.length === 0 && <p className="mt-2 text-xs text-graphite">No wells acquired yet.</p>}
        {wells.length > 0 && <p className="mt-2 text-xs text-graphite">Numbers give the order the agent acquired wells in.</p>}
        {maxReps > reps && <p className="mt-1 text-xs text-graphite">+n: more replicates at that dose than shown.</p>}
      </div>
    </div>
  );
}
