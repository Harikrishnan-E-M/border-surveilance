// ─── Footfall / People Counting ─────────────────────────────────────────────

export interface FootfallData {
  camera_id: string;
  camera_name: string;
  zone_id?: string;
  zone_name?: string;
  timestamp: string;
  interval: "minute" | "hour" | "day" | "week" | "month";
  entry_count: number;
  exit_count: number;
  current_occupancy: number;
  peak_occupancy: number;
  avg_dwell_time_seconds: number;
}

export interface FootfallSummary {
  total_entries: number;
  total_exits: number;
  current_occupancy: number;
  peak_occupancy: number;
  peak_time: string;
  avg_dwell_time_seconds: number;
  trend_percentage: number; // positive = up, negative = down
  data_points: FootfallData[];
}

export interface FootfallFilters {
  camera_id?: string;
  zone_id?: string;
  date_from: string;
  date_to: string;
  interval: "minute" | "hour" | "day" | "week" | "month";
}

// ─── Heatmap ────────────────────────────────────────────────────────────────

export interface HeatmapData {
  camera_id: string;
  camera_name: string;
  width: number;
  height: number;
  cell_size: number;
  grid: number[][]; // 2D array of intensity values (0-255)
  max_value: number;
  min_value: number;
  total_detections: number;
  period_start: string;
  period_end: string;
}

export interface HeatmapConfig {
  opacity: number; // 0-1
  radius: number; // blur radius in pixels
  color_scheme: "default" | "thermal" | "viridis" | "magma" | "inferno";
  show_grid: boolean;
  show_values: boolean;
}

export interface HeatmapFilters {
  camera_id: string;
  zone_id?: string;
  date_from: string;
  date_to: string;
  object_type?: string; // "person", "vehicle", etc.
}

// ─── Dwell Time ─────────────────────────────────────────────────────────────

export interface DwellTimeData {
  camera_id: string;
  zone_id?: string;
  zone_name?: string;
  timestamp: string;
  avg_dwell_seconds: number;
  min_dwell_seconds: number;
  max_dwell_seconds: number;
  median_dwell_seconds: number;
  total_visitors: number;
  distribution: DwellTimeDistribution[];
}

export interface DwellTimeDistribution {
  range_label: string; // e.g., "0-30s", "30s-1m", "1-5m", "5m+"
  range_min_seconds: number;
  range_max_seconds: number;
  count: number;
  percentage: number;
}

// ─── Emotion / Demographics ─────────────────────────────────────────────────

export interface EmotionData {
  camera_id: string;
  camera_name: string;
  timestamp: string;
  interval: "hour" | "day" | "week";
  total_faces: number;
  emotions: EmotionBreakdown;
  age_groups: AgeGroupBreakdown;
  gender: GenderBreakdown;
}

export interface EmotionBreakdown {
  happy: number;
  neutral: number;
  sad: number;
  angry: number;
  surprised: number;
  fearful: number;
  disgusted: number;
}

export interface AgeGroupBreakdown {
  "0-17": number;
  "18-30": number;
  "31-45": number;
  "46-60": number;
  "60+": number;
}

export interface GenderBreakdown {
  male: number;
  female: number;
  unknown: number;
}

// ─── Dashboard Statistics ───────────────────────────────────────────────────

export interface DashboardStats {
  cameras: {
    total: number;
    online: number;
    offline: number;
    error: number;
    recording: number;
  };
  alerts: {
    total_today: number;
    active: number;
    critical: number;
    high: number;
    medium: number;
    low: number;
    trend_percentage: number;
  };
  analytics: {
    total_people_today: number;
    current_occupancy: number;
    peak_occupancy_today: number;
    total_vehicles_today: number;
    face_recognitions_today: number;
    avg_dwell_time_seconds: number;
  };
  system: {
    cpu_usage: number;
    memory_usage: number;
    gpu_usage?: number;
    disk_usage: number;
    uptime_seconds: number;
    active_streams: number;
    processing_fps: number;
  };
  recent_alerts: Array<{
    id: string;
    title: string;
    severity: string;
    camera_name: string;
    created_at: string;
  }>;
  alert_trend: Array<{
    hour: string;
    count: number;
  }>;
  occupancy_trend: Array<{
    hour: string;
    count: number;
  }>;
}

// ─── Attendance ─────────────────────────────────────────────────────────────

export interface AttendanceRecord {
  person_id: string;
  person_name: string;
  date: string;
  first_seen: string;
  last_seen: string;
  total_duration_seconds: number;
  camera_id: string;
  camera_name: string;
  face_thumbnail_url?: string;
  status: "present" | "late" | "absent" | "left_early";
}

export interface AttendanceSummary {
  date: string;
  total_expected: number;
  total_present: number;
  total_late: number;
  total_absent: number;
  attendance_rate: number;
  records: AttendanceRecord[];
}

export interface AttendanceFilters {
  date_from: string;
  date_to: string;
  person_id?: string;
  group?: string;
  status?: string;
  camera_id?: string;
}

// ─── PPE Compliance ─────────────────────────────────────────────────────────

export interface PPEComplianceData {
  camera_id: string;
  camera_name: string;
  timestamp: string;
  interval: "hour" | "day" | "week";
  total_checks: number;
  compliant: number;
  non_compliant: number;
  compliance_rate: number;
  violations: PPEViolationBreakdown;
}

export interface PPEViolationBreakdown {
  no_helmet: number;
  no_vest: number;
  no_goggles: number;
  no_gloves: number;
  no_mask: number;
  other: number;
}

// ─── Pattern / Behavioral Analytics ─────────────────────────────────────────

export interface PatternData {
  camera_id: string;
  camera_name: string;
  pattern_type: "trajectory" | "speed" | "direction" | "grouping" | "anomaly";
  timestamp: string;
  description: string;
  confidence: number;
  metadata: Record<string, unknown>;
  visualization_data?: unknown;
}

// ─── Analytics Query ────────────────────────────────────────────────────────

export interface AnalyticsQuery {
  type: "footfall" | "heatmap" | "dwell_time" | "emotion" | "attendance" | "ppe" | "pattern";
  camera_ids?: string[];
  zone_ids?: string[];
  date_from: string;
  date_to: string;
  interval?: "minute" | "hour" | "day" | "week" | "month";
  group_by?: string;
  filters?: Record<string, unknown>;
}

export interface AnalyticsExportRequest {
  query: AnalyticsQuery;
  format: "csv" | "xlsx" | "pdf" | "json";
  include_charts?: boolean;
}
