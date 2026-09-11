// ─── Generic API Response Types ─────────────────────────────────────────────

/**
 * Standard API response wrapper.
 */
export interface ApiResponse<T> {
  data: T;
  message?: string;
  success: boolean;
}

/**
 * Paginated API response.
 */
export interface PaginatedResponse<T> {
  data: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  has_next: boolean;
  has_previous: boolean;
}

export interface ErrorDetailItem {
  field?: string;
  message: string;
  type?: string;
}

/**
 * Standard error response from the API.
 */
export interface ErrorResponse {
  message: string;
  detail?: string;
  status: number;
  errors?: Record<string, string[]> | ErrorDetailItem[];
}

// ─── Enums (as string unions for TypeScript) ────────────────────────────────

export type UserRole = "admin" | "operator" | "viewer" | "api_user";

export type AlertSeverity = "critical" | "high" | "medium" | "low" | "info";

export type AlertStatus = "active" | "acknowledged" | "resolved" | "dismissed";

export type RuleType =
  | "intrusion_detection"
  | "loitering"
  | "line_crossing"
  | "object_left"
  | "object_removed"
  | "crowd_detection"
  | "face_recognition"
  | "license_plate"
  | "ppe_violation"
  | "fire_smoke"
  | "fall_detection"
  | "tailgating"
  | "wrong_direction"
  | "speed_violation";

export type CameraProtocol = "rtsp" | "rtmp" | "http" | "https" | "onvif" | "usb";

export type CameraStatus = "online" | "offline" | "error" | "maintenance" | "configuring";

export type RecordingStatus = "recording" | "completed" | "failed" | "archived";

export type RecordingType = "continuous" | "event" | "manual" | "scheduled";

// ─── User Types ─────────────────────────────────────────────────────────────

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
  avatar_url?: string;
  last_login?: string;
  created_at: string;
  updated_at: string;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user: User;
}

// ─── Camera Types ───────────────────────────────────────────────────────────

export interface Camera {
  id: string;
  name: string;
  description?: string;
  location?: string;
  stream_url: string;
  protocol: CameraProtocol;
  status: CameraStatus;
  is_recording: boolean;
  fps?: number;
  resolution_width?: number;
  resolution_height?: number;
  thumbnail_url?: string;
  zones: Zone[];
  rules: Rule[];
  health?: CameraHealth;
  tags?: string[];
  created_at: string;
  updated_at: string;
}

export interface CameraHealth {
  camera_id: string;
  is_online: boolean;
  cpu_usage?: number;
  memory_usage?: number;
  bandwidth_kbps?: number;
  frame_drop_rate?: number;
  latency_ms?: number;
  uptime_seconds?: number;
  last_frame_at?: string;
  error_message?: string;
  checked_at: string;
}

export interface Zone {
  id: string;
  camera_id: string;
  name: string;
  description?: string;
  polygon: Array<{ x: number; y: number }>;
  color: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface Rule {
  id: string;
  camera_id: string;
  zone_id?: string;
  name: string;
  description?: string;
  type: RuleType;
  is_active: boolean;
  severity: AlertSeverity;
  config: Record<string, unknown>;
  schedule?: RuleSchedule;
  cooldown_seconds?: number;
  created_at: string;
  updated_at: string;
}

export interface RuleSchedule {
  days: number[]; // 0 = Sunday, 6 = Saturday
  start_time: string; // "HH:mm"
  end_time: string; // "HH:mm"
  timezone: string;
}

// ─── Alert Types ────────────────────────────────────────────────────────────

export interface Alert {
  id: string;
  camera_id: string;
  camera_name: string;
  rule_id?: string;
  rule_name?: string;
  zone_id?: string;
  zone_name?: string;
  type: RuleType;
  severity: AlertSeverity;
  status: AlertStatus;
  title: string;
  description?: string;
  thumbnail_url?: string;
  snapshot_url?: string;
  video_clip_url?: string;
  metadata: Record<string, unknown>;
  acknowledged_by?: string;
  acknowledged_at?: string;
  resolved_by?: string;
  resolved_at?: string;
  created_at: string;
  updated_at: string;
}

// ─── Person / Face Types ────────────────────────────────────────────────────

export interface Person {
  id: string;
  name: string;
  description?: string;
  group?: string;
  face_images: string[];
  is_watchlisted: boolean;
  metadata?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface FaceEvent {
  id: string;
  camera_id: string;
  camera_name: string;
  person_id?: string;
  person_name?: string;
  confidence: number;
  face_image_url: string;
  snapshot_url: string;
  bounding_box: BoundingBox;
  is_known: boolean;
  created_at: string;
}

export interface BoundingBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

// ─── Vehicle Types ──────────────────────────────────────────────────────────

export interface Vehicle {
  id: string;
  license_plate: string;
  plate_region?: string;
  vehicle_type?: string;
  color?: string;
  make?: string;
  model?: string;
  is_watchlisted: boolean;
  owner_name?: string;
  notes?: string;
  created_at: string;
  updated_at: string;
}

export interface VehicleEvent {
  id: string;
  camera_id: string;
  camera_name: string;
  vehicle_id?: string;
  license_plate: string;
  plate_confidence: number;
  vehicle_type?: string;
  color?: string;
  speed_kmh?: number;
  direction?: string;
  plate_image_url: string;
  snapshot_url: string;
  bounding_box: BoundingBox;
  created_at: string;
}

// ─── Recording Types ────────────────────────────────────────────────────────

export interface Recording {
  id: string;
  camera_id: string;
  camera_name: string;
  type: RecordingType;
  status: RecordingStatus;
  start_time: string;
  end_time?: string;
  duration_seconds?: number;
  file_path?: string;
  file_size_bytes?: number;
  thumbnail_url?: string;
  stream_url?: string;
  alert_id?: string;
  created_at: string;
}

// ─── Report Types ───────────────────────────────────────────────────────────

export interface Report {
  id: string;
  name: string;
  description?: string;
  type: string;
  format: "pdf" | "csv" | "xlsx" | "json";
  status: "pending" | "generating" | "completed" | "failed";
  parameters: Record<string, unknown>;
  file_url?: string;
  file_size_bytes?: number;
  generated_by: string;
  generated_at?: string;
  created_at: string;
}
