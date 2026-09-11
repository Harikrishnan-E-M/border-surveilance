import { create } from "zustand";
import type { Camera, CameraHealth, PaginatedResponse } from "@/types/api";
import type { GridLayout, CameraFilters, CameraGridItem } from "@/types/camera";
import { api } from "@/lib/api-client";

// ─── State Interface ────────────────────────────────────────────────────────

export interface CameraState {
  /** Array of cameras from the API. */
  cameras: Camera[];

  /** Total count of cameras matching filters. */
  totalCount: number;

  /** Currently selected camera for detail view. */
  selectedCamera: Camera | null;

  /** Current camera grid layout. */
  gridLayout: GridLayout;

  /** Cameras arranged in the grid view. */
  gridCameras: CameraGridItem[];

  /** Camera health data indexed by camera ID. */
  healthData: Record<string, CameraHealth>;

  /** Current filter configuration. */
  filters: CameraFilters;

  /** Whether cameras are currently being fetched. */
  isLoading: boolean;

  /** Error from the last operation. */
  error: string | null;
}

export interface CameraActions {
  /**
   * Fetch cameras from the API with optional filters.
   */
  fetchCameras: (filters?: Partial<CameraFilters>) => Promise<void>;

  /**
   * Fetch a single camera by ID.
   */
  fetchCamera: (cameraId: string) => Promise<Camera>;

  /**
   * Fetch health data for all cameras or a specific camera.
   */
  fetchHealth: (cameraId?: string) => Promise<void>;

  /**
   * Set the selected camera for detail view.
   */
  selectCamera: (camera: Camera | null) => void;

  /**
   * Set the grid layout and rearrange cameras.
   */
  setGridLayout: (layout: GridLayout) => void;

  /**
   * Update cameras in the grid view.
   */
  setGridCameras: (cameras: CameraGridItem[]) => void;

  /**
   * Update filters and re-fetch.
   */
  setFilters: (filters: Partial<CameraFilters>) => void;

  /**
   * Reset filters to defaults.
   */
  resetFilters: () => void;

  /**
   * Update a camera's status locally (from WebSocket events).
   */
  updateCameraStatus: (
    cameraId: string,
    status: Camera["status"],
    health?: CameraHealth
  ) => void;

  /**
   * Clear error state.
   */
  clearError: () => void;
}

export type CameraStore = CameraState & CameraActions;

// ─── Default Filters ────────────────────────────────────────────────────────

const DEFAULT_FILTERS: CameraFilters = {
  sort_by: "name",
  sort_order: "asc",
  page: 1,
  page_size: 50,
};

// ─── Helpers ────────────────────────────────────────────────────────────────

/**
 * Calculate maximum number of cameras for a given grid layout.
 */
function getGridCapacity(layout: GridLayout): number {
  switch (layout) {
    case "1x1":
      return 1;
    case "2x2":
      return 4;
    case "3x3":
      return 9;
    case "4x4":
      return 16;
    default:
      return 4;
  }
}

/**
 * Build grid camera items from cameras list for a given layout.
 */
function buildGridCameras(cameras: Camera[], layout: GridLayout): CameraGridItem[] {
  const capacity = getGridCapacity(layout);
  return cameras.slice(0, capacity).map((camera, index) => ({
    camera,
    position: index,
    isSelected: false,
    isFullscreen: false,
  }));
}

// ─── Store Implementation ───────────────────────────────────────────────────

export const useCameraStore = create<CameraStore>()((set, get) => ({
  // ── State ────────────────────────────────────────────────────────────
  cameras: [],
  totalCount: 0,
  selectedCamera: null,
  gridLayout: "2x2",
  gridCameras: [],
  healthData: {},
  filters: { ...DEFAULT_FILTERS },
  isLoading: false,
  error: null,

  // ── Actions ──────────────────────────────────────────────────────────

  fetchCameras: async (filters?: Partial<CameraFilters>) => {
    const currentFilters = { ...get().filters, ...filters };
    set({ isLoading: true, error: null, filters: currentFilters });

    try {
      const response = await api.getRaw<any>("/api/v1/cameras", {
        params: currentFilters,
      });

      // After interceptor unwrap, response.data is the cameras array directly
      const raw = response.data;
      const cameras: Camera[] = Array.isArray(raw) ? raw : (raw?.data ?? []);
      const totalCount = Array.isArray(raw) ? raw.length : (raw?.total ?? raw?.data?.length ?? 0);
      const gridCameras = buildGridCameras(cameras, get().gridLayout);

      set({
        cameras,
        totalCount,
        gridCameras,
        isLoading: false,
      });
    } catch (err: unknown) {
      const message =
        err && typeof err === "object" && "message" in err
          ? (err as { message: string }).message
          : "Failed to fetch cameras";

      set({ isLoading: false, error: message });
    }
  },

  fetchCamera: async (cameraId: string) => {
    try {
      const camera = await api.get<Camera>(`/api/v1/cameras/${cameraId}`);
      set({ selectedCamera: camera });

      // Also update in the cameras array if present
      set((state) => ({
        cameras: state.cameras.map((c) => (c.id === cameraId ? camera : c)),
      }));

      return camera;
    } catch (err: unknown) {
      const message =
        err && typeof err === "object" && "message" in err
          ? (err as { message: string }).message
          : "Failed to fetch camera";

      set({ error: message });
      throw err;
    }
  },

  fetchHealth: async (cameraId?: string) => {
    try {
      if (cameraId) {
        const health = await api.get<CameraHealth>(`/api/v1/cameras/${cameraId}/health`);
        set((state) => ({
          healthData: { ...state.healthData, [cameraId]: health },
        }));
      } else {
        const healthList = await api.get<CameraHealth[]>("/api/v1/cameras/health");
        const healthMap: Record<string, CameraHealth> = {};
        healthList.forEach((h) => {
          healthMap[h.camera_id] = h;
        });
        set({ healthData: healthMap });
      }
    } catch {
      // Silently fail for health checks
    }
  },

  selectCamera: (camera: Camera | null) => {
    set({ selectedCamera: camera });
  },

  setGridLayout: (layout: GridLayout) => {
    const gridCameras = buildGridCameras(get().cameras, layout);
    set({ gridLayout: layout, gridCameras });
  },

  setGridCameras: (cameras: CameraGridItem[]) => {
    set({ gridCameras: cameras });
  },

  setFilters: (filters: Partial<CameraFilters>) => {
    const newFilters = { ...get().filters, ...filters, page: filters.page || 1 };
    set({ filters: newFilters });
    get().fetchCameras(newFilters);
  },

  resetFilters: () => {
    set({ filters: { ...DEFAULT_FILTERS } });
    get().fetchCameras(DEFAULT_FILTERS);
  },

  updateCameraStatus: (
    cameraId: string,
    status: Camera["status"],
    health?: CameraHealth
  ) => {
    set((state) => {
      const cameras = state.cameras.map((c) =>
        c.id === cameraId ? { ...c, status } : c
      );

      const gridCameras = state.gridCameras.map((item) =>
        item.camera.id === cameraId
          ? { ...item, camera: { ...item.camera, status } }
          : item
      );

      const selectedCamera =
        state.selectedCamera?.id === cameraId
          ? { ...state.selectedCamera, status }
          : state.selectedCamera;

      const healthData = health
        ? { ...state.healthData, [cameraId]: health }
        : state.healthData;

      return { cameras, gridCameras, selectedCamera, healthData };
    });
  },

  clearError: () => {
    set({ error: null });
  },
}));
