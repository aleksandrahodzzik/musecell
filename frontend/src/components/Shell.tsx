import type { ReactNode } from "react";
import { href, type Route } from "../lib/router";

const DISCLAIMER = "Research tool for cell-biology screening data. Not for clinical use.";

/** The mark: a cell with its nucleus, drawn as seen through the eyepiece. Neutral, never a stain colour. */
export function Mark({ className = "h-6 w-6" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden className={className}>
      <circle cx="16" cy="16" r="13" fill="none" stroke="currentColor" strokeOpacity="0.35" strokeWidth="1" />
      <path d="M16 6.5c5.6 0 9.5 3.9 9.5 9.2 0 5.6-4.4 9.8-9.9 9.8-5.3 0-9.1-4-9.1-9.3C6.5 10.6 10.4 6.5 16 6.5Z"
            fill="none" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="13.6" cy="14.6" r="3.6" fill="currentColor" />
    </svg>
  );
}

export function Nav({ route, liveNote, live }: { route: Route; liveNote?: string; live?: boolean }) {
  const link = (r: Route, label: string, active: boolean) => (
    <a
      href={href(r)}
      aria-current={active ? "page" : undefined}
      className={`relative px-2 h-12 flex items-center text-sm transition-colors ${active ? "text-white" : "text-mist hover:text-white"}`}
    >
      {label}
      {active && <span aria-hidden className="absolute left-2 right-2 bottom-0 h-px bg-white" />}
    </a>
  );
  return (
    <header className="dark no-print h-12 shrink-0 flex items-center gap-5 px-4 bg-night border-b border-line">
      <a href="#/" className="flex items-center gap-2 text-haze hover:text-white">
        <Mark />
        <span className="font-serif text-[19px] font-semibold tracking-tight">MuseCell</span>
      </a>
      <nav className="flex items-center gap-1" aria-label="Main">
        {link({ name: "home" }, "New run", route.name === "home")}
        {link({ name: "benchmark" }, "Benchmark", route.name === "benchmark")}
      </nav>
      {liveNote && (
        <span className="ml-auto flex items-center gap-2 text-xs text-mist">
          <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${live ? "bg-white shadow-[0_0_8px_rgba(255,255,255,0.8)]" : "bg-mist/50"}`} />
          {liveNote}
        </span>
      )}
    </header>
  );
}

export function Footer({ compact = false }: { compact?: boolean }) {
  return (
    <footer className={`dark no-print shrink-0 px-4 text-mist bg-night border-t border-line ${compact ? "h-7 flex items-center text-[11px]" : "py-4 text-xs leading-relaxed"}`}>
      <p className={compact ? "truncate" : ""}>
        <span className="text-haze">{DISCLAIMER}</span>{" "}
        Images: BBBC021v1 (Caie et al., Molecular Cancer Therapeutics, 2010) from the Broad Bioimage Benchmark
        Collection (Ljosa et al., Nature Methods, 2012); also IDR idr0035, CC BY 4.0, © AstraZeneca.
      </p>
    </footer>
  );
}

export function Page({ children, dark = false }: { children: ReactNode; dark?: boolean }) {
  return <main className={`flex-1 min-h-0 overflow-y-auto ${dark ? "dark instrument" : "bg-paper"}`}>{children}</main>;
}
