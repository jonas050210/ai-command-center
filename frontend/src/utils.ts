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
  if (!iso) return "Unknown";
  // backend timestamps are UTC "YYYY-MM-DD HH:MM:SS"
  const normalized = iso.includes("T") ? iso : iso.replace(" ", "T") + "Z";
  const then = new Date(normalized).getTime();
  if (Number.isNaN(then)) return "Unknown";
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
