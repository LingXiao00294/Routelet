import type { Attempt, Daily } from "./types";
const number = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 });
export const count = (value?: number | null): string =>
  value == null || !Number.isFinite(value) ? "—" : number.format(value);
export function compact(value?: number | null): string {
  if (value == null || !Number.isFinite(value)) return "—";
  if (value >= 1e9) return number.format(value / 1e9) + "B";
  if (value >= 1e6) return number.format(value / 1e6) + "M";
  if (value >= 1e3) return number.format(value / 1e3) + "k";
  return count(value);
}
export const money = (value?: number | null, digits = 4): string =>
  value == null || !Number.isFinite(value)
    ? "—"
    : "$" +
      value.toLocaleString("en-US", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });
export const latency = (value?: number | null): string =>
  value == null || !Number.isFinite(value) || value < 0
    ? "—"
    : value < 1000
      ? Math.round(value) + " ms"
      : (value / 1000).toFixed(2) + " s";
export function dateTime(value: string, timeOnly = false): string {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "—";
  return date.toLocaleString(
    "zh-CN",
    timeOnly
      ? { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }
      : {
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false,
        },
  );
}
export function prettyJson(value?: string | null): string {
  if (value == null || value === "") return "没有记录正文";
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}
export function parseAttempts(value?: string | null): Attempt[] {
  try {
    const parsed: unknown = JSON.parse(value ?? "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (item): item is Attempt =>
          !!item &&
          typeof item === "object" &&
          typeof item.provider === "string",
      )
      .map((item) => ({
        ...item,
        model: typeof item.model === "string" ? item.model : undefined,
        error: typeof item.error === "string" ? item.error : undefined,
        latency_ms:
          typeof item.latency_ms === "number" &&
          Number.isFinite(item.latency_ms) &&
          item.latency_ms >= 0
            ? item.latency_ms
            : undefined,
      }));
  } catch {
    return [];
  }
}
export function fillDays(
  rows: Daily[],
  days: number,
  now = new Date(),
): Daily[] {
  const byDay = new Map(rows.map((row) => [row.day, row]));
  const end = Date.UTC(
    now.getUTCFullYear(),
    now.getUTCMonth(),
    now.getUTCDate(),
  );
  return Array.from({ length: days }, (_, index) => {
    const day = new Date(end - (days - index - 1) * 86400000)
      .toISOString()
      .slice(0, 10);
    return (
      byDay.get(day) ?? {
        day,
        count: 0,
        success_count: 0,
        input_tokens: 0,
        output_tokens: 0,
        cache_read_tokens: 0,
        cache_write_tokens: 0,
        cost_usd: 0,
      }
    );
  });
}
export function csvCell(value: unknown): string {
  let text = value == null ? "" : String(value);
  if (/^[=+\-@\t\r]/.test(text)) text = "'" + text;
  return '"' + text.replaceAll('"', '""') + '"';
}
export function downloadText(name: string, text: string, type = "text/plain") {
  const url = URL.createObjectURL(
    new Blob([text], { type: type + ";charset=utf-8" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
