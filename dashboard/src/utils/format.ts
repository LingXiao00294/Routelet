import type { DailyStat, FailoverEntry } from "@/api/types";

export function formatNumber(n: number | null | undefined, digits = 0): string {
  if (n == null || Number.isNaN(n)) return "—";
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(n);
}

export function formatUsd(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `$${formatNumber(n, n >= 1 ? 2 : 4)}`;
}

export function formatLatency(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

export function formatTokens(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

/** Format a structured actual-model identity without parsing display text. */
export function formatActualModel(
  provider: string | null | undefined,
  model: string | null | undefined,
): string {
  if (!provider || !model) return "—";
  return `${provider}/${model}`;
}

/** Format UTC ISO timestamp for display (local timezone). */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

/** Fill missing UTC calendar days so charts have continuous series. */
export function fillDailyGaps(rows: DailyStat[], days: number): DailyStat[] {
  const map = new Map(rows.map((r) => [r.day, r]));
  const out: DailyStat[] = [];
  const end = new Date();
  end.setUTCHours(0, 0, 0, 0);

  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(end);
    d.setUTCDate(end.getUTCDate() - i);
    const key = d.toISOString().slice(0, 10);
    const hit = map.get(key);
    out.push(
      hit ?? {
        day: key,
        count: 0,
        success_count: 0,
        input_tokens: 0,
        output_tokens: 0,
        cache_read_tokens: 0,
        cache_write_tokens: 0,
        cost_usd: 0,
      },
    );
  }
  return out;
}

export function parseFailover(raw: string | null): FailoverEntry[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    const entries: FailoverEntry[] = [];
    for (const value of parsed) {
      if (!value || typeof value !== "object" || Array.isArray(value)) continue;
      const { provider, model, error, latency_ms } = value as Record<string, unknown>;
      if (
        typeof provider !== "string" || !provider.trim() ||
        typeof model !== "string" || !model.trim() ||
        typeof error !== "string"
      ) {
        continue;
      }
      // Exceptions such as timeouts may legitimately have an empty message.
      const entry: FailoverEntry = { provider, model, error };
      if (typeof latency_ms === "number" && Number.isFinite(latency_ms) && latency_ms >= 0) {
        entry.latency_ms = latency_ms;
      }
      entries.push(entry);
    }
    return entries;
  } catch {
    return [];
  }
}

export function prettyJson(raw: string | null, maxLen = 80_000): string {
  if (!raw) return "";
  try {
    const parsed = JSON.parse(raw) as unknown;
    let text = JSON.stringify(parsed, null, 2);
    if (text.length > maxLen) {
      text = `${text.slice(0, maxLen)}\n… (truncated)`;
    }
    return text;
  } catch {
    return raw.length > maxLen ? `${raw.slice(0, maxLen)}\n… (truncated)` : raw;
  }
}

/** Parse a positive int from query/input; fallback when invalid. */
export function parsePositiveInt(
  raw: unknown,
  fallback: number,
  max?: number,
): number {
  const n = typeof raw === "string" || typeof raw === "number" ? Number(raw) : NaN;
  if (!Number.isFinite(n) || n < 1) return fallback;
  const i = Math.floor(n);
  return max != null ? Math.min(i, max) : i;
}
