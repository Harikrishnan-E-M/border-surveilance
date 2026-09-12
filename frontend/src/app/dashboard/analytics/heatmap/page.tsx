'use client';

import { useState, useCallback } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import {
  Loader2,
  Flame,
  Camera,
  SlidersHorizontal,
  Grid3X3,
  Maximize2,
  Palette,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { HeatmapOverlay } from '@/components/analytics/heatmap-overlay';
import { apiClient } from '@/lib/api-client';
import { cn } from '@/lib/utils';

// -------------------------------------------------------------------
// Types
// -------------------------------------------------------------------

interface CameraItem {
  id: string;
  name: string;
  snapshot_url?: string;
  location?: string;
}

interface HeatmapResult {
  camera_id: string;
  camera_name: string;
  background_url: string;
  heatmap_url: string;
  time_range: { start: string; end: string };
  total_detections: number;
  peak_zone: string;
}

interface HeatmapGeneratePayload {
  camera_ids: string[];
  time_range: string;
  color_scheme: string;
  date_from?: string;
  date_to?: string;
}

// -------------------------------------------------------------------
// Constants
// -------------------------------------------------------------------

const TIME_RANGES = [
  { value: '1h', label: 'Last 1 Hour' },
  { value: '6h', label: 'Last 6 Hours' },
  { value: '12h', label: 'Last 12 Hours' },
  { value: '24h', label: 'Last 24 Hours' },
  { value: '7d', label: 'Last 7 Days' },
  { value: '30d', label: 'Last 30 Days' },
  { value: 'custom', label: 'Custom Range' },
];

const COLOR_SCHEMES = [
  { value: 'jet', label: 'Jet (Classic)' },
  { value: 'hot', label: 'Hot (Red-Yellow)' },
  { value: 'inferno', label: 'Inferno' },
  { value: 'viridis', label: 'Viridis' },
  { value: 'plasma', label: 'Plasma' },
];

// -------------------------------------------------------------------
// Page
// -------------------------------------------------------------------

export default function HeatmapAnalyticsPage() {
  // Filters
  const [selectedCameras, setSelectedCameras] = useState<string[]>([]);
  const [timeRange, setTimeRange] = useState<string>('24h');
  const [colorScheme, setColorScheme] = useState<string>('jet');
  const [opacity, setOpacity] = useState<number>(60);
  const [customFrom, setCustomFrom] = useState<string>('');
  const [customTo, setCustomTo] = useState<string>('');
  const [viewMode, setViewMode] = useState<'grid' | 'single'>('grid');
  const [expandedCamera, setExpandedCamera] = useState<string | null>(null);

  // -------------------------------------------------------------------
  // Queries
  // -------------------------------------------------------------------

  const { data: cameras, isLoading: camerasLoading } = useQuery<CameraItem[]>({
    queryKey: ['cameras-list-heatmap'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/cameras', {
        params: { page_size: 200 },
      });
      return res.data?.results ?? res.data ?? [];
    },
  });

  const {
    data: heatmaps,
    isLoading: heatmapsLoading,
    isError: heatmapsError,
    error: heatmapErrorObj,
  } = useQuery<HeatmapResult[]>({
    queryKey: ['heatmaps', selectedCameras, timeRange, colorScheme, customFrom, customTo],
    queryFn: async () => {
      const params: Record<string, string> = {
        time_range: timeRange,
        color_scheme: colorScheme,
      };
      if (selectedCameras.length > 0) {
        params.camera_ids = selectedCameras.join(',');
      }
      if (timeRange === 'custom') {
        if (customFrom) params.date_from = customFrom;
        if (customTo) params.date_to = customTo;
      }

      const res = await apiClient.get('/api/v1/analytics/heatmaps', { params });
      return res.data?.results ?? res.data ?? [];
    },
    enabled: true,
  });

  // Generate heatmap mutation
  const generateMutation = useMutation({
    mutationFn: async (payload: HeatmapGeneratePayload) => {
      const res = await apiClient.post('/api/v1/analytics/heatmaps/generate', payload);
      return res.data;
    },
  });

  // -------------------------------------------------------------------
  // Handlers
  // -------------------------------------------------------------------

  const handleCameraToggle = useCallback(
    (cameraId: string) => {
      setSelectedCameras((prev) =>
        prev.includes(cameraId)
          ? prev.filter((id) => id !== cameraId)
          : [...prev, cameraId]
      );
    },
    []
  );

  const handleSelectAllCameras = useCallback(() => {
    if (cameras) {
      if (selectedCameras.length === cameras.length) {
        setSelectedCameras([]);
      } else {
        setSelectedCameras(cameras.map((c) => c.id));
      }
    }
  }, [cameras, selectedCameras.length]);

  const handleGenerate = useCallback(() => {
    const payload: HeatmapGeneratePayload = {
      camera_ids: selectedCameras.length > 0 ? selectedCameras : (cameras ?? []).map((c) => c.id),
      time_range: timeRange,
      color_scheme: colorScheme,
    };
    if (timeRange === 'custom') {
      payload.date_from = customFrom;
      payload.date_to = customTo;
    }
    generateMutation.mutate(payload);
  }, [selectedCameras, cameras, timeRange, colorScheme, customFrom, customTo, generateMutation]);

  // -------------------------------------------------------------------
  // Loading
  // -------------------------------------------------------------------

  if (camerasLoading) {
    return (
      <div className="flex h-96 items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
          <p className="text-sm text-slate-500">Loading heatmap analytics...</p>
        </div>
      </div>
    );
  }

  // -------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------

  const heatmapList = heatmaps ?? [];

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">
            Heatmap Analytics
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Visualize movement density and activity zones across cameras
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant={viewMode === 'grid' ? 'default' : 'outline'}
            size="sm"
            onClick={() => setViewMode('grid')}
          >
            <Grid3X3 className="mr-1.5 h-4 w-4" />
            Grid
          </Button>
          <Button
            variant={viewMode === 'single' ? 'default' : 'outline'}
            size="sm"
            onClick={() => setViewMode('single')}
          >
            <Maximize2 className="mr-1.5 h-4 w-4" />
            Single
          </Button>
        </div>
      </div>

      {/* Controls */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <SlidersHorizontal className="h-4 w-4" />
            Heatmap Controls
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {/* Camera Selector */}
            <div className="space-y-1.5">
              <Label className="text-xs">Camera Selection</Label>
              <div className="flex gap-2">
                <Select
                  value={selectedCameras.length === 1 ? selectedCameras[0] : 'multiple'}
                  onValueChange={(val) => {
                    if (val !== 'multiple') {
                      setSelectedCameras([val]);
                    }
                  }}
                >
                  <SelectTrigger>
                    <SelectValue
                      placeholder={
                        selectedCameras.length === 0
                          ? 'All Cameras'
                          : `${selectedCameras.length} selected`
                      }
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {(cameras ?? []).map((cam) => (
                      <SelectItem key={cam.id} value={cam.id}>
                        {cam.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button variant="outline" size="icon" onClick={handleSelectAllCameras} title="Toggle all">
                  <Camera className="h-4 w-4" />
                </Button>
              </div>
            </div>

            {/* Time Range */}
            <div className="space-y-1.5">
              <Label className="text-xs">Time Range</Label>
              <Select value={timeRange} onValueChange={setTimeRange}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TIME_RANGES.map((tr) => (
                    <SelectItem key={tr.value} value={tr.value}>
                      {tr.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Color Scheme */}
            <div className="space-y-1.5">
              <Label className="flex items-center gap-1.5 text-xs">
                <Palette className="h-3.5 w-3.5" />
                Color Scheme
              </Label>
              <Select value={colorScheme} onValueChange={setColorScheme}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {COLOR_SCHEMES.map((cs) => (
                    <SelectItem key={cs.value} value={cs.value}>
                      {cs.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Opacity Control */}
            <div className="space-y-1.5">
              <Label className="text-xs">
                Overlay Opacity: {opacity}%
              </Label>
              <input
                type="range"
                min={0}
                max={100}
                value={opacity}
                onChange={(e) => setOpacity(Number(e.target.value))}
                className="mt-2 w-full accent-primary h-2 rounded-lg appearance-none bg-secondary cursor-pointer"
              />
            </div>
          </div>

          {/* Custom Date Range */}
          {timeRange === 'custom' && (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <div className="space-y-1.5">
                <Label className="text-xs">From</Label>
                <Input
                  type="datetime-local"
                  value={customFrom}
                  onChange={(e) => setCustomFrom(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">To</Label>
                <Input
                  type="datetime-local"
                  value={customTo}
                  onChange={(e) => setCustomTo(e.target.value)}
                />
              </div>
            </div>
          )}

          {/* Camera Pills */}
          {cameras && cameras.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {cameras.map((cam) => {
                const isActive = selectedCameras.includes(cam.id);
                return (
                  <button
                    key={cam.id}
                    onClick={() => handleCameraToggle(cam.id)}
                    className={cn(
                      'inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors',
                      isActive
                        ? 'border-blue-300 bg-blue-50 text-blue-700 dark:border-blue-700 dark:bg-blue-900/30 dark:text-blue-400'
                        : 'border-slate-200 bg-background text-muted-foreground hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800'
                    )}
                  >
                    <Camera className="h-3 w-3" />
                    {cam.name}
                  </button>
                );
              })}
            </div>
          )}

          {/* Generate Button */}
          <div className="flex items-center gap-3">
            <Button onClick={handleGenerate} disabled={generateMutation.isPending}>
              {generateMutation.isPending ? (
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
              ) : (
                <Flame className="mr-1.5 h-4 w-4" />
              )}
              Generate Heatmap
            </Button>
            {generateMutation.isSuccess && (
              <Badge variant="default" className="bg-green-600">
                Heatmap generated successfully
              </Badge>
            )}
            {generateMutation.isError && (
              <Badge variant="critical">
                Generation failed. Please try again.
              </Badge>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Error State */}
      {heatmapsError && (
        <Card className="border-red-200 bg-red-50 dark:border-red-800 dark:bg-red-950/30">
          <CardContent className="flex items-center gap-3 p-4">
            <Flame className="h-5 w-5 text-red-500" />
            <div>
              <p className="text-sm font-medium text-red-700 dark:text-red-400">
                Failed to load heatmap data
              </p>
              <p className="text-xs text-red-600 dark:text-red-500">
                {(heatmapErrorObj as { message?: string })?.message ?? 'Please check your connection and try again.'}
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Loading */}
      {heatmapsLoading && (
        <div className="flex h-64 items-center justify-center">
          <div className="flex flex-col items-center gap-3">
            <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
            <p className="text-sm text-slate-500">Generating heatmaps...</p>
          </div>
        </div>
      )}

      {/* Heatmap Results */}
      {!heatmapsLoading && heatmapList.length === 0 && !heatmapsError && (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-16">
            <Flame className="mb-3 h-12 w-12 text-slate-300 dark:text-slate-600" />
            <p className="text-sm font-medium text-slate-500 dark:text-slate-400">
              No heatmaps available
            </p>
            <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">
              Select cameras and time range, then click Generate Heatmap
            </p>
          </CardContent>
        </Card>
      )}

      {/* Heatmap Grid / Single View */}
      {heatmapList.length > 0 && (
        <Tabs value={viewMode} onValueChange={(v) => setViewMode(v as 'grid' | 'single')}>
          <TabsList>
            <TabsTrigger value="grid">Grid View</TabsTrigger>
            <TabsTrigger value="single">Single View</TabsTrigger>
          </TabsList>

          <TabsContent value="grid">
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {heatmapList.map((hm) => (
                <div key={hm.camera_id} className="relative">
                  <HeatmapOverlay
                    backgroundImageUrl={hm.background_url}
                    heatmapImageUrl={hm.heatmap_url}
                    title={hm.camera_name}
                    timeRange={hm.time_range}
                  />
                  <div className="mt-2 flex items-center justify-between px-1">
                    <div className="flex items-center gap-2">
                      <Badge variant="secondary" className="text-xs">
                        {hm.total_detections.toLocaleString()} detections
                      </Badge>
                      {hm.peak_zone && (
                        <Badge variant="outline" className="text-xs">
                          Peak: {hm.peak_zone}
                        </Badge>
                      )}
                    </div>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        setExpandedCamera(hm.camera_id);
                        setViewMode('single');
                      }}
                    >
                      <Maximize2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </TabsContent>

          <TabsContent value="single">
            {(() => {
              const activeHeatmap =
                heatmapList.find((h) => h.camera_id === expandedCamera) ?? heatmapList[0];
              if (!activeHeatmap) return null;

              return (
                <div className="space-y-4">
                  {/* Camera selector pills for single view */}
                  <div className="flex flex-wrap gap-2">
                    {heatmapList.map((hm) => (
                      <button
                        key={hm.camera_id}
                        onClick={() => setExpandedCamera(hm.camera_id)}
                        className={cn(
                          'rounded-full border px-3 py-1 text-xs font-medium transition-colors',
                          (expandedCamera === hm.camera_id ||
                            (!expandedCamera && hm.camera_id === heatmapList[0]?.camera_id))
                            ? 'border-blue-300 bg-blue-50 text-blue-700 dark:border-blue-700 dark:bg-blue-900/30 dark:text-blue-400'
                            : 'border-slate-200 text-muted-foreground hover:bg-slate-50 dark:border-slate-700'
                        )}
                      >
                        {hm.camera_name}
                      </button>
                    ))}
                  </div>

                  <HeatmapOverlay
                    backgroundImageUrl={activeHeatmap.background_url}
                    heatmapImageUrl={activeHeatmap.heatmap_url}
                    title={activeHeatmap.camera_name}
                    timeRange={activeHeatmap.time_range}
                    className="max-w-4xl"
                  />

                  <div className="flex items-center gap-3">
                    <Badge variant="secondary">
                      {activeHeatmap.total_detections.toLocaleString()} total detections
                    </Badge>
                    {activeHeatmap.peak_zone && (
                      <Badge variant="outline">
                        Peak Zone: {activeHeatmap.peak_zone}
                      </Badge>
                    )}
                  </div>
                </div>
              );
            })()}
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}
