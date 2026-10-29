import type { ReactNode } from "react";
import { href, type Route } from "../lib/router";

const DISCLAIMER = "Research tool for cell-biology screening data. Not for clinical use.";

export function Nav({ route, liveNote }: { route: Route; liveNote?: string }) {
  const link = (r: Route, label: string, active: boolean) => (
    <a
      href={href(r)}
      aria-current={active ? "page" : undefined}
      className={`px-2 py-1 rounded-control text-sm ${active ? "text-ink font-medium bg-sheet" : "text-graphite hover:text-ink"}`}
    >
      {label}
    </a>
  );
  return (
    <header className="no-print h-11 shrink-0 flex items-center gap-4 px-4 border-b border-rule bg-paper">
      <a href="#/" className="font-serif text-[19px] font-semibold tracking-tight text-ink">MuseCell</a>
      <nav className="flex items-center gap-1" aria-label="Main">
        {link({ name: "home" }, "New run", route.name === "home")}
        {link({ name: "benchmark" }, "Benchmark", route.name === "benchmark")}
      </nav>
      {liveNote && <span className="ml-auto label">{liveNote}</span>}
    </header>
  );
}

export function Footer({ compact = false }: { compact?: boolean }) {
  return (
    <footer className={`no-print shrink-0 px-4 text-graphite ${compact ? "h-6 flex items-center text-[11px]" : "py-4 text-xs leading-relaxed border-t border-rule"}`}>
      <p className={compact ? "truncate" : ""}>
        <span className="text-ink">{DISCLAIMER}</span>{" "}
        Images: BBBC021v1 (Caie et al., Molecular Cancer Therapeutics, 2010) from the Broad Bioimage Benchmark
        Collection (Ljosa et al., Nature Methods, 2012); also IDR idr0035, CC BY 4.0, © AstraZeneca.
      </p>
    </footer>
  );
}

export function Page({ children }: { children: ReactNode }) {
  return <main className="flex-1 min-h-0 overflow-y-auto">{children}</main>;
}
