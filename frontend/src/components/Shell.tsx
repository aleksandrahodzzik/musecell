import { useEffect, useRef, type ElementType, type ReactNode } from "react";
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

/** A link whose underline draws in from the left on hover (design v2). */
function NavLink({ to, label, active }: { to: Route; label: string; active: boolean }) {
  return (
    <a href={href(to)} aria-current={active ? "page" : undefined}
       className="group relative text-[15px] font-medium text-ink whitespace-nowrap">
      {label}
      <span aria-hidden className={`absolute left-0 right-0 -bottom-0.5 h-px bg-current transition-transform duration-500 ease-[cubic-bezier(.7,0,.2,1)]
        ${active ? "scale-x-100" : "scale-x-0 origin-right group-hover:scale-x-100 group-hover:origin-left"}`} />
    </a>
  );
}

export function Nav({ route, liveNote, live }: { route: Route; liveNote?: string; live?: boolean }) {
  return (
    <header className="no-print h-16 shrink-0 flex items-center gap-8 px-5 lg:px-9 bg-paper text-ink">
      <a href="#/" className="flex items-center gap-2" aria-label="MuseCell, start page">
        <Mark className="h-6 w-6" />
        <span className="display text-[24px] leading-none">MuseCell</span>
      </a>
      <nav className="flex items-center gap-7" aria-label="Main">
        <NavLink to={{ name: "home" }} label="New run" active={route.name === "home"} />
        <NavLink to={{ name: "benchmark" }} label="Benchmark" active={route.name === "benchmark"} />
      </nav>
      <div className="ml-auto flex items-center gap-4">
        {liveNote && (
          <span className="tag hidden md:flex items-center gap-2 text-graphite">
            <span aria-hidden className={`h-[7px] w-[7px] rounded-full ${live ? "bg-lime animate-[pulse-lime_1.8s_ease-out_infinite] ring-1 ring-ink/40" : "bg-graphite/50"}`} />
            {liveNote}
          </span>
        )}
        {route.name !== "home" && (
          <a href="#/" className="btn-primary h-10">Start a run</a>
        )}
      </div>
    </header>
  );
}

export function Footer({ compact = false }: { compact?: boolean }) {
  return (
    <footer className={`no-print shrink-0 px-5 lg:px-9 text-graphite bg-paper border-t border-rule ${compact ? "h-8 flex items-center text-[11px]" : "py-4 text-xs leading-relaxed"}`}>
      <p className={compact ? "truncate" : ""}>
        <span className="text-ink font-medium">{DISCLAIMER}</span>{" "}
        Images: BBBC021v1 (Caie et al., Molecular Cancer Therapeutics, 2010) from the Broad Bioimage Benchmark
        Collection (Ljosa et al., Nature Methods, 2012); also IDR idr0035, CC BY 4.0, © AstraZeneca.
      </p>
    </footer>
  );
}

export function Page({ children, dark = false }: { children: ReactNode; dark?: boolean }) {
  return <main className={`flex-1 min-h-0 overflow-y-auto ${dark ? "dark instrument" : "bg-paper"}`}>{children}</main>;
}

/** Rises and fades in the first time it scrolls into view (instantly under prefers-reduced-motion). */
export function Reveal({ as: Tag = "div", className = "", children, ...rest }:
  { as?: ElementType; className?: string; children: ReactNode } & Record<string, unknown>) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (!("IntersectionObserver" in window)) return el.classList.add("is-in");
    const io = new IntersectionObserver(([e]) => {
      if (e.isIntersecting) { el.classList.add("is-in"); io.disconnect(); }
    }, { rootMargin: "0px 0px -10% 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return <Tag ref={ref} className={`reveal ${className}`} {...rest}>{children}</Tag>;
}
