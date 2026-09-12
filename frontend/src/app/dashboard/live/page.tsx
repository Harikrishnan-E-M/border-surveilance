'use client';

import { useState, useEffect, useRef } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import {
  Maximize2,
  Minimize2,
  Grid2X2,
  Grid3X3,
  Square,
  Loader2,
  VideoOff,
  Camera,
  ChevronDown,
  LayoutGrid,
  Play,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { apiClient } from '@/lib/api-client';
import { getAccessToken } from '@/lib/auth';
import { StreamPlayer } from '@/components/camera/stream-player';

/** Build the MJPEG stream URL for a camera */
function getMjpegUrl(cameraId: string, token: string | null): string {
  if (typeof window === 'undefined') return '';
  const protocol = window.location.protocol;
  const hostname = window.location.hostname;
  const params = new URLSearchParams({ fps: '12', quality: '65' });
  if (token) params.set('token', token);
  return `${protocol}//${hostname}:8000/api/v1/cameras/${cameraId}/stream/mjpeg?${params.toString()}`;
}

interface CameraOption {
  id: string;
  name: string;
  stream_url: string;
  is_online?: boolean;
  is_active?: boolean;
  status?: 'online' | 'offline' | 'degraded';
  location_description?: string | null;
  location?: string;
}

interface Detection {
  id: string;
  type: string;
  label: string;
  confidence: number;
  bbox: [number, number, number, number];
}

type GridLayout = '1x1' | '2x2' | '3x3' | '4x4';

interface GridCell {
  cameraId: string | null;
  isFullscreen: boolean;
}

const gridConfig: Record<GridLayout, number> = {
  '1x1': 1,
  '2x2': 4,
  '3x3': 9,
  '4x4': 16,
};

const gridCols: Record<GridLayout, string> = {
  '1x1': 'grid-cols-1',
  '2x2': 'grid-cols-2',
  '3x3': 'grid-cols-3',
  '4x4': 'grid-cols-4',
};

export default function LivePage() {
  const accessToken = getAccessToken();
  const [layout, setLayout] = useState<GridLayout>('2x2');
  const [cells, setCells] = useState<GridCell[]>([]);
  const [fullscreenCell, setFullscreenCell] = useState<number | null>(null);

  const { data: cameras, isLoading, refetch: refetchCameras } = useQuery<CameraOption[]>({
    queryKey: ['cameras', 'list'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/cameras', {
        params: { page_size: 100 },
      });
      const raw = res.data;
      return Array.isArray(raw) ? raw : (raw?.items || raw?.data || []);
    },
  });

  const startAllMutation = useMutation({
    mutationFn: async () => {
      await apiClient.post('/api/v1/cameras/start-all');
    },
    onSuccess: () => {
      refetchCameras();
    },
  });

  useEffect(() => {
    const count = gridConfig[layout];
    setCells((prev) => {
      const newCells: GridCell[] = [];
      for (let i = 0; i < count; i++) {
        newCells.push(prev[i] || { cameraId: null, isFullscreen: false });
      }
      return newCells;
    });
    setFullscreenCell(null);
  }, [layout]);

  // Auto-assign cameras to empty cells
  useEffect(() => {
    if (cameras && cameras.length > 0 && cells.length > 0) {
      const hasAnyCamera = cells.some((c) => c.cameraId !== null);
      if (!hasAnyCamera) {
        setCells((prev) =>
          prev.map((cell, idx) => ({
            ...cell,
            cameraId: cameras[idx]?.id || null,
          }))
        );
      }
    }
  }, [cameras, cells.length]);

  const assignCamera = (cellIndex: number, cameraId: string | null) => {
    setCells((prev) =>
      prev.map((cell, idx) =>
        idx === cellIndex ? { ...cell, cameraId } : cell
      )
    );
  };

  const toggleFullscreen = (cellIndex: number) => {
    setFullscreenCell((prev) => (prev === cellIndex ? null : cellIndex));
  };

  const layoutButtons: { value: GridLayout; label: string; icon: React.ElementType }[] = [
    { value: '1x1', label: '1x1', icon: Square },
    { value: '2x2', label: '2x2', icon: Grid2X2 },
    { value: '3x3', label: '3x3', icon: Grid3X3 },
    { value: '4x4', label: '4x4', icon: LayoutGrid },
  ];

  if (isLoading) {
    return (
      <div className="flex h-96 items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
          <p className="text-sm text-slate-500">Loading cameras...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Live Monitoring</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Real-time camera feeds with AI detection overlays
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            onClick={() => startAllMutation.mutate()}
            disabled={startAllMutation.isPending}
          >
            {startAllMutation.isPending ? (
              <Loader2 className="mr-1 h-4 w-4 animate-spin" />
            ) : (
              <Play className="mr-1 h-4 w-4" />
            )}
            Start All
          </Button>
          <span className="text-sm text-slate-500 dark:text-slate-400">Layout:</span>
          <div className="flex rounded-lg border border-slate-200 dark:border-slate-700">
            {layoutButtons.map((btn) => {
              const Icon = btn.icon;
              return (
                <button
                  key={btn.value}
                  onClick={() => setLayout(btn.value)}
                  className={`flex items-center gap-1 px-3 py-1.5 text-sm transition-colors first:rounded-l-lg last:rounded-r-lg ${
                    layout === btn.value
                      ? 'bg-blue-600 text-white'
                      : 'bg-white text-slate-600 hover:bg-slate-50 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700'
                  }`}
                >
                  <Icon className="h-4 w-4" />
                  <span className="hidden sm:inline">{btn.label}</span>
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* Grid */}
      {fullscreenCell !== null ? (
        <div className="relative aspect-video w-full">
          <CameraCell
            cell={cells[fullscreenCell]}
            cameras={cameras || []}
            cellIndex={fullscreenCell}
            onAssignCamera={assignCamera}
            onToggleFullscreen={toggleFullscreen}
            isFullscreen={true}
            accessToken={accessToken}
          />
        </div>
      ) : (
        <div className={`grid gap-2 ${gridCols[layout]}`}>
          {cells.map((cell, idx) => (
            <div key={idx} className="aspect-video">
              <CameraCell
                cell={cell}
                cameras={cameras || []}
                cellIndex={idx}
                onAssignCamera={assignCamera}
                onToggleFullscreen={toggleFullscreen}
                isFullscreen={false}
                accessToken={accessToken}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function CameraCell({
  cell,
  cameras,
  cellIndex,
  onAssignCamera,
  onToggleFullscreen,
  isFullscreen,
  accessToken,
}: {
  cell: GridCell;
  cameras: CameraOption[];
  cellIndex: number;
  onAssignCamera: (index: number, cameraId: string | null) => void;
  onToggleFullscreen: (index: number) => void;
  isFullscreen: boolean;
  accessToken: string | null;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const [showCameraSelect, setShowCameraSelect] = useState(false);
  const [detections, setDetections] = useState<Detection[]>([]);

  const selectedCamera = cameras.find((c) => c.id === cell.cameraId);

  // WebSocket for detection overlays
  useEffect(() => {
    if (!cell.cameraId || !accessToken) return;

    const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.hostname}:8000/api/v1/ws/detections/${cell.cameraId}?token=${accessToken}`;
    const ws = new WebSocket(wsUrl);

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.detections) {
          setDetections(data.detections);
        }
      } catch {
        // Ignore parse errors
      }
    };

    ws.onerror = () => {
      setDetections([]);
    };

    wsRef.current = ws;

    return () => {
      ws.close();
      wsRef.current = null;
      setDetections([]);
    };
  }, [cell.cameraId, accessToken]);

  // Draw detections on canvas
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const parent = canvas.parentElement;
    if (parent) {
      canvas.width = parent.clientWidth;
      canvas.height = parent.clientHeight;
    }

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    detections.forEach((det) => {
      const [x, y, w, h] = det.bbox;
      const sx = canvas.width;
      const sy = canvas.height;

      ctx.strokeStyle = det.type === 'person' ? '#22c55e' : '#3b82f6';
      ctx.lineWidth = 2;
      ctx.strokeRect(x * sx, y * sy, w * sx, h * sy);

      ctx.fillStyle = det.type === 'person' ? '#22c55e' : '#3b82f6';
      ctx.font = '12px Inter, sans-serif';
      const label = `${det.label} ${Math.round(det.confidence * 100)}%`;
      const textWidth = ctx.measureText(label).width;
      ctx.fillRect(x * sx, y * sy - 18, textWidth + 8, 18);
      ctx.fillStyle = '#ffffff';
      ctx.fillText(label, x * sx + 4, y * sy - 5);
    });
  }, [detections]);

  return (
    <div className="relative h-full w-full overflow-hidden rounded-lg border border-slate-200 bg-slate-900 dark:border-slate-700">
      {cell.cameraId && selectedCamera ? (
        <>
          {/* Live MJPEG Stream */}
          <StreamPlayer
            url={getMjpegUrl(cell.cameraId, accessToken)}
            title={selectedCamera.name}
            status={selectedCamera.is_online ? 'online' : (selectedCamera.status || 'offline')}
            className="h-full w-full"
          />

          {/* Detection Overlay Canvas */}
          <canvas
            ref={canvasRef}
            className="pointer-events-none absolute inset-0 h-full w-full"
          />

          {detections.length > 0 && (
            <div className="absolute right-2 top-2">
              <Badge className="bg-blue-600/80 text-white backdrop-blur-sm text-xs">
                {detections.length} detection{detections.length > 1 ? 's' : ''}
              </Badge>
            </div>
          )}
        </>
      ) : (
        <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-slate-500">
          <VideoOff className="h-8 w-8" />
          <span className="text-sm">No camera selected</span>
        </div>
      )}

      {/* Bottom Controls */}
      <div className="absolute bottom-0 left-0 right-0 flex items-center justify-between bg-gradient-to-t from-black/70 to-transparent p-2">
        {/* Camera Selector */}
        <div className="relative">
          <button
            onClick={() => setShowCameraSelect(!showCameraSelect)}
            className="flex items-center gap-1 rounded-md bg-white/10 px-2 py-1 text-xs text-white backdrop-blur-sm hover:bg-white/20"
          >
            <Camera className="h-3 w-3" />
            <span>{selectedCamera?.name || 'Select Camera'}</span>
            <ChevronDown className="h-3 w-3" />
          </button>

          {showCameraSelect && (
            <div className="absolute bottom-full left-0 z-10 mb-1 max-h-48 w-56 overflow-y-auto rounded-lg border border-slate-600 bg-slate-800 py-1 shadow-xl">
              <button
                onClick={() => {
                  onAssignCamera(cellIndex, null);
                  setShowCameraSelect(false);
                }}
                className="w-full px-3 py-1.5 text-left text-xs text-slate-400 hover:bg-slate-700"
              >
                -- None --
              </button>
              {cameras.map((cam) => (
                <button
                  key={cam.id}
                  onClick={() => {
                    onAssignCamera(cellIndex, cam.id);
                    setShowCameraSelect(false);
                  }}
                  className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-slate-700 ${
                    cam.id === cell.cameraId
                      ? 'bg-blue-900/40 text-blue-400'
                      : 'text-slate-300'
                  }`}
                >
                  <div
                    className={`h-2 w-2 rounded-full ${
                      cam.is_online ? 'bg-green-500' : 'bg-red-500'
                    }`}
                  />
                  <span className="truncate">{cam.name}</span>
                  <span className="ml-auto text-slate-500">{cam.location_description || cam.location || ''}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Fullscreen Toggle */}
        <button
          onClick={() => onToggleFullscreen(cellIndex)}
          className="rounded-md bg-white/10 p-1.5 text-white backdrop-blur-sm hover:bg-white/20"
          title={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
        >
          {isFullscreen ? (
            <Minimize2 className="h-4 w-4" />
          ) : (
            <Maximize2 className="h-4 w-4" />
          )}
        </button>
      </div>
    </div>
  );
}
