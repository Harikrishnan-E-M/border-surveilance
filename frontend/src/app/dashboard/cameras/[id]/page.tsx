'use client';

import { useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import {
  LineChart,
  Line,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import {
  ArrowLeft,
  Video,
  Layers,
  ShieldCheck,
  Activity,
  Heart,
  Settings,
  Loader2,
  Wifi,
  WifiOff,
  Plus,
  Pencil,
  Trash2,
  ToggleLeft,
  ToggleRight,
  Save,
  AlertCircle,
  Clock,
  Camera,
  MapPin,
  RefreshCw,
} from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { apiClient } from '@/lib/api-client';
import { StreamPlayer } from '@/components/camera/stream-player';
import { getAccessToken } from '@/lib/auth';
import { CreateZoneDialog } from '@/components/camera/create-zone-dialog';
import { CreateRuleDialog } from '@/components/camera/create-rule-dialog';

interface CameraDetail {
  id: string;
  name: string;
  location_description: string | null;
  location?: string;
  stream_url: string;
  is_online: boolean;
  is_active: boolean;
  status?: 'online' | 'offline' | 'degraded';
  protocol: string;
  resolution: string | null;
  fps: number | null;
  thumbnail_url?: string | null;
  created_at: string;
  updated_at: string;
}

interface Zone {
  id: string;
  name: string;
  type: string;
  polygon: number[][];
  color: string;
  active: boolean;
}

interface Rule {
  id: string;
  name: string;
  type: string;
  enabled: boolean;
  zone_id: string | null;
  config: Record<string, any>;
}

interface CameraEvent {
  id: string;
  type: string;
  severity: string;
  message: string;
  created_at: string;
}

interface HealthMetric {
  timestamp: string;
  fps: number;
  bitrate: number;
  latency: number;
  packet_loss: number;
}

const tabs = [
  { id: 'live', label: 'Live View', icon: Video },
  { id: 'zones', label: 'Zones', icon: Layers },
  { id: 'rules', label: 'Rules', icon: ShieldCheck },
  { id: 'events', label: 'Events', icon: Activity },
  { id: 'health', label: 'Health', icon: Heart },
  { id: 'settings', label: 'Settings', icon: Settings },
] as const;

type TabId = (typeof tabs)[number]['id'];

const cameraSettingsSchema = z.object({
  name: z.string().min(1, 'Name is required'),
  location: z.string().min(1, 'Location is required'),
  stream_url: z.string().url('Must be a valid URL'),
  protocol: z.enum(['rtsp', 'rtmp', 'hls', 'webrtc']),
});

type CameraSettingsForm = z.infer<typeof cameraSettingsSchema>;

/** Build the MJPEG stream URL for a camera */
function getMjpegUrl(cameraId: string, token: string | null): string {
  if (typeof window === 'undefined') return '';
  const protocol = window.location.protocol;
  const hostname = window.location.hostname;
  const params = new URLSearchParams({ fps: '15', quality: '75' });
  if (token) params.set('token', token);
  return `${protocol}//${hostname}:8000/api/v1/cameras/${cameraId}/stream/mjpeg?${params.toString()}`;
}

export default function CameraDetailPage() {
  const params = useParams();
  const router = useRouter();
  const queryClient = useQueryClient();
  const cameraId = params.id as string;
  const accessToken = getAccessToken();

  const [activeTab, setActiveTab] = useState<TabId>('live');
  const [streamStarted, setStreamStarted] = useState(false);
  const [zoneDialogOpen, setZoneDialogOpen] = useState(false);
  const [ruleDialogOpen, setRuleDialogOpen] = useState(false);

  const { data: camera, isLoading } = useQuery<CameraDetail>({
    queryKey: ['cameras', cameraId],
    queryFn: async () => {
      const res = await apiClient.get(`/api/v1/cameras/${cameraId}`);
      return res.data;
    },
  });

  const { data: zones } = useQuery<Zone[]>({
    queryKey: ['cameras', cameraId, 'zones'],
    queryFn: async () => {
      const res = await apiClient.get(`/api/v1/cameras/${cameraId}/zones`);
      return res.data;
    },
    enabled: activeTab === 'zones' || activeTab === 'live',
  });

  const { data: rules } = useQuery<Rule[]>({
    queryKey: ['cameras', cameraId, 'rules'],
    queryFn: async () => {
      const res = await apiClient.get(`/api/v1/rules`, {
        params: { camera_id: cameraId },
      });
      return res.data.items || res.data;
    },
    enabled: activeTab === 'rules',
  });

  const { data: events } = useQuery<CameraEvent[]>({
    queryKey: ['cameras', cameraId, 'events'],
    queryFn: async () => {
      const res = await apiClient.get(`/api/v1/events/recent`, {
        params: { limit: 50 },
      });
      return res.data.items || res.data;
    },
    enabled: activeTab === 'events',
  });

  const { data: healthMetrics } = useQuery<HealthMetric[]>({
    queryKey: ['cameras', cameraId, 'health'],
    queryFn: async () => {
      const res = await apiClient.get(`/api/v1/cameras/${cameraId}/health`);
      return res.data;
    },
    enabled: activeTab === 'health',
    refetchInterval: activeTab === 'health' ? 10000 : false,
  });

  const toggleRuleMutation = useMutation({
    mutationFn: async ({ ruleId, enabled }: { ruleId: string; enabled: boolean }) => {
      await apiClient.patch(`/api/v1/rules/${ruleId}`, { enabled });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['cameras', cameraId, 'rules'] });
    },
  });

  const deleteZoneMutation = useMutation({
    mutationFn: async (zoneId: string) => {
      await apiClient.delete(`/api/v1/cameras/${cameraId}/zones/${zoneId}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['cameras', cameraId, 'zones'] });
    },
  });

  const startStreamMutation = useMutation({
    mutationFn: async () => {
      await apiClient.post(`/api/v1/cameras/${cameraId}/start`);
    },
    onSuccess: () => {
      setStreamStarted(true);
      queryClient.invalidateQueries({ queryKey: ['cameras', cameraId] });
    },
  });

  const updateCameraMutation = useMutation({
    mutationFn: async (data: CameraSettingsForm) => {
      await apiClient.put(`/api/v1/cameras/${cameraId}`, data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['cameras', cameraId] });
    },
  });

  const {
    register: registerSettings,
    handleSubmit: handleSubmitSettings,
    formState: { errors: settingsErrors },
  } = useForm<CameraSettingsForm>({
    resolver: zodResolver(cameraSettingsSchema),
    values: camera
      ? {
          name: camera.name,
          location: camera.location,
          stream_url: camera.stream_url,
          protocol: camera.protocol as any,
        }
      : undefined,
  });

  if (isLoading) {
    return (
      <div className="flex h-96 items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
      </div>
    );
  }

  if (!camera) {
    return (
      <div className="flex h-96 flex-col items-center justify-center text-slate-400">
        <Camera className="mb-3 h-10 w-10" />
        <p className="text-sm font-medium">Camera not found</p>
        <Link href="/dashboard/cameras">
          <Button variant="link" className="mt-2">
            Back to cameras
          </Button>
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <Link href="/dashboard/cameras">
            <Button variant="ghost" size="sm">
              <ArrowLeft className="h-4 w-4" />
            </Button>
          </Link>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-2xl font-bold text-slate-900 dark:text-white">
                {camera.name}
              </h1>
              <Badge
                className={
                  camera.is_online
                    ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                    : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
                }
              >
                {camera.is_online ? (
                  <Wifi className="mr-1 h-3 w-3" />
                ) : (
                  <WifiOff className="mr-1 h-3 w-3" />
                )}
                {camera.is_online ? 'online' : 'offline'}
              </Badge>
            </div>
            <div className="flex items-center gap-3 text-sm text-slate-500 dark:text-slate-400">
              <span className="flex items-center gap-1">
                <MapPin className="h-3 w-3" />
                {camera.location_description || camera.location || 'No location'}
              </span>
              <span>{camera.protocol?.toUpperCase()}</span>
              {camera.resolution && <span>{camera.resolution}</span>}
              {camera.fps && <span>{camera.fps} FPS</span>}
            </div>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="border-b border-slate-200 dark:border-slate-700">
        <div className="flex gap-1 overflow-x-auto">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-2 whitespace-nowrap border-b-2 px-4 py-2.5 text-sm font-medium transition-colors ${
                  activeTab === tab.id
                    ? 'border-blue-600 text-blue-600 dark:border-blue-400 dark:text-blue-400'
                    : 'border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200'
                }`}
              >
                <Icon className="h-4 w-4" />
                {tab.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Tab Content */}
      {activeTab === 'live' && (
        <div className="space-y-4">
          {/* Start Stream button (if not streaming yet) */}
          {!streamStarted && !camera.is_online && (
            <div className="flex items-center gap-3">
              <Button
                onClick={() => startStreamMutation.mutate()}
                disabled={startStreamMutation.isPending}
                size="sm"
              >
                {startStreamMutation.isPending ? (
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                ) : (
                  <Video className="mr-1 h-4 w-4" />
                )}
                Start Stream
              </Button>
              {startStreamMutation.isError && (
                <span className="text-sm text-red-500">Failed to start stream</span>
              )}
            </div>
          )}

          <Card>
            <CardContent className="p-0">
              {(streamStarted || camera.is_online) ? (
                <StreamPlayer
                  url={getMjpegUrl(cameraId, accessToken)}
                  title={camera.name}
                  status={camera.is_online ? 'online' : 'offline'}
                  className="aspect-video w-full"
                />
              ) : (
                <div className="relative aspect-video w-full bg-slate-900">
                  <div className="flex h-full w-full items-center justify-center">
                    <div className="flex flex-col items-center gap-2 text-slate-500">
                      <Video className="h-12 w-12" />
                      <span className="text-sm">Click &quot;Start Stream&quot; to begin</span>
                      <span className="text-xs text-slate-600">
                        {camera.resolution} @ {camera.fps || 30}fps
                      </span>
                    </div>
                  </div>
                </div>
              )}
              {/* Zone overlay badge */}
              {zones && zones.length > 0 && (
                <div className="absolute bottom-2 left-2 z-10">
                  <Badge className="bg-black/60 text-white backdrop-blur-sm text-xs">
                    {zones.length} zone{zones.length !== 1 ? 's' : ''} configured
                  </Badge>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {activeTab === 'zones' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <p className="text-sm text-slate-500 dark:text-slate-400">
              {zones?.length || 0} zone{(zones?.length || 0) !== 1 ? 's' : ''} configured
            </p>
            <Button size="sm" onClick={() => setZoneDialogOpen(true)}>
              <Plus className="mr-1 h-4 w-4" />
              Add Zone
            </Button>
          </div>

          {zones && zones.length > 0 ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {zones.map((zone) => (
                <Card key={zone.id}>
                  <CardContent className="p-4">
                    <div className="flex items-start justify-between">
                      <div>
                        <div className="flex items-center gap-2">
                          <div
                            className="h-3 w-3 rounded-full"
                            style={{ backgroundColor: zone.color }}
                          />
                          <h3 className="font-medium text-slate-900 dark:text-white">
                            {zone.name}
                          </h3>
                        </div>
                        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                          Type: {zone.type} | Points: {zone.polygon.length}
                        </p>
                        <Badge
                          variant="outline"
                          className={`mt-2 text-xs ${
                            zone.active
                              ? 'border-green-200 text-green-600'
                              : 'border-slate-200 text-slate-400'
                          }`}
                        >
                          {zone.active ? 'Active' : 'Inactive'}
                        </Badge>
                      </div>
                      <div className="flex gap-1">
                        <Button variant="ghost" size="sm" title="Edit zone">
                          <Pencil className="h-3 w-3" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          title="Delete zone"
                          onClick={() => deleteZoneMutation.mutate(zone.id)}
                          className="text-red-500 hover:text-red-700"
                        >
                          <Trash2 className="h-3 w-3" />
                        </Button>
                      </div>
                    </div>
                    {/* Mini polygon preview */}
                    <div className="mt-3 h-24 rounded-lg bg-slate-100 dark:bg-slate-800">
                      <svg viewBox="0 0 100 100" className="h-full w-full" preserveAspectRatio="xMidYMid meet">
                        <polygon
                          points={zone.polygon
                            .map(([x, y]) => `${x * 100},${y * 100}`)
                            .join(' ')}
                          fill={zone.color + '33'}
                          stroke={zone.color}
                          strokeWidth="1"
                        />
                      </svg>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          ) : (
            <div className="flex h-40 flex-col items-center justify-center text-slate-400">
              <Layers className="mb-2 h-8 w-8" />
              <p className="text-sm">No zones configured for this camera</p>
            </div>
          )}
        </div>
      )}

      {activeTab === 'rules' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <p className="text-sm text-slate-500 dark:text-slate-400">
              {rules?.length || 0} rule{(rules?.length || 0) !== 1 ? 's' : ''} configured
            </p>
            <Button size="sm" onClick={() => setRuleDialogOpen(true)}>
              <Plus className="mr-1 h-4 w-4" />
              Add Rule
            </Button>
          </div>

          {rules && rules.length > 0 ? (
            <div className="space-y-3">
              {rules.map((rule) => (
                <Card key={rule.id}>
                  <CardContent className="flex items-center justify-between p-4">
                    <div>
                      <h3 className="font-medium text-slate-900 dark:text-white">{rule.name}</h3>
                      <p className="text-xs text-slate-500 dark:text-slate-400">
                        Type: {rule.type.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase())}
                        {rule.zone_id && ' | Zone linked'}
                      </p>
                    </div>
                    <button
                      onClick={() =>
                        toggleRuleMutation.mutate({
                          ruleId: rule.id,
                          enabled: !rule.enabled,
                        })
                      }
                      className={`flex items-center gap-2 rounded-full px-3 py-1.5 text-sm font-medium transition-colors ${
                        rule.enabled
                          ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                          : 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400'
                      }`}
                    >
                      {rule.enabled ? (
                        <ToggleRight className="h-5 w-5" />
                      ) : (
                        <ToggleLeft className="h-5 w-5" />
                      )}
                      {rule.enabled ? 'Enabled' : 'Disabled'}
                    </button>
                  </CardContent>
                </Card>
              ))}
            </div>
          ) : (
            <div className="flex h-40 flex-col items-center justify-center text-slate-400">
              <ShieldCheck className="mb-2 h-8 w-8" />
              <p className="text-sm">No rules configured for this camera</p>
            </div>
          )}
        </div>
      )}

      {activeTab === 'events' && (
        <Card>
          <CardContent className="p-0">
            {events && events.length > 0 ? (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-200 bg-slate-50/50 dark:border-slate-700 dark:bg-slate-800/50">
                    <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                      Type
                    </th>
                    <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                      Severity
                    </th>
                    <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                      Message
                    </th>
                    <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                      Time
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((event) => (
                    <tr
                      key={event.id}
                      className="border-b border-slate-100 dark:border-slate-800"
                    >
                      <td className="px-4 py-3 font-medium text-slate-700 dark:text-slate-300">
                        {event.type.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase())}
                      </td>
                      <td className="px-4 py-3">
                        <Badge
                          className={
                            event.severity === 'critical'
                              ? 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
                              : event.severity === 'warning'
                              ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400'
                              : 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400'
                          }
                        >
                          {event.severity}
                        </Badge>
                      </td>
                      <td className="max-w-xs truncate px-4 py-3 text-slate-600 dark:text-slate-400">
                        {event.message}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-slate-500">
                        {new Date(event.created_at).toLocaleString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="flex h-40 flex-col items-center justify-center text-slate-400">
                <Activity className="mb-2 h-8 w-8" />
                <p className="text-sm">No recent events</p>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {activeTab === 'health' && (
        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">FPS Over Time</CardTitle>
            </CardHeader>
            <CardContent>
              {healthMetrics && healthMetrics.length > 0 ? (
                <ResponsiveContainer width="100%" height={220}>
                  <AreaChart data={healthMetrics}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis dataKey="timestamp" tick={{ fontSize: 10 }} stroke="#94a3b8" />
                    <YAxis tick={{ fontSize: 10 }} stroke="#94a3b8" />
                    <Tooltip />
                    <Area type="monotone" dataKey="fps" stroke="#3b82f6" fill="#3b82f6" fillOpacity={0.1} />
                  </AreaChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex h-48 items-center justify-center text-slate-400 text-sm">
                  No health data available
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Bitrate (kbps)</CardTitle>
            </CardHeader>
            <CardContent>
              {healthMetrics && healthMetrics.length > 0 ? (
                <ResponsiveContainer width="100%" height={220}>
                  <AreaChart data={healthMetrics}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis dataKey="timestamp" tick={{ fontSize: 10 }} stroke="#94a3b8" />
                    <YAxis tick={{ fontSize: 10 }} stroke="#94a3b8" />
                    <Tooltip />
                    <Area type="monotone" dataKey="bitrate" stroke="#22c55e" fill="#22c55e" fillOpacity={0.1} />
                  </AreaChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex h-48 items-center justify-center text-slate-400 text-sm">
                  No health data available
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Latency (ms)</CardTitle>
            </CardHeader>
            <CardContent>
              {healthMetrics && healthMetrics.length > 0 ? (
                <ResponsiveContainer width="100%" height={220}>
                  <LineChart data={healthMetrics}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis dataKey="timestamp" tick={{ fontSize: 10 }} stroke="#94a3b8" />
                    <YAxis tick={{ fontSize: 10 }} stroke="#94a3b8" />
                    <Tooltip />
                    <Line type="monotone" dataKey="latency" stroke="#f59e0b" strokeWidth={2} dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex h-48 items-center justify-center text-slate-400 text-sm">
                  No health data available
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Packet Loss (%)</CardTitle>
            </CardHeader>
            <CardContent>
              {healthMetrics && healthMetrics.length > 0 ? (
                <ResponsiveContainer width="100%" height={220}>
                  <LineChart data={healthMetrics}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis dataKey="timestamp" tick={{ fontSize: 10 }} stroke="#94a3b8" />
                    <YAxis tick={{ fontSize: 10 }} stroke="#94a3b8" />
                    <Tooltip />
                    <Line type="monotone" dataKey="packet_loss" stroke="#ef4444" strokeWidth={2} dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex h-48 items-center justify-center text-slate-400 text-sm">
                  No health data available
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {/* Zone & Rule creation dialogs */}
      <CreateZoneDialog
        open={zoneDialogOpen}
        onOpenChange={setZoneDialogOpen}
        cameraId={cameraId}
        snapshotUrl={`/api/v1/cameras/${cameraId}/snapshot`}
      />
      <CreateRuleDialog
        open={ruleDialogOpen}
        onOpenChange={setRuleDialogOpen}
        cameraId={cameraId}
      />

      {activeTab === 'settings' && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Camera Configuration</CardTitle>
            <CardDescription>Update camera settings</CardDescription>
          </CardHeader>
          <CardContent>
            <form
              onSubmit={handleSubmitSettings((data) => updateCameraMutation.mutate(data))}
              className="space-y-4 max-w-lg"
            >
              {updateCameraMutation.isSuccess && (
                <div className="rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700 dark:border-green-800 dark:bg-green-900/30 dark:text-green-400">
                  Camera settings updated successfully.
                </div>
              )}

              {updateCameraMutation.isError && (
                <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">
                  Failed to update camera settings.
                </div>
              )}

              <div className="space-y-2">
                <Label>Camera Name</Label>
                <Input {...registerSettings('name')} />
                {settingsErrors.name && (
                  <p className="text-xs text-red-500">{settingsErrors.name.message}</p>
                )}
              </div>

              <div className="space-y-2">
                <Label>Location</Label>
                <Input {...registerSettings('location')} />
                {settingsErrors.location && (
                  <p className="text-xs text-red-500">{settingsErrors.location.message}</p>
                )}
              </div>

              <div className="space-y-2">
                <Label>Stream URL</Label>
                <Input {...registerSettings('stream_url')} />
                {settingsErrors.stream_url && (
                  <p className="text-xs text-red-500">{settingsErrors.stream_url.message}</p>
                )}
              </div>

              <div className="space-y-2">
                <Label>Protocol</Label>
                <select
                  {...registerSettings('protocol')}
                  className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                >
                  <option value="rtsp">RTSP</option>
                  <option value="rtmp">RTMP</option>
                  <option value="hls">HLS</option>
                  <option value="webrtc">WebRTC</option>
                </select>
              </div>

              <Button type="submit" disabled={updateCameraMutation.isPending}>
                {updateCameraMutation.isPending ? (
                  <>
                    <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                    Saving...
                  </>
                ) : (
                  <>
                    <Save className="mr-1 h-4 w-4" />
                    Save Changes
                  </>
                )}
              </Button>
            </form>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
