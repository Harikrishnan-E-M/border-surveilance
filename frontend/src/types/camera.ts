import type {
  Camera,
  CameraHealth,
  CameraProtocol,
  CameraStatus,
  Zone,
  Rule,
  BoundingBox,
} from "./api";

// ─── Camera Grid & Layout ───────────────────────────────────────────────────

export type GridLayout = "1x1" | "2x2" | "3x3" | "4x4";

export interface CameraGridItem {
  camera: Camera;
  position: number;
  isSelected: boolean;
  isFullscreen: boolean;
}

export interface CameraGridConfig {
  layout: GridLayout;
  cameras: CameraGridItem[];
}

// ─── Camera Stream ──────────────────────────────────────────────────────────

export type StreamType = "live" | "playback" | "snapshot";
export type StreamProtocol = "hls" | "webrtc" | "mjpeg" | "websocket";

export interface StreamConfig {
  cameraId: string;
  type: StreamType;
  protocol: StreamProtocol;
  url: string;
  lowLatency?: boolean;
  autoplay?: boolean;
  muted?: boolean;
}

export interface StreamStats {
  fps: number;
  bitrate_kbps: number;
  resolution: string;
  latency_ms: number;
  buffered_seconds: number;
  dropped_frames: number;
}

// ─── Camera PTZ (Pan-Tilt-Zoom) ─────────────────────────────────────────────

export interface PTZCapabilities {
  can_pan: boolean;
  can_tilt: boolean;
  can_zoom: boolean;
  can_focus: boolean;
  has_presets: boolean;
  has_patrol: boolean;
}

export interface PTZCommand {
  action: "pan" | "tilt" | "zoom" | "stop" | "preset" | "home";
  direction?: "left" | "right" | "up" | "down" | "in" | "out";
  speed?: number; // 0-1
  preset_id?: number;
}

export interface PTZPreset {
  id: number;
  name: string;
  thumbnail_url?: string;
}

// ─── Camera Configuration ───────────────────────────────────────────────────

export interface CameraCreateRequest {
  name: string;
  description?: string;
  location?: string;
  stream_url: string;
  protocol: CameraProtocol;
  tags?: string[];
}

export interface CameraUpdateRequest {
  name?: string;
  description?: string;
  location?: string;
  stream_url?: string;
  protocol?: CameraProtocol;
  is_recording?: boolean;
  tags?: string[];
}

export interface CameraTestResult {
  success: boolean;
  message: string;
  latency_ms?: number;
  resolution?: string;
  codec?: string;
  fps?: number;
}

// ─── Zone Configuration ─────────────────────────────────────────────────────

export interface ZoneCreateRequest {
  camera_id: string;
  name: string;
  description?: string;
  polygon: Array<{ x: number; y: number }>;
  color?: string;
}

export interface ZoneUpdateRequest {
  name?: string;
  description?: string;
  polygon?: Array<{ x: number; y: number }>;
  color?: string;
  is_active?: boolean;
}

// ─── Detection Overlay ──────────────────────────────────────────────────────

export interface Detection {
  id: string;
  class_name: string;
  confidence: number;
  bounding_box: BoundingBox;
  tracking_id?: string;
  color?: string;
  attributes?: Record<string, unknown>;
}

export interface DetectionFrame {
  camera_id: string;
  frame_number: number;
  timestamp: string;
  detections: Detection[];
}

// ─── Camera Filters ─────────────────────────────────────────────────────────

export interface CameraFilters {
  search?: string;
  status?: CameraStatus[];
  protocol?: CameraProtocol[];
  is_recording?: boolean;
  tags?: string[];
  location?: string;
  sort_by?: "name" | "status" | "created_at" | "updated_at";
  sort_order?: "asc" | "desc";
  page?: number;
  page_size?: number;
}

// ─── Re-exports for convenience ─────────────────────────────────────────────

export type { Camera, CameraHealth, CameraProtocol, CameraStatus, Zone, Rule, BoundingBox };
