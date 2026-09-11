import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";
import { format, formatDistanceToNow } from "date-fns";

/**
 * Merge Tailwind CSS class names with clsx and tailwind-merge.
 * Handles conditional classes and deduplication of conflicting utilities.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/**
 * Format a date string or Date object into a human-readable string.
 *
 * @param date - Date to format (string, number, or Date)
 * @param formatStr - date-fns format string (default: "MMM d, yyyy HH:mm")
 * @returns Formatted date string
 */
export function formatDate(
  date: string | number | Date,
  formatStr: string = "MMM d, yyyy HH:mm"
): string {
  try {
    const d = typeof date === "string" || typeof date === "number" ? new Date(date) : date;
    if (isNaN(d.getTime())) return "Invalid date";
    return format(d, formatStr);
  } catch {
    return "Invalid date";
  }
}

/**
 * Format a date into a relative time string (e.g., "5 minutes ago").
 *
 * @param date - Date to format
 * @returns Relative time string
 */
export function formatRelativeTime(date: string | number | Date): string {
  try {
    const d = typeof date === "string" || typeof date === "number" ? new Date(date) : date;
    if (isNaN(d.getTime())) return "Unknown";
    return formatDistanceToNow(d, { addSuffix: true });
  } catch {
    return "Unknown";
  }
}

/**
 * Format a duration in seconds into a human-readable string.
 *
 * @param seconds - Duration in seconds
 * @returns Formatted duration string (e.g., "1h 23m 45s")
 */
export function formatDuration(seconds: number): string {
  if (seconds < 0 || !isFinite(seconds)) return "0s";

  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);

  const parts: string[] = [];
  if (hours > 0) parts.push(`${hours}h`);
  if (minutes > 0) parts.push(`${minutes}m`);
  if (secs > 0 || parts.length === 0) parts.push(`${secs}s`);

  return parts.join(" ");
}

/**
 * Format bytes into a human-readable size string.
 *
 * @param bytes - Number of bytes
 * @param decimals - Number of decimal places (default: 2)
 * @returns Formatted size string (e.g., "1.23 GB")
 */
export function formatBytes(bytes: number, decimals: number = 2): string {
  if (bytes === 0) return "0 B";
  if (bytes < 0 || !isFinite(bytes)) return "0 B";

  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ["B", "KB", "MB", "GB", "TB", "PB"];

  const i = Math.floor(Math.log(bytes) / Math.log(k));
  const index = Math.min(i, sizes.length - 1);

  return `${parseFloat((bytes / Math.pow(k, index)).toFixed(dm))} ${sizes[index]}`;
}

/**
 * Truncate a string to a maximum length, adding an ellipsis if needed.
 *
 * @param str - String to truncate
 * @param maxLength - Maximum allowed length (default: 50)
 * @returns Truncated string
 */
export function truncate(str: string, maxLength: number = 50): string {
  if (!str) return "";
  if (str.length <= maxLength) return str;
  return `${str.slice(0, maxLength).trimEnd()}...`;
}

/**
 * Extract initials from a name string.
 *
 * @param name - Full name string
 * @param maxChars - Maximum number of initials (default: 2)
 * @returns Uppercase initials (e.g., "JD" for "John Doe")
 */
export function getInitials(name: string, maxChars: number = 2): string {
  if (!name || typeof name !== "string") return "";

  return name
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, maxChars)
    .map((word) => word[0])
    .join("")
    .toUpperCase();
}

/**
 * Generate a deterministic color from a string for avatars, tags, etc.
 *
 * @param str - Input string
 * @returns HSL color string
 */
export function stringToColor(str: string): string {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = str.charCodeAt(i) + ((hash << 5) - hash);
  }
  const hue = Math.abs(hash % 360);
  return `hsl(${hue}, 65%, 45%)`;
}

/**
 * Sleep for a specified number of milliseconds.
 */
export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Debounce a function call.
 */
export function debounce<T extends (...args: unknown[]) => unknown>(
  fn: T,
  delay: number
): (...args: Parameters<T>) => void {
  let timeoutId: ReturnType<typeof setTimeout>;
  return (...args: Parameters<T>) => {
    clearTimeout(timeoutId);
    timeoutId = setTimeout(() => fn(...args), delay);
  };
}
