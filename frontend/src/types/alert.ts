import type { Alert, AlertSeverity, AlertStatus, RuleType } from "./api";

// ─── Alert Filters ──────────────────────────────────────────────────────────

export interface AlertFilters {
  search?: string;
  severity?: AlertSeverity[];
  status?: AlertStatus[];
  type?: RuleType[];
  camera_id?: string;
  zone_id?: string;
  date_from?: string;
  date_to?: string;
  sort_by?: "created_at" | "severity" | "status" | "type";
  sort_order?: "asc" | "desc";
  page?: number;
  page_size?: number;
}

// ─── Alert Actions ──────────────────────────────────────────────────────────

export interface AlertAcknowledgeRequest {
  alert_id: string;
  notes?: string;
}

export interface AlertResolveRequest {
  alert_id: string;
  resolution_notes?: string;
  false_positive?: boolean;
}

export interface AlertBulkActionRequest {
  alert_ids: string[];
  action: "acknowledge" | "resolve" | "dismiss";
  notes?: string;
}

// ─── Alert Notification Preferences ─────────────────────────────────────────

export interface AlertNotificationConfig {
  enabled: boolean;
  channels: AlertNotificationChannel[];
  severity_filter: AlertSeverity[];
  type_filter: RuleType[];
  quiet_hours?: {
    enabled: boolean;
    start_time: string; // "HH:mm"
    end_time: string; // "HH:mm"
    timezone: string;
  };
}

export interface AlertNotificationChannel {
  type: "browser" | "email" | "sms" | "webhook" | "slack";
  enabled: boolean;
  config: Record<string, unknown>;
}

// ─── Alert Statistics ───────────────────────────────────────────────────────

export interface AlertStats {
  total: number;
  active: number;
  acknowledged: number;
  resolved: number;
  dismissed: number;
  by_severity: Record<AlertSeverity, number>;
  by_type: Record<string, number>;
  by_camera: Array<{
    camera_id: string;
    camera_name: string;
    count: number;
  }>;
  trend: AlertTrendPoint[];
}

export interface AlertTrendPoint {
  timestamp: string;
  count: number;
  severity_breakdown: Record<AlertSeverity, number>;
}

// ─── Real-time Alert Events (WebSocket) ─────────────────────────────────────

export interface AlertWebSocketEvent {
  event: "new_alert" | "alert_updated" | "alert_resolved" | "alert_acknowledged";
  data: Alert;
}

export interface AlertSoundConfig {
  enabled: boolean;
  volume: number; // 0-1
  sounds: Record<AlertSeverity, string | null>;
}

// ─── Alert Timeline ─────────────────────────────────────────────────────────

export interface AlertTimelineEntry {
  id: string;
  alert_id: string;
  action: "created" | "acknowledged" | "resolved" | "dismissed" | "reopened" | "comment";
  user_id?: string;
  user_name?: string;
  notes?: string;
  created_at: string;
}

// ─── Re-exports for convenience ─────────────────────────────────────────────

export type { Alert, AlertSeverity, AlertStatus, RuleType };
