const day = new Intl.DateTimeFormat(undefined, { weekday: "long", year: "numeric", month: "long", day: "numeric" });
const time = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
const short = new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric" });
const monthYear = new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short" });
const rel = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

export const fmtDay = (ts: number | null) => (ts ? day.format(ts) : "Undated");
export const fmtTime = (ts: number | null) => (ts ? time.format(ts) : "");
export const fmtShort = (ts: number | null | undefined) => (ts ? short.format(ts) : "");
export const fmtMonthYear = (ts: number | null | undefined) => (ts ? monthYear.format(ts) : "");
export const count = (n: number | undefined, one: string, many = `${one}s`) =>
  `${(n ?? 0).toLocaleString()} ${(n ?? 0) === 1 ? one : many}`;

export function ago(ts: number | null | undefined): string {
  if (!ts) return "";
  const s = (ts - Date.now()) / 1000;
  const units: [Intl.RelativeTimeFormatUnit, number][] = [["year", 31536000], ["month", 2592000], ["week", 604800], ["day", 86400], ["hour", 3600], ["minute", 60]];
  for (const [unit, size] of units) if (Math.abs(s) >= size) return rel.format(Math.round(s / size), unit);
  return "just now";
}

export function threadDate(ts: number | null): string {
  if (!ts) return "";
  const d = new Date(ts);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) return time.format(d);
  if (now.getTime() - d.getTime() < 6 * 86400000) return d.toLocaleDateString(undefined, { weekday: "short" });
  return d.toLocaleDateString(undefined, { year: "2-digit", month: "numeric", day: "numeric" });
}

export function span(first: number | null | undefined, last: number | null | undefined): string {
  if (!first || !last) return "";
  const a = fmtMonthYear(first);
  const b = fmtMonthYear(last);
  return a === b ? a : `${a} – ${b}`;
}

export const bytes = (n: number | null | undefined) => {
  if (!n) return "";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 && i ? 1 : 0)} ${u[i]}`;
};
