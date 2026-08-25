// Formatting + misc helpers.

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export function formatNumber(n: number | null | undefined): string {
  if (n === null || n === undefined) return "Unknown";
  return n.toLocaleString("en-US");
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "Unknown";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes;
  let u = -1;
  do {
    value /= 1024;
    u += 1;
  } while (value >= 1024 && u < units.length - 1);
  return `${value.toFixed(value >= 100 ? 0 : 1)} ${units[u]}`;
}

export function formatEuro(n: number | null | undefined, currency = "EUR"): string {
  const symbol = currency === "EUR" ? "€" : `${currency} `;
  if (n === null || n === undefined) return `${symbol}0.00`;
  return `${symbol}${n.toFixed(2)}`;
}

export function estimateTokensClient(text: string): number {
  // Mirrors the backend heuristic — always displayed as ESTIMATED.
  if (!text) return 0;
  return Math.max(1, Math.ceil(text.length / 4));
}

export function timeAgo(iso: string | null | undefined): string {
  const then = parseTs(iso);
  if (then === null) return "Unknown";
  const seconds = Math.max(0, (Date.now() - then) / 1000);
  if (seconds < 60) return "just now";
  const minutes = seconds / 60;
  if (minutes < 60) return `${Math.floor(minutes)}m ago`;
  const hours = minutes / 60;
  if (hours < 24) return `${Math.floor(hours)}h ago`;
  const days = hours / 24;
  if (days < 30) return `${Math.floor(days)}d ago`;
  return new Date(then).toLocaleDateString();
}

/** Parse the backend's UTC ISO-ish timestamp ("YYYY-MM-DD HH:MM:SS" or ISO). */
export function parseTs(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const normalized = iso.includes("T") ? iso : iso.replace(" ", "T") + "Z";
  const t = new Date(normalized).getTime();
  return Number.isNaN(t) ? null : t;
}

/** Local-time bucket for list grouping: Today · Yesterday · previous-7-days · older. */
export function dayBucket(iso: string | null | undefined): string {
  const t = parseTs(iso);
  if (t === null) return "Older";
  const now = new Date();
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  if (t >= startToday) return "Today";
  if (t >= startToday - 86400000) return "Yesterday";
  if (t >= startToday - 6 * 86400000) return "Previous 7 days";
  return "Older";
}

/** Clock label "14:03" in local time for message toolbars. */
export function formatClock(iso: string | null | undefined): string {
  const t = parseTs(iso);
  if (t === null) return "";
  return new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** Day-separator label for the chat timeline. */
export function dayLabel(iso: string | null | undefined): string {
  const bucket = dayBucket(iso);
  if (bucket === "Today" || bucket === "Yesterday") return bucket;
  const t = parseTs(iso);
  if (t === null) return "";
  return new Date(t).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
}

/** Same local calendar day? */
export function sameDay(a: string | null | undefined, b: string | null | undefined): boolean {
  const ta = parseTs(a); const tb = parseTs(b);
  if (ta === null || tb === null) return true;
  const da = new Date(ta); const db = new Date(tb);
  return da.getFullYear() === db.getFullYear() && da.getMonth() === db.getMonth()
    && da.getDate() === db.getDate();
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      return true;
    } catch {
      return false;
    }
  }
}
