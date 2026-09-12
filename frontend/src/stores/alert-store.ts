import { create } from "zustand";
import type { Alert, AlertSeverity } from "@/types/api";
import type { AlertFilters, AlertStats } from "@/types/alert";
import { api } from "@/lib/api-client";
import { WebSocketManager } from "@/lib/websocket";

// ─── State Interface ────────────────────────────────────────────────────────

export interface AlertState {
  /** Array of alerts currently loaded. */
  alerts: Alert[];

  /** Total number of alerts matching the current filters. */
  totalCount: number;

  /** Number of unread/active alerts. */
  unreadCount: number;

  /** Current filter configuration. */
  filters: AlertFilters;

  /** Alert statistics summary. */
  stats: AlertStats | null;

  /** Whether alerts are currently being fetched. */
  isLoading: boolean;

  /** Error from the last alert operation. */
  error: string | null;

  /** Whether audio notifications are enabled. */
  soundEnabled: boolean;
}

export interface AlertActions {
  /**
   * Fetch alerts from the API with the current filters.
   */
  fetchAlerts: (filters?: Partial<AlertFilters>) => Promise<void>;

  /**
   * Fetch alert statistics summary.
   */
  fetchStats: () => Promise<void>;

  /**
   * Add a new alert (from WebSocket or API response).
   */
  addAlert: (alert: Alert) => void;

  /**
   * Acknowledge an alert by ID.
   */
  acknowledgeAlert: (alertId: string, notes?: string) => Promise<void>;

  /**
   * Resolve an alert by ID.
   */
  resolveAlert: (alertId: string, notes?: string) => Promise<void>;

  /**
   * Dismiss an alert by ID.
   */
  dismissAlert: (alertId: string) => Promise<void>;

  /**
   * Perform a bulk action on multiple alerts.
   */
  bulkAction: (
    alertIds: string[],
    action: "acknowledge" | "resolve" | "dismiss"
  ) => Promise<void>;

  /**
   * Update filters and re-fetch alerts.
   */
  setFilters: (filters: Partial<AlertFilters>) => void;

  /**
   * Reset filters to defaults.
   */
  resetFilters: () => void;

  /**
   * Toggle sound notifications.
   */
  toggleSound: () => void;

  /**
   * Subscribe to real-time alerts via WebSocket.
   * Returns an unsubscribe function.
   */
  subscribeToAlerts: (wsManager: WebSocketManager) => () => void;

  /**
   * Set the unread alert count.
   */
  setUnreadCount: (count: number) => void;

  /**
   * Mark all active alerts as read (resets unread count).
   */
  markAllRead: () => void;

  /**
   * Clear error state.
   */
  clearError: () => void;
}

export type AlertStore = AlertState & AlertActions;

// ─── Default Filters ────────────────────────────────────────────────────────

const DEFAULT_FILTERS: AlertFilters = {
  sort_by: "created_at",
  sort_order: "desc",
  page: 1,
  page_size: 25,
};

// ─── Store Implementation ───────────────────────────────────────────────────

export const useAlertStore = create<AlertStore>()((set, get) => ({
  // ── State ────────────────────────────────────────────────────────────
  alerts: [],
  totalCount: 0,
  unreadCount: 0,
  filters: { ...DEFAULT_FILTERS },
  stats: null,
  isLoading: false,
  error: null,
  soundEnabled: true,

  // ── Actions ──────────────────────────────────────────────────────────

  fetchAlerts: async (filters?: Partial<AlertFilters>) => {
    const currentFilters = { ...get().filters, ...filters };
    set({ isLoading: true, error: null, filters: currentFilters });

    try {
      const response = await api.getRaw<any>("/api/v1/alerts", {
        params: currentFilters,
      });

      // After interceptor unwrap, response.data is the alerts array directly
      const raw = response.data;
      const alerts: Alert[] = Array.isArray(raw) ? raw : (raw?.data ?? []);
      const totalCount = Array.isArray(raw) ? raw.length : (raw?.total ?? raw?.data?.length ?? 0);

      set({
        alerts,
        totalCount,
        isLoading: false,
      });
    } catch (err: unknown) {
      const message =
        err && typeof err === "object" && "message" in err
          ? (err as { message: string }).message
          : "Failed to fetch alerts";

      set({ isLoading: false, error: message });
    }
  },

  fetchStats: async () => {
    try {
      const stats = await api.get<AlertStats>("/api/v1/alerts/stats");
      set({ stats, unreadCount: stats.active });
    } catch {
      // Silently fail for stats
    }
  },

  addAlert: (alert: Alert) => {
    set((state) => {
      // Avoid duplicates
      const exists = state.alerts.some((a) => a.id === alert.id);
      if (exists) return state;

      // Play notification sound for critical/high alerts
      if (state.soundEnabled && (alert.severity === "critical" || alert.severity === "high")) {
        playAlertSound(alert.severity);
      }

      return {
        alerts: [alert, ...state.alerts],
        totalCount: state.totalCount + 1,
        unreadCount: state.unreadCount + 1,
      };
    });
  },

  acknowledgeAlert: async (alertId: string, notes?: string) => {
    try {
      const updated = await api.post<Alert>(`/api/v1/alerts/${alertId}/acknowledge`, { notes });

      set((state) => ({
        alerts: state.alerts.map((a) => (a.id === alertId ? updated : a)),
        unreadCount: Math.max(0, state.unreadCount - 1),
      }));
    } catch (err: unknown) {
      const message =
        err && typeof err === "object" && "message" in err
          ? (err as { message: string }).message
          : "Failed to acknowledge alert";

      set({ error: message });
      throw err;
    }
  },

  resolveAlert: async (alertId: string, notes?: string) => {
    try {
      const updated = await api.post<Alert>(`/api/v1/alerts/${alertId}/resolve`, {
        resolution_notes: notes,
      });

      set((state) => ({
        alerts: state.alerts.map((a) => (a.id === alertId ? updated : a)),
        unreadCount: Math.max(0, state.unreadCount - 1),
      }));
    } catch (err: unknown) {
      const message =
        err && typeof err === "object" && "message" in err
          ? (err as { message: string }).message
          : "Failed to resolve alert";

      set({ error: message });
      throw err;
    }
  },

  dismissAlert: async (alertId: string) => {
    try {
      await api.post(`/api/v1/alerts/${alertId}/dismiss`);

      set((state) => ({
        alerts: state.alerts.filter((a) => a.id !== alertId),
        totalCount: state.totalCount - 1,
        unreadCount: Math.max(0, state.unreadCount - 1),
      }));
    } catch (err: unknown) {
      const message =
        err && typeof err === "object" && "message" in err
          ? (err as { message: string }).message
          : "Failed to dismiss alert";

      set({ error: message });
      throw err;
    }
  },

  bulkAction: async (alertIds: string[], action: "acknowledge" | "resolve" | "dismiss") => {
    try {
      await api.post("/api/v1/alerts/bulk", { alert_ids: alertIds, action });

      // Refresh alerts after bulk action
      await get().fetchAlerts();
      await get().fetchStats();
    } catch (err: unknown) {
      const message =
        err && typeof err === "object" && "message" in err
          ? (err as { message: string }).message
          : "Failed to perform bulk action";

      set({ error: message });
      throw err;
    }
  },

  setFilters: (filters: Partial<AlertFilters>) => {
    const newFilters = { ...get().filters, ...filters, page: filters.page || 1 };
    set({ filters: newFilters });
    get().fetchAlerts(newFilters);
  },

  resetFilters: () => {
    set({ filters: { ...DEFAULT_FILTERS } });
    get().fetchAlerts(DEFAULT_FILTERS);
  },

  toggleSound: () => {
    set((state) => ({ soundEnabled: !state.soundEnabled }));
  },

  subscribeToAlerts: (wsManager: WebSocketManager) => {
    const unsubNewAlert = wsManager.subscribe<Alert>("new_alert", (alert) => {
      get().addAlert(alert);
    });

    const unsubUpdated = wsManager.subscribe<Alert>("alert_updated", (alert) => {
      set((state) => ({
        alerts: state.alerts.map((a) => (a.id === alert.id ? alert : a)),
      }));
    });

    const unsubResolved = wsManager.subscribe<Alert>("alert_resolved", (alert) => {
      set((state) => ({
        alerts: state.alerts.map((a) => (a.id === alert.id ? alert : a)),
        unreadCount: Math.max(0, state.unreadCount - 1),
      }));
    });

    const unsubAcknowledged = wsManager.subscribe<Alert>("alert_acknowledged", (alert) => {
      set((state) => ({
        alerts: state.alerts.map((a) => (a.id === alert.id ? alert : a)),
      }));
    });

    // Return combined unsubscribe function
    return () => {
      unsubNewAlert();
      unsubUpdated();
      unsubResolved();
      unsubAcknowledged();
    };
  },

  setUnreadCount: (count: number) => {
    if (get().unreadCount !== count) set({ unreadCount: count });
  },

  markAllRead: () => {
    set({ unreadCount: 0 });
  },

  clearError: () => {
    set({ error: null });
  },
}));

// ─── Helpers ────────────────────────────────────────────────────────────────

/**
 * Play an alert notification sound based on severity.
 * Uses the Web Audio API for lightweight sound generation.
 */
function playAlertSound(severity: AlertSeverity): void {
  if (typeof window === "undefined" || !window.AudioContext) return;

  try {
    const ctx = new AudioContext();
    const oscillator = ctx.createOscillator();
    const gainNode = ctx.createGain();

    oscillator.connect(gainNode);
    gainNode.connect(ctx.destination);

    // Different tones for different severities
    switch (severity) {
      case "critical":
        oscillator.frequency.value = 880; // A5 - urgent
        oscillator.type = "square";
        break;
      case "high":
        oscillator.frequency.value = 660; // E5 - warning
        oscillator.type = "sawtooth";
        break;
      default:
        oscillator.frequency.value = 440; // A4 - info
        oscillator.type = "sine";
    }

    gainNode.gain.value = 0.1;
    gainNode.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.5);

    oscillator.start(ctx.currentTime);
    oscillator.stop(ctx.currentTime + 0.5);
  } catch {
    // Silently fail if audio is not available
  }
}
