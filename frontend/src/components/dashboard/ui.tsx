"use client";

/**
 * The shared CollectFlow UI kit (complete UX redesign, docs/v2/13 §4). Every page builds on these pieces so
 * headers, sections, tabs, filters, tables, statuses, notices and drawers look and behave the same
 * everywhere. Colours are the semantic tokens from app/globals.css.
 */
import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from "react";

// Buttons and form controls ---------------------------------------------------------------------------------

const focusRing = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2";

export const buttonStyles = {
  primary: `inline-flex h-10 items-center justify-center gap-2 whitespace-nowrap rounded-lg bg-brand-600 px-4 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-brand-500 disabled:cursor-not-allowed disabled:opacity-50 ${focusRing}`,
  secondary: `inline-flex h-10 items-center justify-center gap-2 whitespace-nowrap rounded-lg border border-line-strong bg-white px-3.5 text-sm font-medium text-ink shadow-sm transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50 ${focusRing}`,
  danger: `inline-flex h-10 items-center justify-center gap-2 whitespace-nowrap rounded-lg border border-danger-200 bg-white px-3.5 text-sm font-medium text-danger-700 shadow-sm transition-colors hover:bg-danger-50 disabled:cursor-not-allowed disabled:opacity-50 ${focusRing}`,
  ghost: `inline-flex h-10 items-center justify-center gap-2 whitespace-nowrap rounded-lg px-3 text-sm font-medium text-ink-muted transition-colors hover:bg-slate-100 hover:text-ink disabled:cursor-not-allowed disabled:opacity-50 ${focusRing}`,
  primarySm: `inline-flex h-8 items-center justify-center gap-1.5 whitespace-nowrap rounded-md bg-brand-600 px-3 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-brand-500 disabled:cursor-not-allowed disabled:opacity-50 ${focusRing}`,
  secondarySm: `inline-flex h-8 items-center justify-center gap-1.5 whitespace-nowrap rounded-md border border-line-strong bg-white px-3 text-xs font-medium text-ink shadow-sm transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50 ${focusRing}`,
  link: "rounded text-sm font-medium text-brand-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500",
};

export const inputStyles = {
  input:
    "h-10 w-full rounded-lg border border-line-strong bg-white px-3 text-sm text-ink placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/25 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-ink-subtle",
  select:
    "h-10 w-full rounded-lg border border-line-strong bg-white px-3 text-sm text-ink focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/25 disabled:cursor-not-allowed disabled:bg-slate-50",
  textarea:
    "w-full rounded-lg border border-line-strong bg-white px-3 py-2 text-sm text-ink placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/25 disabled:cursor-not-allowed disabled:bg-slate-50",
  label: "mb-1.5 block text-sm font-medium text-ink",
  help: "mt-1.5 text-xs text-ink-subtle",
};

/** Table anatomy shared by every list: sentence-case headers, 56 px rows, numbers right-aligned. */
export const tableStyles = {
  // `relative` keeps absolutely positioned content (screen-reader labels) inside the scroll area.
  wrap: "relative overflow-x-auto",
  table: "min-w-full text-sm",
  headRow: "border-b border-line bg-slate-50/70 text-left text-xs font-medium text-ink-subtle",
  th: "px-4 py-3 font-medium whitespace-nowrap first:pl-6 last:pr-6",
  thNum: "px-4 py-3 text-right font-medium whitespace-nowrap first:pl-6 last:pr-6",
  body: "divide-y divide-slate-100",
  row: "transition-colors hover:bg-slate-50/80",
  td: "px-4 py-4 align-middle first:pl-6 last:pr-6",
  tdNum: "px-4 py-4 text-right align-middle font-medium tabular-nums whitespace-nowrap first:pl-6 last:pr-6",
};

// Basic pieces ----------------------------------------------------------------------------------------------

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-slate-200/70 ${className}`} aria-hidden="true" />;
}

export function ErrorMessage({ message }: { message: string }) {
  return (
    <p role="alert" className="rounded-lg border border-danger-200 bg-danger-50 px-4 py-3 text-sm text-danger-800">
      {message}
    </p>
  );
}

// Outline icons (24×24, stroke-based) drawn inline to avoid an icon dependency.
const ICON_PATHS = {
  document:
    "M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25M9 16.5h6m-6-3h6M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z",
  clock: "M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
  check: "M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
  sparkles:
    "M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09ZM18.259 8.715 18 9.75l-.259-1.035a3.375 3.375 0 0 0-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 0 0 2.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 0 0 2.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 0 0-2.456 2.456Z",
  close: "M6 18 18 6M6 6l12 12",
  sync: "M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99",
  info: "m11.25 11.25.041-.02a.75.75 0 0 1 1.063.852l-.708 2.836a.75.75 0 0 0 1.063.853l.041-.021M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9-3.75h.008v.008H12V8.25Z",
  warning:
    "M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z",
  chevron: "m8.25 4.5 7.5 7.5-7.5 7.5",
  mail: "M21.75 6.75v10.5a2.25 2.25 0 0 1-2.25 2.25h-15a2.25 2.25 0 0 1-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0 0 19.5 4.5h-15a2.25 2.25 0 0 0-2.25 2.25m19.5 0v.243a2.25 2.25 0 0 1-1.07 1.916l-7.5 4.615a2.25 2.25 0 0 1-2.36 0L3.32 8.91a2.25 2.25 0 0 1-1.07-1.916V6.75",
  user: "M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.501 20.118a7.5 7.5 0 0 1 14.998 0A17.933 17.933 0 0 1 12 21.75c-2.676 0-5.216-.584-7.499-1.632Z",
  pause: "M14.25 9v6m-4.5 0V9M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
  search: "m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z",
} as const;

export type IconName = keyof typeof ICON_PATHS;

export function Icon({ name, className = "h-5 w-5" }: { name: IconName; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} className={className} aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d={ICON_PATHS[name]} />
    </svg>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-xl border border-line bg-surface shadow-[0_1px_2px_rgba(15,23,42,0.04)] ${className}`}>{children}</div>;
}

export function Spinner({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <span
      className={`inline-block animate-spin rounded-full border-2 border-current border-t-transparent ${className}`}
      aria-hidden="true"
    />
  );
}

// Page structure --------------------------------------------------------------------------------------------

/**
 * Every page starts with this: the title and one purpose line on the left, the page's primary action (and
 * at most a few secondary ones) on the right, top-aligned with the title. On phones the actions move below.
 */
export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-ink-muted">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2 max-sm:[&>*]:flex-1">{actions}</div>}
    </header>
  );
}

/**
 * A titled card: heading (with an optional count) and description on the left, list filters or utility
 * actions on the right, then the content. The header and body share one inner left edge.
 */
export function Section({
  title,
  count,
  description,
  actions,
  children,
  className = "",
  flush = false,
  headingLevel = 2,
}: {
  title: ReactNode;
  count?: number | string;
  description?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
  /** Content without padding (tables and lists bring their own). */
  flush?: boolean;
  headingLevel?: 2 | 3;
}) {
  const id = useId();
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <section aria-labelledby={id} className={className}>
      <Card className="overflow-hidden">
        <div className="flex flex-col gap-3 border-b border-line px-5 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">
          <div className="min-w-0">
            <Heading id={id} className="text-base font-semibold text-ink">
              {title}
              {count !== undefined && <span className="ml-1.5 font-normal text-ink-subtle tabular-nums">{count}</span>}
            </Heading>
            {description && <p className="mt-0.5 text-sm text-ink-muted">{description}</p>}
          </div>
          {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
        </div>
        {children !== undefined && <div className={flush ? "" : "px-5 py-5 sm:px-6"}>{children}</div>}
      </Card>
    </section>
  );
}

/** A metric card. Cards in a row are equal height; label, value and supporting text line up across them. */
export function StatCard({
  label,
  value,
  detail,
  tone = "neutral",
  icon,
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  tone?: "neutral" | "danger" | "success" | "warning";
  icon?: IconName;
}) {
  const valueTone = { neutral: "text-ink", danger: "text-danger-700", success: "text-ink", warning: "text-warning-800" }[tone];
  const iconTone = {
    neutral: "bg-brand-50 text-brand-600",
    danger: "bg-danger-50 text-danger-600",
    success: "bg-success-50 text-success-600",
    warning: "bg-warning-50 text-warning-700",
  }[tone];
  return (
    <Card className="flex h-full flex-col p-5">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm font-medium text-ink-muted">{label}</p>
        {icon && (
          <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${iconTone}`}>
            <Icon name={icon} className="h-4 w-4" />
          </span>
        )}
      </div>
      <div className={`mt-1 text-2xl font-semibold tracking-tight tabular-nums ${valueTone}`}>{value}</div>
      {detail && <div className="mt-auto pt-3 text-sm text-ink-subtle">{detail}</div>}
    </Card>
  );
}

/** An inline message. Tone sets the colour and the icon; the title carries the meaning without colour. */
export function Notice({
  tone = "info",
  title,
  children,
  action,
  role,
}: {
  tone?: "info" | "warning" | "danger" | "success" | "neutral";
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  role?: "status" | "alert";
}) {
  const styles = {
    info: "border-info-200 bg-info-50 text-info-900",
    warning: "border-warning-300 bg-warning-50 text-warning-900",
    danger: "border-danger-200 bg-danger-50 text-danger-900",
    success: "border-success-200 bg-success-50 text-success-900",
    neutral: "border-line bg-slate-50 text-ink",
  }[tone];
  const icon: IconName = tone === "warning" || tone === "danger" ? "warning" : tone === "success" ? "check" : "info";
  return (
    <div role={role} className={`flex gap-3 rounded-lg border px-4 py-3 text-sm ${styles}`}>
      <Icon name={icon} className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0 flex-1">
        {title && <p className="font-semibold">{title}</p>}
        {children && <div className={title ? "mt-0.5" : ""}>{children}</div>}
      </div>
      {action && <div className="shrink-0 self-center">{action}</div>}
    </div>
  );
}

/** What an empty list means, whether that is normal, and (optionally) what to do next. */
export function EmptyState({
  title,
  children,
  action,
  icon = "check",
  compact = false,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  icon?: IconName;
  compact?: boolean;
}) {
  return (
    <div className={`flex flex-col items-center text-center ${compact ? "px-5 py-6" : "px-6 py-12"}`}>
      {!compact && (
        <span className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-slate-100 text-ink-subtle">
          <Icon name={icon} className="h-5 w-5" />
        </span>
      )}
      <p className="text-sm font-semibold text-ink">{title}</p>
      {children && <div className="mx-auto mt-1 max-w-md text-sm text-ink-muted">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

const PILL_TONES = {
  neutral: { pill: "bg-slate-100 text-slate-700 ring-slate-500/20", dot: "bg-slate-400" },
  info: { pill: "bg-info-50 text-info-700 ring-info-600/20", dot: "bg-info-500" },
  brand: { pill: "bg-brand-50 text-brand-700 ring-brand-600/20", dot: "bg-brand-500" },
  good: { pill: "bg-success-50 text-success-700 ring-success-600/20", dot: "bg-success-500" },
  warn: { pill: "bg-warning-50 text-warning-800 ring-warning-600/30", dot: "bg-warning-500" },
  bad: { pill: "bg-danger-50 text-danger-700 ring-danger-600/20", dot: "bg-danger-500" },
} as const;

export type PillTone = keyof typeof PILL_TONES;

/** A status shown the same way everywhere (labels come from lib/status; the text alone carries the meaning). */
export function StatusPill({ label, tone = "neutral", title }: { label: string; tone?: PillTone; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${PILL_TONES[tone].pill}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${PILL_TONES[tone].dot}`} aria-hidden="true" />
      {label}
    </span>
  );
}

/** Label/value pairs in a responsive grid. */
export function KeyValues({ items, columns = 3 }: { items: { label: string; value: ReactNode }[]; columns?: 2 | 3 | 4 }) {
  const cols = { 2: "sm:grid-cols-2", 3: "sm:grid-cols-3", 4: "sm:grid-cols-2 lg:grid-cols-4" }[columns];
  return (
    <dl className={`grid grid-cols-2 gap-x-6 gap-y-4 ${cols}`}>
      {items.map((item) => (
        <div key={item.label} className="min-w-0">
          <dt className="text-xs font-medium text-ink-subtle">{item.label}</dt>
          <dd className="mt-1 text-sm font-medium text-ink">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

// Tabs and filters ------------------------------------------------------------------------------------------

export type TabItem<K extends string> = { key: K; label: string; count?: number };

/**
 * Navigation tabs: switch between different content sections of one page or record (WAI-ARIA tabs pattern:
 * arrow keys, Home and End move between tabs). Aligned to the content container, underline for the active
 * tab, scrolls horizontally on small screens instead of wrapping. Use SegmentedFilter for list filters.
 */
export function Tabs<K extends string>({
  tabs,
  value,
  onChange,
  label,
  idBase,
  className = "",
}: {
  tabs: TabItem<K>[];
  value: K;
  onChange: (key: K) => void;
  label: string;
  idBase: string;
  className?: string;
}) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const last = tabs.length - 1;
    const next =
      event.key === "ArrowRight" ? (index === last ? 0 : index + 1)
      : event.key === "ArrowLeft" ? (index === 0 ? last : index - 1)
      : event.key === "Home" ? 0
      : event.key === "End" ? last
      : null;
    if (next === null) return;
    event.preventDefault();
    const tab = tabs[next];
    if (!tab) return;
    onChange(tab.key);
    refs.current[tab.key]?.focus();
  };
  return (
    <div className={`relative border-b border-line ${className}`}>
      <div role="tablist" aria-label={label} className="-mb-px flex gap-6 overflow-x-auto [scrollbar-width:thin]">
        {tabs.map((tab, index) => {
          const active = tab.key === value;
          return (
            <button
              key={tab.key}
              ref={(el) => {
                refs.current[tab.key] = el;
              }}
              type="button"
              role="tab"
              id={`${idBase}-tab-${tab.key}`}
              aria-selected={active}
              aria-controls={`${idBase}-panel-${tab.key}`}
              tabIndex={active ? 0 : -1}
              onClick={() => onChange(tab.key)}
              onKeyDown={(event) => onKeyDown(event, index)}
              className={`inline-flex h-11 shrink-0 items-center gap-2 border-b-2 text-sm whitespace-nowrap transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500 ${
                active ? "border-brand-600 font-semibold text-ink" : "border-transparent font-medium text-ink-subtle hover:border-slate-300 hover:text-ink"
              }`}
            >
              {tab.label}
              {tab.count !== undefined && (
                <span
                  className={`min-w-5 rounded-full px-1.5 text-center text-xs font-semibold tabular-nums ${
                    active ? "bg-brand-100 text-brand-700" : "bg-slate-100 text-ink-muted"
                  }`}
                >
                  {tab.count}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function TabPanel({ idBase, tab, children, className = "" }: { idBase: string; tab: string; children: ReactNode; className?: string }) {
  return (
    <div role="tabpanel" id={`${idBase}-panel-${tab}`} aria-labelledby={`${idBase}-tab-${tab}`} tabIndex={0} className={`focus:outline-none ${className}`}>
      {children}
    </div>
  );
}

/**
 * A segmented filter: changes which records one list shows (not a navigation tab). Buttons with
 * aria-pressed inside a labelled group; scrolls horizontally on small screens.
 */
export function SegmentedFilter<K extends string>({
  options,
  value,
  onChange,
  label,
  fill = false,
}: {
  options: TabItem<K>[];
  value: K;
  onChange: (key: K) => void;
  label: string;
  /** Share the full width evenly (for narrow list panels). */
  fill?: boolean;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className={`flex max-w-full gap-1 overflow-x-auto rounded-lg bg-slate-100 p-1 [scrollbar-width:none] ${fill ? "w-full" : ""}`}
    >
      {options.map((option) => {
        const active = option.key === value;
        return (
          <button
            key={option.key}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(option.key)}
            className={`inline-flex h-8 items-center justify-center gap-1.5 whitespace-nowrap rounded-md text-sm font-medium transition-colors ${fill ? "flex-1 px-2" : "shrink-0 px-3"} focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 ${
              active ? "bg-white text-ink shadow-sm" : "text-ink-subtle hover:text-ink"
            }`}
          >
            {option.label}
            {option.count !== undefined && (
              <span className={`text-xs tabular-nums ${active ? "text-ink-muted" : "text-slate-400"}`}>{option.count}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}

// Drawer ------------------------------------------------------------------------------------------------------

const FOCUSABLE = 'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * A side drawer for inspecting one record: a fixed header (eyebrow, title, identifier, close), an optional
 * fixed summary and tab bar, a scrolling body, and an optional fixed footer for actions. Full screen on
 * phones. Focus moves into the drawer, stays there, and returns to where it was when the drawer closes.
 */
export function Drawer({
  eyebrow,
  title,
  subtitle,
  onClose,
  summary,
  tabs,
  children,
  footer,
  bodyKey,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  subtitle?: ReactNode;
  onClose: () => void;
  summary?: ReactNode;
  tabs?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  /** Changing it scrolls the body back to the top (e.g. when the tab changes). */
  bodyKey?: string;
}) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const body = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panel.current?.querySelector<HTMLElement>("[data-drawer-close]")?.focus();
    return () => {
      document.body.style.overflow = overflow;
      previous?.focus?.();
    };
  }, []);

  useEffect(() => {
    body.current?.scrollTo?.({ top: 0 });
  }, [bodyKey]);

  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      // A confirmation dialog on top of the drawer handles its own keys.
      if (document.querySelector('[role="alertdialog"]')) return;
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key !== "Tab" || !panel.current) return;
      const items = [...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null);
      const first = items[0];
      const last = items[items.length - 1];
      if (!first || !last) return;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <div className="absolute inset-0 bg-slate-900/30" onClick={onClose} aria-hidden="true" />
      <div ref={panel} className="absolute inset-y-0 right-0 flex w-full flex-col bg-surface shadow-2xl sm:max-w-[640px]">
        <header className="flex items-start justify-between gap-4 border-b border-line px-5 pt-5 pb-4 sm:px-6">
          <div className="min-w-0">
            {eyebrow && <p className="text-xs font-medium text-ink-subtle">{eyebrow}</p>}
            <h2 id={titleId} className="mt-0.5 truncate text-lg font-semibold text-ink">
              {title}
            </h2>
            {subtitle && <div className="mt-0.5 text-sm text-ink-muted">{subtitle}</div>}
          </div>
          <button
            type="button"
            data-drawer-close
            onClick={onClose}
            className="-mr-2 rounded-lg p-2 text-ink-subtle hover:bg-slate-100 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            aria-label="Close"
          >
            <Icon name="close" />
          </button>
        </header>
        {(summary || tabs) && (
          // With tabs, the tab bar's own bottom border closes this area.
          <div className={`px-5 sm:px-6 ${tabs ? "" : "border-b border-line"}`}>
            {summary && <div className="py-4">{summary}</div>}
            {tabs}
          </div>
        )}
        <div ref={body} className="flex-1 overflow-y-auto px-5 py-5 sm:px-6">
          {children}
        </div>
        {footer && <div className="border-t border-line bg-white px-5 py-3 sm:px-6">{footer}</div>}
      </div>
    </div>
  );
}
