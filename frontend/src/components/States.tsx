import { AlertTriangle, Loader2 } from "lucide-react";
import type { ReactNode } from "react";

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-2 p-4 text-sm">
      <p className="font-medium text-ink dark:text-white">{title}</p>
      {children && <div className="text-graphite dark:text-mist max-w-prose">{children}</div>}
      {action}
    </div>
  );
}

export function ErrorNote({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div role="alert" className="flex gap-2.5 rounded-panel border border-ink/40 bg-white/70 p-3 text-sm dark:border-white/20 dark:bg-white/5">
      <AlertTriangle aria-hidden className="h-4 w-4 mt-0.5 shrink-0 text-ink dark:text-haze" />
      <div className="flex flex-col gap-1">
        <p className="font-medium text-ink dark:text-white">{title}</p>
        {children && <div className="text-graphite dark:text-mist">{children}</div>}
        {action && <div className="mt-1">{action}</div>}
      </div>
    </div>
  );
}

export function Loading({ label }: { label: string }) {
  return (
    <p className="flex items-center gap-2 p-4 text-sm text-graphite dark:text-mist" role="status">
      <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> {label}
    </p>
  );
}
