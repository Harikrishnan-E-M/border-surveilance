'use client';

import { useState, useRef, useCallback, useEffect, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { format, formatDistanceToNow } from 'date-fns';
import {
  Loader2,
  Upload,
  Plus,
  Trash2,
  Camera,
  Map,
  Layers,
  Users,
  AlertTriangle,
  Clock,
  Maximize2,
  Minimize2,
  ZoomIn,
  ZoomOut,
  RotateCw,
  Move,
  Eye,
  EyeOff,
  ChevronLeft,
  ChevronRight,
  Building,
  Crosshair,
  Activity,
  BarChart3,
  RefreshCw,
  Settings,
  MousePointer,
  Gauge,
} from 'lucide-react';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { Separator } from '@/components/ui/separator';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import api from '@/lib/api-client';
import { cn, formatDuration } from '@/lib/utils';
import { useWebSocket } from '@/hooks/use-websocket';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
} from 'recharts';

// -------------------------------------------------------------------
// Types
// -------------------------------------------------------------------

interface FloorPlanListItem {
  id: string;
  org_id: string;
  name: string;
  building_name: string | null;
  floor_number: number;
  image_url: string;
  width_px: number;
  height_px: number;
  is_active: boolean;
  camera_count: number;
  zone_count: number;
  created_at: string | null;
  updated_at: string | null;
}

interface CameraPlacement {
  id: string;
  floor_plan_id: string;
  camera_id: string;
  x_position: number;
  y_position: number;
  rotation_degrees: number;
  fov_angle: number;
  fov_range: number;
  label: string | null;
  camera_name: string | null;
  camera_is_online: boolean | null;
  created_at: string | null;
  updated_at: string | null;
}

interface ZonePlacement {
  id: string;
  floor_plan_id: string;
  zone_id: string;
  polygon_points: Array<{ x: number; y: number }>;
  color: string;
  opacity: number;
  zone_name: string | null;
  zone_type: string | null;
  created_at: string | null;
  updated_at: string | null;
}

interface Overlay {
  id: string;
  floor_plan_id: string;
  overlay_type: string;
  image_url: string;
  generated_at: string | null;
  valid_until: string | null;
}

interface FloorPlanDetail {
  id: string;
  org_id: string;
  name: string;
  building_name: string | null;
  floor_number: number;
  image_url: string;
  width_px: number;
  height_px: number;
  scale_meters_per_pixel: number | null;
  metadata_json: Record<string, unknown> | null;
  is_active: boolean;
  camera_placements: CameraPlacement[];
  zone_placements: ZonePlacement[];
  overlays: Overlay[];
  created_at: string | null;
  updated_at: string | null;
}

interface LiveStatusData {
  cameras: Array<{
    camera_id: string;
    placement_id: string;
    is_online: boolean;
    person_count: number;
    active_alerts: number;
  }>;
  zones: Array<{
    zone_id: string;
    placement_id: string;
    person_count: number;
    capacity: number | null;
    dwell_avg_seconds: number;
    alert_count: number;
    occupancy_pct: number;
  }>;
  total_persons: number;
  total_alerts: number;
  timestamp: string;
}

interface AvailableCamera {
  id: string;
  name: string;
  is_online: boolean;
}

// -------------------------------------------------------------------
// Constants
// -------------------------------------------------------------------

const ZONE_COLORS = [
  '#3B82F6', '#10B981', '#F59E0B', '#EF4444',
  '#8B5CF6', '#EC4899', '#06B6D4', '#F97316',
];

const HEATMAP_GRADIENT = [
  'rgba(0, 0, 255, 0)',
  'rgba(0, 0, 255, 0.3)',
  'rgba(0, 255, 0, 0.5)',
  'rgba(255, 255, 0, 0.7)',
  'rgba(255, 0, 0, 0.9)',
];

const PIE_COLORS = ['#10B981', '#F59E0B', '#EF4444', '#6B7280'];

// -------------------------------------------------------------------
// Helper Components
// -------------------------------------------------------------------

function StatCard({
  icon: Icon,
  label,
  value,
  subValue,
  color = 'text-foreground',
}: {
  icon: React.ElementType;
  label: string;
  value: string | number;
  subValue?: string;
  color?: string;
}) {
  return (
    <div className="flex items-center gap-3 p-3 rounded-lg bg-muted/50">
      <div className={cn('p-2 rounded-md bg-background', color)}>
        <Icon className="h-4 w-4" />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-xs text-muted-foreground truncate">{label}</p>
        <p className="text-sm font-semibold">{value}</p>
        {subValue && (
          <p className="text-xs text-muted-foreground">{subValue}</p>
        )}
      </div>
    </div>
  );
}

// -------------------------------------------------------------------
// Floor Plan Canvas Component
// -------------------------------------------------------------------

function FloorPlanCanvas({
  floorPlan,
  liveData,
  showZones,
  showCameras,
  showHeatmap,
  showAlerts,
  selectedCameraPlacement,
  onCameraPlacementSelect,
  onCameraDrop,
  isDraggingCamera,
  zoom,
  panOffset,
  onZoomChange,
  onPanChange,
  containerRef,
}: {
  floorPlan: FloorPlanDetail;
  liveData: LiveStatusData | null;
  showZones: boolean;
  showCameras: boolean;
  showHeatmap: boolean;
  showAlerts: boolean;
  selectedCameraPlacement: string | null;
  onCameraPlacementSelect: (id: string | null) => void;
  onCameraDrop: (x: number, y: number) => void;
  isDraggingCamera: boolean;
  zoom: number;
  panOffset: { x: number; y: number };
  onZoomChange: (z: number) => void;
  onPanChange: (p: { x: number; y: number }) => void;
  containerRef: React.RefObject<HTMLDivElement | null>;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const [imageLoaded, setImageLoaded] = useState(false);
  const [isPanning, setIsPanning] = useState(false);
  const [panStart, setPanStart] = useState({ x: 0, y: 0 });

  // Load floor plan image
  useEffect(() => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => {
      imageRef.current = img;
      setImageLoaded(true);
    };
    img.onerror = () => {
      setImageLoaded(false);
    };
    img.src = floorPlan.image_url;
  }, [floorPlan.image_url]);

  // Draw canvas
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    const img = imageRef.current;
    if (!canvas || !ctx || !img || !imageLoaded) return;

    const container = containerRef.current;
    if (!container) return;

    canvas.width = container.clientWidth;
    canvas.height = container.clientHeight;

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.save();

    // Apply zoom and pan
    ctx.translate(panOffset.x, panOffset.y);
    ctx.scale(zoom, zoom);

    // Calculate image fit
    const scaleX = canvas.width / img.width;
    const scaleY = canvas.height / img.height;
    const fitScale = Math.min(scaleX, scaleY) / zoom;
    const imgW = img.width * fitScale;
    const imgH = img.height * fitScale;
    const imgX = (canvas.width / zoom - imgW) / 2;
    const imgY = (canvas.height / zoom - imgH) / 2;

    // Draw floor plan image
    ctx.drawImage(img, imgX, imgY, imgW, imgH);

    // Draw zone placements
    if (showZones && floorPlan.zone_placements.length > 0) {
      floorPlan.zone_placements.forEach((zp) => {
        if (!zp.polygon_points || zp.polygon_points.length < 3) return;

        const zoneLive = liveData?.zones.find(
          (z) => z.placement_id === zp.id
        );

        ctx.beginPath();
        const firstPt = zp.polygon_points[0];
        ctx.moveTo(imgX + firstPt.x * imgW, imgY + firstPt.y * imgH);
        zp.polygon_points.slice(1).forEach((pt) => {
          ctx.lineTo(imgX + pt.x * imgW, imgY + pt.y * imgH);
        });
        ctx.closePath();

        // Fill with zone color
        const baseColor = zp.color || '#3B82F6';
        const alertActive = showAlerts && (zoneLive?.alert_count || 0) > 0;
        ctx.fillStyle = alertActive
          ? `rgba(239, 68, 68, ${zp.opacity + 0.1})`
          : hexToRgba(baseColor, zp.opacity);
        ctx.fill();

        // Stroke
        ctx.strokeStyle = alertActive ? '#EF4444' : baseColor;
        ctx.lineWidth = alertActive ? 2 / zoom : 1 / zoom;
        ctx.stroke();

        // Zone label
        const centroidX =
          imgX +
          (zp.polygon_points.reduce((s, p) => s + p.x, 0) /
            zp.polygon_points.length) *
            imgW;
        const centroidY =
          imgY +
          (zp.polygon_points.reduce((s, p) => s + p.y, 0) /
            zp.polygon_points.length) *
            imgH;

        ctx.font = `${Math.max(10, 12 / zoom)}px system-ui, sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';

        // Draw label background
        const labelText = zp.zone_name || 'Zone';
        const countText = zoneLive
          ? `${zoneLive.person_count} people`
          : '';
        const fullLabel = countText
          ? `${labelText} (${countText})`
          : labelText;
        const textMetrics = ctx.measureText(fullLabel);
        const padding = 4 / zoom;

        ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
        ctx.fillRect(
          centroidX - textMetrics.width / 2 - padding,
          centroidY - 8 / zoom - padding,
          textMetrics.width + padding * 2,
          16 / zoom + padding * 2
        );

        ctx.fillStyle = '#ffffff';
        ctx.fillText(fullLabel, centroidX, centroidY);
      });
    }

    // Draw heatmap overlay
    if (showHeatmap && liveData) {
      liveData.zones.forEach((zone) => {
        const zp = floorPlan.zone_placements.find(
          (z) => z.id === zone.placement_id
        );
        if (!zp || !zp.polygon_points || zp.polygon_points.length < 3)
          return;

        const intensity = Math.min(zone.occupancy_pct / 100, 1.0);
        const r = Math.round(255 * intensity);
        const g = Math.round(255 * (1 - intensity));
        const heatColor = `rgba(${r}, ${g}, 0, ${0.15 + intensity * 0.35})`;

        ctx.beginPath();
        const firstPt = zp.polygon_points[0];
        ctx.moveTo(imgX + firstPt.x * imgW, imgY + firstPt.y * imgH);
        zp.polygon_points.slice(1).forEach((pt) => {
          ctx.lineTo(imgX + pt.x * imgW, imgY + pt.y * imgH);
        });
        ctx.closePath();
        ctx.fillStyle = heatColor;
        ctx.fill();
      });
    }

    // Draw camera placements
    if (showCameras && floorPlan.camera_placements.length > 0) {
      floorPlan.camera_placements.forEach((cp) => {
        const cx = imgX + cp.x_position * imgW;
        const cy = imgY + cp.y_position * imgH;
        const isSelected = selectedCameraPlacement === cp.id;

        const cameraLive = liveData?.cameras.find(
          (c) => c.placement_id === cp.id
        );
        const isOnline =
          cameraLive?.is_online ?? cp.camera_is_online ?? false;

        // Draw FOV cone
        const fovHalf = ((cp.fov_angle || 90) / 2) * (Math.PI / 180);
        const rotRad = ((cp.rotation_degrees || 0) - 90) * (Math.PI / 180);
        const range = (cp.fov_range || 100) * fitScale;

        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.arc(
          cx,
          cy,
          range,
          rotRad - fovHalf,
          rotRad + fovHalf,
          false
        );
        ctx.closePath();

        const coneColor = isOnline
          ? 'rgba(16, 185, 129, 0.15)'
          : 'rgba(239, 68, 68, 0.1)';
        ctx.fillStyle = coneColor;
        ctx.fill();
        ctx.strokeStyle = isOnline
          ? 'rgba(16, 185, 129, 0.4)'
          : 'rgba(239, 68, 68, 0.3)';
        ctx.lineWidth = 1 / zoom;
        ctx.stroke();

        // Draw camera icon circle
        const radius = isSelected ? 10 / zoom : 8 / zoom;
        ctx.beginPath();
        ctx.arc(cx, cy, radius, 0, Math.PI * 2);
        ctx.fillStyle = isOnline ? '#10B981' : '#EF4444';
        ctx.fill();
        ctx.strokeStyle = isSelected ? '#ffffff' : 'rgba(0,0,0,0.3)';
        ctx.lineWidth = isSelected ? 2 / zoom : 1 / zoom;
        ctx.stroke();

        // Camera icon inner
        ctx.fillStyle = '#ffffff';
        ctx.font = `bold ${Math.max(8, 10 / zoom)}px system-ui`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('C', cx, cy);

        // Camera label
        const labelText = cp.label || cp.camera_name || 'Camera';
        ctx.font = `${Math.max(9, 10 / zoom)}px system-ui, sans-serif`;
        ctx.fillStyle = 'rgba(0, 0, 0, 0.8)';
        const labelMetrics = ctx.measureText(labelText);
        ctx.fillRect(
          cx - labelMetrics.width / 2 - 2 / zoom,
          cy + radius + 2 / zoom,
          labelMetrics.width + 4 / zoom,
          12 / zoom
        );
        ctx.fillStyle = '#ffffff';
        ctx.fillText(labelText, cx, cy + radius + 8 / zoom);

        // Person count badge
        if (cameraLive && cameraLive.person_count > 0) {
          const badgeX = cx + radius;
          const badgeY = cy - radius;
          const badgeRadius = 7 / zoom;
          ctx.beginPath();
          ctx.arc(badgeX, badgeY, badgeRadius, 0, Math.PI * 2);
          ctx.fillStyle = '#3B82F6';
          ctx.fill();
          ctx.fillStyle = '#ffffff';
          ctx.font = `bold ${Math.max(7, 8 / zoom)}px system-ui`;
          ctx.fillText(
            String(cameraLive.person_count),
            badgeX,
            badgeY
          );
        }

        // Alert indicator
        if (showAlerts && cameraLive && cameraLive.active_alerts > 0) {
          const alertX = cx - radius;
          const alertY = cy - radius;
          const alertRadius = 5 / zoom;
          ctx.beginPath();
          ctx.arc(alertX, alertY, alertRadius, 0, Math.PI * 2);
          ctx.fillStyle = '#EF4444';
          ctx.fill();
        }
      });
    }

    ctx.restore();
  }, [
    imageLoaded,
    zoom,
    panOffset,
    floorPlan,
    liveData,
    showZones,
    showCameras,
    showHeatmap,
    showAlerts,
    selectedCameraPlacement,
    containerRef,
  ]);

  useEffect(() => {
    draw();
  }, [draw]);

  // Resize handler
  useEffect(() => {
    const observer = new ResizeObserver(() => draw());
    const container = containerRef.current;
    if (container) observer.observe(container);
    return () => observer.disconnect();
  }, [draw, containerRef]);

  // Mouse handlers
  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (isDraggingCamera) return;
      setIsPanning(true);
      setPanStart({ x: e.clientX - panOffset.x, y: e.clientY - panOffset.y });
    },
    [isDraggingCamera, panOffset]
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (!isPanning) return;
      onPanChange({
        x: e.clientX - panStart.x,
        y: e.clientY - panStart.y,
      });
    },
    [isPanning, panStart, onPanChange]
  );

  const handleMouseUp = useCallback(() => {
    setIsPanning(false);
  }, []);

  const handleWheel = useCallback(
    (e: React.WheelEvent) => {
      e.preventDefault();
      const delta = e.deltaY > 0 ? -0.1 : 0.1;
      onZoomChange(Math.max(0.2, Math.min(5, zoom + delta)));
    },
    [zoom, onZoomChange]
  );

  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      const canvas = canvasRef.current;
      const img = imageRef.current;
      const container = containerRef.current;
      if (!canvas || !img || !container) return;

      const rect = canvas.getBoundingClientRect();
      const clickX = e.clientX - rect.left;
      const clickY = e.clientY - rect.top;

      // Transform click coords to image space
      const ix = (clickX - panOffset.x) / zoom;
      const iy = (clickY - panOffset.y) / zoom;

      const scaleX = canvas.width / img.width;
      const scaleY = canvas.height / img.height;
      const fitScale = Math.min(scaleX, scaleY) / zoom;
      const imgW = img.width * fitScale;
      const imgH = img.height * fitScale;
      const imgX = (canvas.width / zoom - imgW) / 2;
      const imgY = (canvas.height / zoom - imgH) / 2;

      // Relative position (0-1)
      const relX = (ix - imgX) / imgW;
      const relY = (iy - imgY) / imgH;

      if (isDraggingCamera && relX >= 0 && relX <= 1 && relY >= 0 && relY <= 1) {
        onCameraDrop(relX, relY);
        return;
      }

      // Check camera click
      let clickedCamera: string | null = null;
      const clickThreshold = 15 / zoom;
      for (const cp of floorPlan.camera_placements) {
        const cx = imgX + cp.x_position * imgW;
        const cy = imgY + cp.y_position * imgH;
        const dist = Math.sqrt((ix - cx) ** 2 + (iy - cy) ** 2);
        if (dist < clickThreshold) {
          clickedCamera = cp.id;
          break;
        }
      }
      onCameraPlacementSelect(clickedCamera);
    },
    [
      zoom,
      panOffset,
      isDraggingCamera,
      floorPlan.camera_placements,
      onCameraDrop,
      onCameraPlacementSelect,
      containerRef,
    ]
  );

  return (
    <canvas
      ref={canvasRef}
      className={cn(
        'w-full h-full',
        isDraggingCamera ? 'cursor-crosshair' : isPanning ? 'cursor-grabbing' : 'cursor-grab'
      )}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
      onWheel={handleWheel}
      onClick={handleClick}
    />
  );
}

// -------------------------------------------------------------------
// Mini-map Component
// -------------------------------------------------------------------

function MiniMap({
  floorPlan,
  zoom,
  panOffset,
}: {
  floorPlan: FloorPlanDetail;
  zoom: number;
  panOffset: { x: number; y: number };
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);

  useEffect(() => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => {
      imgRef.current = img;
      drawMiniMap();
    };
    img.src = floorPlan.image_url;
  }, [floorPlan.image_url]);

  const drawMiniMap = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    const img = imgRef.current;
    if (!canvas || !ctx || !img) return;

    const miniW = 180;
    const miniH = 120;
    canvas.width = miniW;
    canvas.height = miniH;

    ctx.clearRect(0, 0, miniW, miniH);

    // Draw scaled floor plan
    const scale = Math.min(miniW / img.width, miniH / img.height);
    const w = img.width * scale;
    const h = img.height * scale;
    const x = (miniW - w) / 2;
    const y = (miniH - h) / 2;

    ctx.drawImage(img, x, y, w, h);

    // Draw camera dots
    floorPlan.camera_placements.forEach((cp) => {
      ctx.beginPath();
      ctx.arc(x + cp.x_position * w, y + cp.y_position * h, 3, 0, Math.PI * 2);
      ctx.fillStyle = cp.camera_is_online ? '#10B981' : '#EF4444';
      ctx.fill();
    });

    // Draw viewport indicator
    const vpW = (miniW / zoom) * 0.8;
    const vpH = (miniH / zoom) * 0.8;
    const vpX = miniW / 2 - vpW / 2 - panOffset.x * scale / zoom;
    const vpY = miniH / 2 - vpH / 2 - panOffset.y * scale / zoom;

    ctx.strokeStyle = '#3B82F6';
    ctx.lineWidth = 1.5;
    ctx.strokeRect(vpX, vpY, vpW, vpH);
  }, [floorPlan, zoom, panOffset]);

  useEffect(() => {
    drawMiniMap();
  }, [drawMiniMap]);

  return (
    <div className="absolute bottom-4 right-4 bg-background/90 border rounded-lg shadow-md overflow-hidden">
      <canvas ref={canvasRef} width={180} height={120} />
    </div>
  );
}

// -------------------------------------------------------------------
// Utility
// -------------------------------------------------------------------

function hexToRgba(hex: string, alpha: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

// -------------------------------------------------------------------
// Main Page
// -------------------------------------------------------------------

export default function FloorPlanPage() {
  const queryClient = useQueryClient();
  const containerRef = useRef<HTMLDivElement>(null);

  // State
  const [selectedFloorPlanId, setSelectedFloorPlanId] = useState<string | null>(null);
  const [showUploadDialog, setShowUploadDialog] = useState(false);
  const [showCameraDialog, setShowCameraDialog] = useState(false);
  const [isDraggingCamera, setIsDraggingCamera] = useState(false);
  const [selectedCameraToPlace, setSelectedCameraToPlace] = useState<string | null>(null);
  const [selectedCameraPlacement, setSelectedCameraPlacement] = useState<string | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [showZones, setShowZones] = useState(true);
  const [showCameras, setShowCameras] = useState(true);
  const [showHeatmap, setShowHeatmap] = useState(false);
  const [showAlerts, setShowAlerts] = useState(true);
  const [zoom, setZoom] = useState(1);
  const [panOffset, setPanOffset] = useState({ x: 0, y: 0 });

  // Upload form state
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadName, setUploadName] = useState('');
  const [uploadBuilding, setUploadBuilding] = useState('');
  const [uploadFloor, setUploadFloor] = useState(0);
  const [uploadScale, setUploadScale] = useState('');

  // Camera placement form
  const [placementRotation, setPlacementRotation] = useState(0);
  const [placementFovAngle, setPlacementFovAngle] = useState(90);
  const [placementFovRange, setPlacementFovRange] = useState(100);

  // WebSocket for real-time updates
  const { subscribe } = useWebSocket('/floor-plans', {
    enabled: !!selectedFloorPlanId,
  });

  useEffect(() => {
    if (!selectedFloorPlanId) return;
    const unsub = subscribe('floor_plan.live_update', () => {
      queryClient.invalidateQueries({
        queryKey: ['floor-plan-live', selectedFloorPlanId],
      });
    });
    return unsub;
  }, [subscribe, selectedFloorPlanId, queryClient]);

  // ── Queries ────────────────────────────────────────────────────────

  const {
    data: floorPlans,
    isLoading: isLoadingList,
  } = useQuery<FloorPlanListItem[]>({
    queryKey: ['floor-plans'],
    queryFn: () => api.get('/api/v1/floor-plans'),
    staleTime: 30_000,
  });

  const {
    data: floorPlanDetail,
    isLoading: isLoadingDetail,
  } = useQuery<FloorPlanDetail>({
    queryKey: ['floor-plan', selectedFloorPlanId],
    queryFn: () => api.get(`/api/v1/floor-plans/${selectedFloorPlanId}`),
    enabled: !!selectedFloorPlanId,
    staleTime: 15_000,
  });

  const {
    data: liveData,
  } = useQuery<LiveStatusData>({
    queryKey: ['floor-plan-live', selectedFloorPlanId],
    queryFn: () => api.get(`/api/v1/floor-plans/${selectedFloorPlanId}/live`),
    enabled: !!selectedFloorPlanId,
    refetchInterval: 5_000,
    staleTime: 3_000,
  });

  const { data: availableCameras } = useQuery<AvailableCamera[]>({
    queryKey: ['cameras-list'],
    queryFn: async () => {
      const res = await api.getRaw<{ data: AvailableCamera[] }>('/api/v1/cameras', {
        params: { page_size: 100 },
      });
      return res.data.data || [];
    },
    staleTime: 60_000,
  });

  // Auto-select first floor plan
  useEffect(() => {
    if (floorPlans && floorPlans.length > 0 && !selectedFloorPlanId) {
      setSelectedFloorPlanId(floorPlans[0].id);
    }
  }, [floorPlans, selectedFloorPlanId]);

  // ── Mutations ──────────────────────────────────────────────────────

  const uploadMutation = useMutation({
    mutationFn: async () => {
      if (!uploadFile) throw new Error('No file selected');
      const formData = new FormData();
      formData.append('image', uploadFile);
      formData.append('name', uploadName);
      if (uploadBuilding) formData.append('building_name', uploadBuilding);
      formData.append('floor_number', String(uploadFloor));
      if (uploadScale) {
        formData.append('scale_meters_per_pixel', uploadScale);
      }
      return api.upload('/api/v1/floor-plans', formData);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['floor-plans'] });
      setShowUploadDialog(false);
      setUploadFile(null);
      setUploadName('');
      setUploadBuilding('');
      setUploadFloor(0);
      setUploadScale('');
    },
  });

  const placeCameraMutation = useMutation({
    mutationFn: async ({
      x,
      y,
    }: {
      x: number;
      y: number;
    }) => {
      if (!selectedFloorPlanId || !selectedCameraToPlace) return;
      return api.post(`/api/v1/floor-plans/${selectedFloorPlanId}/cameras`, {
        camera_id: selectedCameraToPlace,
        x_position: x,
        y_position: y,
        rotation_degrees: placementRotation,
        fov_angle: placementFovAngle,
        fov_range: placementFovRange,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ['floor-plan', selectedFloorPlanId],
      });
      setIsDraggingCamera(false);
      setSelectedCameraToPlace(null);
      setShowCameraDialog(false);
    },
  });

  const removeCameraPlacementMutation = useMutation({
    mutationFn: async (placementId: string) => {
      if (!selectedFloorPlanId) return;
      return api.delete(
        `/api/v1/floor-plans/${selectedFloorPlanId}/cameras/${placementId}`
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ['floor-plan', selectedFloorPlanId],
      });
      setSelectedCameraPlacement(null);
    },
  });

  const generateHeatmapMutation = useMutation({
    mutationFn: async () => {
      if (!selectedFloorPlanId) return;
      return api.post(`/api/v1/floor-plans/${selectedFloorPlanId}/heatmap`, {
        time_range: '1h',
        color_scheme: 'jet',
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ['floor-plan', selectedFloorPlanId],
      });
    },
  });

  // ── Handlers ───────────────────────────────────────────────────────

  const handleCameraDrop = useCallback(
    (x: number, y: number) => {
      placeCameraMutation.mutate({ x, y });
    },
    [placeCameraMutation]
  );

  const handleStartCameraPlacement = useCallback(
    (cameraId: string) => {
      setSelectedCameraToPlace(cameraId);
      setIsDraggingCamera(true);
      setShowCameraDialog(false);
    },
    []
  );

  const resetView = useCallback(() => {
    setZoom(1);
    setPanOffset({ x: 0, y: 0 });
  }, []);

  // ── Computed ───────────────────────────────────────────────────────

  const sortedFloorPlans = useMemo(() => {
    if (!floorPlans) return [];
    return [...floorPlans].sort(
      (a, b) => a.floor_number - b.floor_number
    );
  }, [floorPlans]);

  const groupedByBuilding = useMemo(() => {
    const groups: Record<string, FloorPlanListItem[]> = {};
    sortedFloorPlans.forEach((fp) => {
      const key = fp.building_name || 'Default Building';
      if (!groups[key]) groups[key] = [];
      groups[key].push(fp);
    });
    return groups;
  }, [sortedFloorPlans]);

  const placedCameraIds = useMemo(() => {
    if (!floorPlanDetail) return new Set<string>();
    return new Set(floorPlanDetail.camera_placements.map((cp) => cp.camera_id));
  }, [floorPlanDetail]);

  const unplacedCameras = useMemo(() => {
    if (!availableCameras) return [];
    return availableCameras.filter((c) => !placedCameraIds.has(c.id));
  }, [availableCameras, placedCameraIds]);

  const zoneStats = useMemo(() => {
    if (!liveData) return [];
    return liveData.zones.map((z) => {
      const zp = floorPlanDetail?.zone_placements.find(
        (zpl) => zpl.id === z.placement_id
      );
      return {
        name: zp?.zone_name || 'Zone',
        persons: z.person_count,
        capacity: z.capacity || 0,
        occupancy: z.occupancy_pct,
        alerts: z.alert_count,
        dwellAvg: z.dwell_avg_seconds,
      };
    });
  }, [liveData, floorPlanDetail]);

  const occupancyChartData = useMemo(() => {
    return zoneStats.map((z) => ({
      name: z.name.length > 12 ? z.name.slice(0, 12) + '...' : z.name,
      occupancy: z.occupancy,
      persons: z.persons,
    }));
  }, [zoneStats]);

  // ── Render ─────────────────────────────────────────────────────────

  if (isLoadingList) {
    return (
      <div className="p-6 space-y-4">
        <Skeleton className="h-8 w-64" />
        <div className="grid grid-cols-3 gap-4">
          <Skeleton className="h-[500px]" />
          <Skeleton className="h-[500px] col-span-2" />
        </div>
      </div>
    );
  }

  return (
    <TooltipProvider>
      <div className="flex flex-col h-[calc(100vh-4rem)]">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-3 border-b bg-background">
          <div className="flex items-center gap-3">
            <Map className="h-5 w-5 text-primary" />
            <h1 className="text-lg font-semibold">Floor Plan</h1>
            {floorPlanDetail && (
              <Badge variant="outline" className="text-xs">
                {floorPlanDetail.name}
                {floorPlanDetail.building_name &&
                  ` - ${floorPlanDetail.building_name}`}
                {' '}(Floor {floorPlanDetail.floor_number})
              </Badge>
            )}
          </div>

          <div className="flex items-center gap-2">
            {/* Floor selector */}
            {sortedFloorPlans.length > 0 && (
              <Select
                value={selectedFloorPlanId || ''}
                onValueChange={(v) => {
                  setSelectedFloorPlanId(v);
                  resetView();
                }}
              >
                <SelectTrigger className="w-[220px] h-8 text-xs">
                  <SelectValue placeholder="Select floor plan" />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(groupedByBuilding).map(
                    ([building, plans]) => (
                      <div key={building}>
                        <p className="px-2 py-1 text-xs text-muted-foreground font-medium">
                          {building}
                        </p>
                        {plans.map((fp) => (
                          <SelectItem key={fp.id} value={fp.id}>
                            <span className="flex items-center gap-2">
                              <Building className="h-3 w-3" />
                              {fp.name} (F{fp.floor_number})
                            </span>
                          </SelectItem>
                        ))}
                      </div>
                    )
                  )}
                </SelectContent>
              </Select>
            )}

            <Separator orientation="vertical" className="h-6" />

            {/* Zoom controls */}
            <div className="flex items-center gap-1">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7"
                    onClick={() => setZoom(Math.max(0.2, zoom - 0.2))}
                  >
                    <ZoomOut className="h-3.5 w-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Zoom Out</TooltipContent>
              </Tooltip>

              <span className="text-xs text-muted-foreground w-10 text-center">
                {Math.round(zoom * 100)}%
              </span>

              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7"
                    onClick={() => setZoom(Math.min(5, zoom + 0.2))}
                  >
                    <ZoomIn className="h-3.5 w-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Zoom In</TooltipContent>
              </Tooltip>

              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7"
                    onClick={resetView}
                  >
                    <Maximize2 className="h-3.5 w-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Reset View</TooltipContent>
              </Tooltip>
            </div>

            <Separator orientation="vertical" className="h-6" />

            {/* Layer toggles */}
            <div className="flex items-center gap-1">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant={showZones ? 'secondary' : 'ghost'}
                    size="icon"
                    className="h-7 w-7"
                    onClick={() => setShowZones(!showZones)}
                  >
                    <Layers className="h-3.5 w-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Toggle Zones</TooltipContent>
              </Tooltip>

              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant={showCameras ? 'secondary' : 'ghost'}
                    size="icon"
                    className="h-7 w-7"
                    onClick={() => setShowCameras(!showCameras)}
                  >
                    <Camera className="h-3.5 w-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Toggle Cameras</TooltipContent>
              </Tooltip>

              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant={showHeatmap ? 'secondary' : 'ghost'}
                    size="icon"
                    className="h-7 w-7"
                    onClick={() => setShowHeatmap(!showHeatmap)}
                  >
                    <Activity className="h-3.5 w-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Toggle Heatmap</TooltipContent>
              </Tooltip>

              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant={showAlerts ? 'secondary' : 'ghost'}
                    size="icon"
                    className="h-7 w-7"
                    onClick={() => setShowAlerts(!showAlerts)}
                  >
                    <AlertTriangle className="h-3.5 w-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Toggle Alerts</TooltipContent>
              </Tooltip>
            </div>

            <Separator orientation="vertical" className="h-6" />

            {/* Actions */}
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  onClick={() => setShowCameraDialog(true)}
                  disabled={!selectedFloorPlanId}
                >
                  <Crosshair className="h-3.5 w-3.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Place Camera</TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  onClick={() => generateHeatmapMutation.mutate()}
                  disabled={!selectedFloorPlanId || generateHeatmapMutation.isPending}
                >
                  {generateHeatmapMutation.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <BarChart3 className="h-3.5 w-3.5" />
                  )}
                </Button>
              </TooltipTrigger>
              <TooltipContent>Generate Heatmap</TooltipContent>
            </Tooltip>

            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs"
              onClick={() => setShowUploadDialog(true)}
            >
              <Upload className="h-3.5 w-3.5 mr-1" />
              Upload
            </Button>

            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
            >
              {sidebarCollapsed ? (
                <ChevronLeft className="h-3.5 w-3.5" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" />
              )}
            </Button>
          </div>
        </div>

        {/* Dragging indicator */}
        {isDraggingCamera && (
          <div className="bg-blue-500/10 border-b border-blue-500/30 px-6 py-2 flex items-center justify-between">
            <p className="text-sm text-blue-600 dark:text-blue-400">
              <MousePointer className="h-4 w-4 inline mr-2" />
              Click on the floor plan to place the camera
            </p>
            <Button
              variant="ghost"
              size="sm"
              className="h-6 text-xs"
              onClick={() => {
                setIsDraggingCamera(false);
                setSelectedCameraToPlace(null);
              }}
            >
              Cancel
            </Button>
          </div>
        )}

        {/* Main content */}
        <div className="flex flex-1 overflow-hidden">
          {/* Canvas area */}
          <div
            ref={containerRef}
            className="flex-1 relative bg-muted/30 overflow-hidden"
          >
            {isLoadingDetail ? (
              <div className="flex items-center justify-center h-full">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
              </div>
            ) : floorPlanDetail ? (
              <>
                <FloorPlanCanvas
                  floorPlan={floorPlanDetail}
                  liveData={liveData || null}
                  showZones={showZones}
                  showCameras={showCameras}
                  showHeatmap={showHeatmap}
                  showAlerts={showAlerts}
                  selectedCameraPlacement={selectedCameraPlacement}
                  onCameraPlacementSelect={setSelectedCameraPlacement}
                  onCameraDrop={handleCameraDrop}
                  isDraggingCamera={isDraggingCamera}
                  zoom={zoom}
                  panOffset={panOffset}
                  onZoomChange={setZoom}
                  onPanChange={setPanOffset}
                  containerRef={containerRef}
                />
                <MiniMap
                  floorPlan={floorPlanDetail}
                  zoom={zoom}
                  panOffset={panOffset}
                />

                {/* Live totals overlay */}
                {liveData && (
                  <div className="absolute top-4 left-4 flex gap-2">
                    <Badge variant="secondary" className="text-xs gap-1">
                      <Users className="h-3 w-3" />
                      {liveData.total_persons} people
                    </Badge>
                    {liveData.total_alerts > 0 && (
                      <Badge variant="destructive" className="text-xs gap-1">
                        <AlertTriangle className="h-3 w-3" />
                        {liveData.total_alerts} alerts
                      </Badge>
                    )}
                  </div>
                )}
              </>
            ) : (
              <div className="flex flex-col items-center justify-center h-full text-muted-foreground gap-4">
                <Map className="h-16 w-16 opacity-30" />
                <p className="text-sm">
                  {sortedFloorPlans.length === 0
                    ? 'No floor plans uploaded yet.'
                    : 'Select a floor plan to view.'}
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setShowUploadDialog(true)}
                >
                  <Upload className="h-4 w-4 mr-2" />
                  Upload Floor Plan
                </Button>
              </div>
            )}
          </div>

          {/* Right sidebar */}
          {!sidebarCollapsed && floorPlanDetail && (
            <div className="w-80 border-l bg-background flex flex-col">
              <ScrollArea className="flex-1">
                <div className="p-4 space-y-4">
                  {/* Real-time stats */}
                  <div>
                    <h3 className="text-sm font-semibold mb-3 flex items-center gap-2">
                      <Activity className="h-4 w-4" />
                      Real-time Stats
                    </h3>
                    <div className="space-y-2">
                      <StatCard
                        icon={Users}
                        label="Total People"
                        value={liveData?.total_persons ?? 0}
                        color="text-blue-500"
                      />
                      <StatCard
                        icon={AlertTriangle}
                        label="Active Alerts"
                        value={liveData?.total_alerts ?? 0}
                        color="text-red-500"
                      />
                      <StatCard
                        icon={Camera}
                        label="Cameras"
                        value={`${floorPlanDetail.camera_placements.filter((c) => c.camera_is_online).length}/${floorPlanDetail.camera_placements.length}`}
                        subValue="online / total"
                        color="text-green-500"
                      />
                      <StatCard
                        icon={Layers}
                        label="Zones"
                        value={floorPlanDetail.zone_placements.length}
                        color="text-purple-500"
                      />
                    </div>
                  </div>

                  <Separator />

                  {/* Per-zone breakdown */}
                  <div>
                    <h3 className="text-sm font-semibold mb-3 flex items-center gap-2">
                      <Gauge className="h-4 w-4" />
                      Zone Occupancy
                    </h3>
                    {zoneStats.length > 0 ? (
                      <div className="space-y-2">
                        {zoneStats.map((zone, idx) => (
                          <div
                            key={idx}
                            className="p-2.5 rounded-lg bg-muted/50 space-y-1.5"
                          >
                            <div className="flex items-center justify-between">
                              <span className="text-xs font-medium truncate">
                                {zone.name}
                              </span>
                              {zone.alerts > 0 && (
                                <Badge variant="destructive" className="text-[10px] h-4 px-1">
                                  {zone.alerts}
                                </Badge>
                              )}
                            </div>
                            <div className="flex items-center gap-2">
                              <div className="flex-1 bg-muted rounded-full h-1.5">
                                <div
                                  className={cn(
                                    'h-1.5 rounded-full transition-all',
                                    zone.occupancy > 90
                                      ? 'bg-red-500'
                                      : zone.occupancy > 70
                                        ? 'bg-yellow-500'
                                        : 'bg-green-500'
                                  )}
                                  style={{
                                    width: `${Math.min(100, zone.occupancy)}%`,
                                  }}
                                />
                              </div>
                              <span className="text-[10px] text-muted-foreground w-8 text-right">
                                {Math.round(zone.occupancy)}%
                              </span>
                            </div>
                            <div className="flex items-center justify-between text-[10px] text-muted-foreground">
                              <span>
                                <Users className="h-3 w-3 inline mr-0.5" />
                                {zone.persons}
                                {zone.capacity > 0 && ` / ${zone.capacity}`}
                              </span>
                              <span>
                                <Clock className="h-3 w-3 inline mr-0.5" />
                                {formatDuration(zone.dwellAvg)} avg
                              </span>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-xs text-muted-foreground">
                        No zone data available.
                      </p>
                    )}
                  </div>

                  {/* Occupancy chart */}
                  {occupancyChartData.length > 0 && (
                    <>
                      <Separator />
                      <div>
                        <h3 className="text-sm font-semibold mb-3">
                          Occupancy Overview
                        </h3>
                        <div className="h-40">
                          <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={occupancyChartData}>
                              <CartesianGrid strokeDasharray="3 3" className="opacity-30" />
                              <XAxis
                                dataKey="name"
                                tick={{ fontSize: 9 }}
                                interval={0}
                              />
                              <YAxis
                                tick={{ fontSize: 9 }}
                                domain={[0, 100]}
                                unit="%"
                              />
                              <RechartsTooltip
                                contentStyle={{
                                  fontSize: 11,
                                  borderRadius: 8,
                                }}
                              />
                              <Bar
                                dataKey="occupancy"
                                fill="#3B82F6"
                                radius={[4, 4, 0, 0]}
                                maxBarSize={30}
                              />
                            </BarChart>
                          </ResponsiveContainer>
                        </div>
                      </div>
                    </>
                  )}

                  <Separator />

                  {/* Camera list */}
                  <div>
                    <h3 className="text-sm font-semibold mb-3 flex items-center gap-2">
                      <Camera className="h-4 w-4" />
                      Placed Cameras
                    </h3>
                    {floorPlanDetail.camera_placements.length > 0 ? (
                      <div className="space-y-1.5">
                        {floorPlanDetail.camera_placements.map((cp) => {
                          const camLive = liveData?.cameras.find(
                            (c) => c.placement_id === cp.id
                          );
                          return (
                            <div
                              key={cp.id}
                              className={cn(
                                'flex items-center gap-2 p-2 rounded-md cursor-pointer transition-colors',
                                selectedCameraPlacement === cp.id
                                  ? 'bg-primary/10 border border-primary/30'
                                  : 'hover:bg-muted/50'
                              )}
                              onClick={() =>
                                setSelectedCameraPlacement(
                                  selectedCameraPlacement === cp.id
                                    ? null
                                    : cp.id
                                )
                              }
                            >
                              <div
                                className={cn(
                                  'w-2 h-2 rounded-full',
                                  cp.camera_is_online
                                    ? 'bg-green-500'
                                    : 'bg-red-500'
                                )}
                              />
                              <span className="text-xs flex-1 truncate">
                                {cp.label || cp.camera_name || 'Camera'}
                              </span>
                              {camLive && camLive.person_count > 0 && (
                                <Badge
                                  variant="secondary"
                                  className="text-[10px] h-4 px-1"
                                >
                                  {camLive.person_count}
                                </Badge>
                              )}
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <Button
                                    variant="ghost"
                                    size="icon"
                                    className="h-5 w-5 opacity-50 hover:opacity-100"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      removeCameraPlacementMutation.mutate(cp.id);
                                    }}
                                  >
                                    <Trash2 className="h-3 w-3" />
                                  </Button>
                                </TooltipTrigger>
                                <TooltipContent>Remove from map</TooltipContent>
                              </Tooltip>
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <p className="text-xs text-muted-foreground">
                        No cameras placed on this floor plan.
                      </p>
                    )}
                  </div>
                </div>
              </ScrollArea>
            </div>
          )}
        </div>

        {/* Upload Dialog */}
        <Dialog open={showUploadDialog} onOpenChange={setShowUploadDialog}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Upload Floor Plan</DialogTitle>
              <DialogDescription>
                Upload a PNG, JPG, or SVG floor plan image.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 pt-2">
              <div className="space-y-2">
                <Label htmlFor="fp-image">Floor Plan Image</Label>
                <Input
                  id="fp-image"
                  type="file"
                  accept="image/png,image/jpeg,image/svg+xml,image/webp"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) {
                      setUploadFile(f);
                      if (!uploadName) {
                        setUploadName(
                          f.name.replace(/\.[^/.]+$/, '').replace(/[-_]/g, ' ')
                        );
                      }
                    }
                  }}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="fp-name">Name</Label>
                <Input
                  id="fp-name"
                  value={uploadName}
                  onChange={(e) => setUploadName(e.target.value)}
                  placeholder="e.g. Main Building - Ground Floor"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-2">
                  <Label htmlFor="fp-building">Building</Label>
                  <Input
                    id="fp-building"
                    value={uploadBuilding}
                    onChange={(e) => setUploadBuilding(e.target.value)}
                    placeholder="e.g. Main Building"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="fp-floor">Floor Level</Label>
                  <Input
                    id="fp-floor"
                    type="number"
                    value={uploadFloor}
                    onChange={(e) =>
                      setUploadFloor(parseInt(e.target.value, 10) || 0)
                    }
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="fp-scale">
                  Scale (meters per pixel)
                </Label>
                <Input
                  id="fp-scale"
                  type="number"
                  step="0.001"
                  value={uploadScale}
                  onChange={(e) => setUploadScale(e.target.value)}
                  placeholder="e.g. 0.05"
                />
                <p className="text-xs text-muted-foreground">
                  Optional. Used for distance calculations on the map.
                </p>
              </div>

              <div className="flex justify-end gap-2 pt-2">
                <Button
                  variant="ghost"
                  onClick={() => setShowUploadDialog(false)}
                >
                  Cancel
                </Button>
                <Button
                  onClick={() => uploadMutation.mutate()}
                  disabled={
                    !uploadFile || !uploadName || uploadMutation.isPending
                  }
                >
                  {uploadMutation.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin mr-2" />
                  ) : (
                    <Upload className="h-4 w-4 mr-2" />
                  )}
                  Upload
                </Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>

        {/* Camera Placement Dialog */}
        <Dialog open={showCameraDialog} onOpenChange={setShowCameraDialog}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>Place Camera on Floor Plan</DialogTitle>
              <DialogDescription>
                Select a camera and configure its orientation, then click on
                the floor plan to place it.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 pt-2">
              <div className="space-y-2">
                <Label>Select Camera</Label>
                {unplacedCameras.length > 0 ? (
                  <div className="max-h-48 overflow-y-auto space-y-1 border rounded-md p-2">
                    {unplacedCameras.map((cam) => (
                      <div
                        key={cam.id}
                        className={cn(
                          'flex items-center gap-2 p-2 rounded cursor-pointer',
                          selectedCameraToPlace === cam.id
                            ? 'bg-primary/10 border border-primary/30'
                            : 'hover:bg-muted/50'
                        )}
                        onClick={() => setSelectedCameraToPlace(cam.id)}
                      >
                        <div
                          className={cn(
                            'w-2 h-2 rounded-full',
                            cam.is_online ? 'bg-green-500' : 'bg-red-500'
                          )}
                        />
                        <span className="text-sm flex-1">{cam.name}</span>
                        <Badge
                          variant="outline"
                          className="text-[10px]"
                        >
                          {cam.is_online ? 'Online' : 'Offline'}
                        </Badge>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    All cameras are already placed on this floor plan.
                  </p>
                )}
              </div>

              <div className="grid grid-cols-3 gap-3">
                <div className="space-y-2">
                  <Label>Rotation</Label>
                  <Input
                    type="number"
                    min={0}
                    max={360}
                    value={placementRotation}
                    onChange={(e) =>
                      setPlacementRotation(
                        parseInt(e.target.value, 10) || 0
                      )
                    }
                  />
                  <p className="text-[10px] text-muted-foreground">
                    degrees
                  </p>
                </div>
                <div className="space-y-2">
                  <Label>FOV Angle</Label>
                  <Input
                    type="number"
                    min={10}
                    max={360}
                    value={placementFovAngle}
                    onChange={(e) =>
                      setPlacementFovAngle(
                        parseInt(e.target.value, 10) || 90
                      )
                    }
                  />
                  <p className="text-[10px] text-muted-foreground">
                    degrees
                  </p>
                </div>
                <div className="space-y-2">
                  <Label>FOV Range</Label>
                  <Input
                    type="number"
                    min={10}
                    max={500}
                    value={placementFovRange}
                    onChange={(e) =>
                      setPlacementFovRange(
                        parseInt(e.target.value, 10) || 100
                      )
                    }
                  />
                  <p className="text-[10px] text-muted-foreground">
                    pixels
                  </p>
                </div>
              </div>

              <div className="flex justify-end gap-2 pt-2">
                <Button
                  variant="ghost"
                  onClick={() => setShowCameraDialog(false)}
                >
                  Cancel
                </Button>
                <Button
                  onClick={() => {
                    if (selectedCameraToPlace) {
                      handleStartCameraPlacement(selectedCameraToPlace);
                    }
                  }}
                  disabled={!selectedCameraToPlace}
                >
                  <Crosshair className="h-4 w-4 mr-2" />
                  Place on Map
                </Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>
      </div>
    </TooltipProvider>
  );
}
