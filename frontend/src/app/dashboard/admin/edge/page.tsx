'use client';

import { useState, useEffect, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Cpu,
  Plus,
  Search,
  Loader2,
  MoreHorizontal,
  Pencil,
  Trash2,
  Power,
  PowerOff,
  AlertCircle,
  CheckCircle2,
  X,
  RefreshCw,
  Activity,
  HardDrive,
  Thermometer,
  Wifi,
  WifiOff,
  Server,
  MemoryStick,
  Gauge,
  Upload,
  RotateCcw,
  Eye,
  Settings,
  BarChart3,
  Clock,
  Zap,
  MonitorSpeaker,
  Signal,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Separator } from '@/components/ui/separator';
import { Progress } from '@/components/ui/progress';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu';
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/components/ui/tabs';
import { apiClient } from '@/lib/api-client';
import { cn, formatDate, formatRelativeTime } from '@/lib/utils';

// ── Types ───────────────────────────────────────────────────────────────

interface EdgeDevice {
  id: string;
  org_id: string;
  name: string;
  device_type: string;
  ip_address: string;
  api_url: string;
  hardware_info: Record<string, any> | null;
  assigned_cameras: string[] | null;
  is_online: boolean;
  last_heartbeat: string | null;
  firmware_version: string | null;
  location: string | null;
  created_at: string;
  updated_at: string | null;
}

interface DeviceListResponse {
  status: string;
  data: EdgeDevice[];
  total: number;
}

interface EdgeDeployment {
  id: string;
  device_id: string;
  model_name: string;
  model_version: string;
  model_format: string;
  status: string;
  file_size: number | null;
  deploy_started_at: string | null;
  deploy_completed_at: string | null;
  error_message: string | null;
  created_at: string;
}

interface EdgeHealth {
  device_id: string;
  device_name: string;
  is_online: boolean;
  cpu_usage_pct: number | null;
  gpu_usage_pct: number | null;
  memory_usage_pct: number | null;
  temperature_celsius: number | null;
  disk_usage_pct: number | null;
  uptime_seconds: number | null;
  fps_processing: number | null;
  inference_latency_ms: number | null;
  last_heartbeat: string | null;
  cpu_status: string;
  gpu_status: string;
  memory_status: string;
  temperature_status: string;
  disk_status: string;
}

interface MetricsPoint {
  id: string;
  device_id: string;
  timestamp: string;
  cpu_usage_pct: number | null;
  gpu_usage_pct: number | null;
  memory_usage_pct: number | null;
  temperature_celsius: number | null;
  disk_usage_pct: number | null;
  fps_processing: number | null;
  inference_latency_ms: number | null;
  detections_count: number | null;
  uptime_seconds: number | null;
}

interface MetricsHistory {
  device_id: string;
  device_name: string;
  time_range: string;
  data_points: MetricsPoint[];
}

interface EdgeAnalytics {
  total_devices: number;
  online_devices: number;
  offline_devices: number;
  total_fps: number;
  avg_inference_latency_ms: number;
  total_detections: number;
  total_deployed_models: number;
  avg_gpu_usage_pct: number;
  avg_cpu_usage_pct: number;
  avg_memory_usage_pct: number;
  avg_temperature_celsius: number;
  devices_by_type: Record<string, number>;
}

interface DeviceFormData {
  name: string;
  device_type: string;
  ip_address: string;
  api_url: string;
  api_key: string;
  location: string;
  firmware_version: string;
  hardware_info: string;
}

interface DeployFormData {
  model_name: string;
  model_version: string;
  model_format: string;
}

// ── Constants ───────────────────────────────────────────────────────────

const DEVICE_TYPES = [
  { value: 'jetson_orin_nano', label: 'Jetson Orin Nano' },
  { value: 'jetson_orin_nx', label: 'Jetson Orin NX' },
  { value: 'jetson_agx_orin', label: 'Jetson AGX Orin' },
  { value: 'generic_gpu', label: 'Generic GPU' },
  { value: 'cpu_only', label: 'CPU Only' },
] as const;

const MODEL_FORMATS = [
  { value: 'onnx', label: 'ONNX' },
  { value: 'tensorrt', label: 'TensorRT' },
  { value: 'openvino', label: 'OpenVINO' },
] as const;

const deviceTypeConfig: Record<string, { label: string; color: string }> = {
  jetson_orin_nano: {
    label: 'Orin Nano',
    color: 'bg-green-100 text-green-700 border-green-200 dark:bg-green-900/30 dark:text-green-400 dark:border-green-800',
  },
  jetson_orin_nx: {
    label: 'Orin NX',
    color: 'bg-blue-100 text-blue-700 border-blue-200 dark:bg-blue-900/30 dark:text-blue-400 dark:border-blue-800',
  },
  jetson_agx_orin: {
    label: 'AGX Orin',
    color: 'bg-purple-100 text-purple-700 border-purple-200 dark:bg-purple-900/30 dark:text-purple-400 dark:border-purple-800',
  },
  generic_gpu: {
    label: 'GPU',
    color: 'bg-amber-100 text-amber-700 border-amber-200 dark:bg-amber-900/30 dark:text-amber-400 dark:border-amber-800',
  },
  cpu_only: {
    label: 'CPU',
    color: 'bg-slate-100 text-slate-600 border-slate-200 dark:bg-slate-800 dark:text-slate-400 dark:border-slate-700',
  },
};

const deployStatusConfig: Record<string, { label: string; color: string }> = {
  deploying: {
    label: 'Deploying',
    color: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
  },
  deployed: {
    label: 'Deployed',
    color: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  },
  failed: {
    label: 'Failed',
    color: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  },
  outdated: {
    label: 'Outdated',
    color: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
  },
};

function statusColor(status: string): string {
  if (status === 'normal') return 'text-green-500';
  if (status === 'warning') return 'text-amber-500';
  if (status === 'critical') return 'text-red-500';
  return 'text-slate-400';
}

function usageBarColor(pct: number | null): string {
  if (pct === null) return 'bg-slate-300 dark:bg-slate-600';
  if (pct >= 90) return 'bg-red-500';
  if (pct >= 70) return 'bg-amber-500';
  return 'bg-green-500';
}

function formatUptime(seconds: number | null): string {
  if (!seconds) return '--';
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

// ── Component ───────────────────────────────────────────────────────────

export default function EdgeDevicesPage() {
  const queryClient = useQueryClient();

  const [search, setSearch] = useState('');
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [detailDialogOpen, setDetailDialogOpen] = useState(false);
  const [deployDialogOpen, setDeployDialogOpen] = useState(false);
  const [selectedDevice, setSelectedDevice] = useState<EdgeDevice | null>(null);
  const [detailTab, setDetailTab] = useState('health');
  const [metricsRange, setMetricsRange] = useState('1h');

  const [deviceForm, setDeviceForm] = useState<DeviceFormData>({
    name: '',
    device_type: 'jetson_orin_nano',
    ip_address: '',
    api_url: '',
    api_key: '',
    location: '',
    firmware_version: '',
    hardware_info: '',
  });

  const [deployForm, setDeployForm] = useState<DeployFormData>({
    model_name: '',
    model_version: '',
    model_format: 'onnx',
  });

  const [deployTargetId, setDeployTargetId] = useState<string | null>(null);
  const [testingConnection, setTestingConnection] = useState(false);
  const [connectionTestResult, setConnectionTestResult] = useState<{ success: boolean; message: string } | null>(null);

  // ── Queries ───────────────────────────────────────────────────────

  const { data: devicesData, isLoading: devicesLoading } = useQuery<DeviceListResponse>({
    queryKey: ['edge-devices'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/edge/devices');
      return res.data as DeviceListResponse;
    },
    refetchInterval: 10000,
  });

  const devices = devicesData?.data || [];
  const filteredDevices = devices.filter(
    (d) =>
      d.name.toLowerCase().includes(search.toLowerCase()) ||
      d.ip_address.includes(search) ||
      (d.location || '').toLowerCase().includes(search.toLowerCase())
  );

  const { data: analytics } = useQuery<EdgeAnalytics>({
    queryKey: ['edge-analytics'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/edge/analytics');
      return res.data as EdgeAnalytics;
    },
    refetchInterval: 10000,
  });

  const { data: selectedHealth } = useQuery<EdgeHealth>({
    queryKey: ['edge-health', selectedDevice?.id],
    queryFn: async () => {
      const res = await apiClient.get(`/api/v1/edge/devices/${selectedDevice!.id}/health`);
      return res.data as EdgeHealth;
    },
    enabled: !!selectedDevice && detailDialogOpen,
    refetchInterval: 10000,
  });

  const { data: selectedMetrics } = useQuery<MetricsHistory>({
    queryKey: ['edge-metrics', selectedDevice?.id, metricsRange],
    queryFn: async () => {
      const res = await apiClient.get(
        `/api/v1/edge/devices/${selectedDevice!.id}/metrics?time_range=${metricsRange}`
      );
      return res.data as MetricsHistory;
    },
    enabled: !!selectedDevice && detailDialogOpen && detailTab === 'metrics',
    refetchInterval: 30000,
  });

  const { data: selectedDeployments } = useQuery<EdgeDeployment[]>({
    queryKey: ['edge-deployments', selectedDevice?.id],
    queryFn: async () => {
      const res = await apiClient.get(`/api/v1/edge/devices/${selectedDevice!.id}/deployments`);
      return res.data as EdgeDeployment[];
    },
    enabled: !!selectedDevice && detailDialogOpen && detailTab === 'deployments',
    refetchInterval: 15000,
  });

  // ── Mutations ─────────────────────────────────────────────────────

  const addDeviceMutation = useMutation({
    mutationFn: async (data: DeviceFormData) => {
      let hw: Record<string, any> = {};
      if (data.hardware_info) {
        try {
          hw = JSON.parse(data.hardware_info);
        } catch {
          hw = {};
        }
      }
      const payload: Record<string, any> = {
        name: data.name,
        device_type: data.device_type,
        ip_address: data.ip_address,
        api_url: data.api_url,
        location: data.location || null,
        firmware_version: data.firmware_version || null,
        hardware_info: Object.keys(hw).length > 0 ? hw : null,
      };
      if (data.api_key) payload.api_key = data.api_key;
      const res = await apiClient.post('/api/v1/edge/devices', payload);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['edge-devices'] });
      queryClient.invalidateQueries({ queryKey: ['edge-analytics'] });
      setAddDialogOpen(false);
      resetDeviceForm();
    },
  });

  const editDeviceMutation = useMutation({
    mutationFn: async ({ id, data }: { id: string; data: Partial<DeviceFormData> }) => {
      const payload: Record<string, any> = {};
      if (data.name) payload.name = data.name;
      if (data.device_type) payload.device_type = data.device_type;
      if (data.ip_address) payload.ip_address = data.ip_address;
      if (data.api_url) payload.api_url = data.api_url;
      if (data.api_key) payload.api_key = data.api_key;
      if (data.location !== undefined) payload.location = data.location || null;
      if (data.firmware_version !== undefined) payload.firmware_version = data.firmware_version || null;
      if (data.hardware_info) {
        try {
          payload.hardware_info = JSON.parse(data.hardware_info);
        } catch { /* skip */ }
      }
      const res = await apiClient.put(`/api/v1/edge/devices/${id}`, payload);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['edge-devices'] });
      setEditDialogOpen(false);
      setSelectedDevice(null);
      resetDeviceForm();
    },
  });

  const deleteDeviceMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/api/v1/edge/devices/${id}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['edge-devices'] });
      queryClient.invalidateQueries({ queryKey: ['edge-analytics'] });
    },
  });

  const deployModelMutation = useMutation({
    mutationFn: async ({ deviceId, data }: { deviceId: string; data: DeployFormData }) => {
      const res = await apiClient.post(`/api/v1/edge/devices/${deviceId}/deploy`, data);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['edge-deployments'] });
      setDeployDialogOpen(false);
      setDeployTargetId(null);
      setDeployForm({ model_name: '', model_version: '', model_format: 'onnx' });
    },
  });

  const restartDeviceMutation = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiClient.post(`/api/v1/edge/devices/${id}/restart`);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['edge-devices'] });
    },
  });

  const bulkRestartMutation = useMutation({
    mutationFn: async () => {
      const onlineDevices = devices.filter((d) => d.is_online);
      await Promise.allSettled(
        onlineDevices.map((d) => apiClient.post(`/api/v1/edge/devices/${d.id}/restart`))
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['edge-devices'] });
    },
  });

  const bulkDeployMutation = useMutation({
    mutationFn: async (data: DeployFormData) => {
      const onlineDevices = devices.filter((d) => d.is_online);
      await Promise.allSettled(
        onlineDevices.map((d) =>
          apiClient.post(`/api/v1/edge/devices/${d.id}/deploy`, data)
        )
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['edge-deployments'] });
      setDeployDialogOpen(false);
      setDeployForm({ model_name: '', model_version: '', model_format: 'onnx' });
    },
  });

  // ── Helpers ───────────────────────────────────────────────────────

  const resetDeviceForm = () => {
    setDeviceForm({
      name: '',
      device_type: 'jetson_orin_nano',
      ip_address: '',
      api_url: '',
      api_key: '',
      location: '',
      firmware_version: '',
      hardware_info: '',
    });
    setConnectionTestResult(null);
  };

  const openEditDialog = (device: EdgeDevice) => {
    setSelectedDevice(device);
    setDeviceForm({
      name: device.name,
      device_type: device.device_type,
      ip_address: device.ip_address,
      api_url: device.api_url,
      api_key: '',
      location: device.location || '',
      firmware_version: device.firmware_version || '',
      hardware_info: device.hardware_info ? JSON.stringify(device.hardware_info, null, 2) : '',
    });
    setEditDialogOpen(true);
  };

  const openDetailDialog = (device: EdgeDevice) => {
    setSelectedDevice(device);
    setDetailTab('health');
    setDetailDialogOpen(true);
  };

  const openDeployDialog = (deviceId: string | null) => {
    setDeployTargetId(deviceId);
    setDeployForm({ model_name: '', model_version: '', model_format: 'onnx' });
    setDeployDialogOpen(true);
  };

  const testConnection = async () => {
    setTestingConnection(true);
    setConnectionTestResult(null);
    try {
      const url = deviceForm.api_url.replace(/\/$/, '');
      const res = await fetch(`${url}/health`, {
        method: 'GET',
        signal: AbortSignal.timeout(10000),
      });
      if (res.ok) {
        setConnectionTestResult({ success: true, message: 'Connection successful' });
      } else {
        setConnectionTestResult({ success: false, message: `HTTP ${res.status}` });
      }
    } catch (err: any) {
      setConnectionTestResult({ success: false, message: err.message || 'Connection failed' });
    } finally {
      setTestingConnection(false);
    }
  };

  // ── Metrics chart helper (simple SVG sparkline) ───────────────────

  function Sparkline({ data, color = '#3b82f6', height = 40 }: { data: number[]; color?: string; height?: number }) {
    if (!data.length) return <div className="h-10 flex items-center justify-center text-xs text-slate-400">No data</div>;
    const max = Math.max(...data, 1);
    const min = Math.min(...data, 0);
    const range = max - min || 1;
    const w = 200;
    const points = data
      .map((v, i) => {
        const x = (i / Math.max(data.length - 1, 1)) * w;
        const y = height - ((v - min) / range) * (height - 4) - 2;
        return `${x},${y}`;
      })
      .join(' ');
    return (
      <svg viewBox={`0 0 ${w} ${height}`} className="w-full" preserveAspectRatio="none">
        <polyline fill="none" stroke={color} strokeWidth="2" points={points} />
      </svg>
    );
  }

  // ── Render ────────────────────────────────────────────────────────

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Edge Devices</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Manage Jetson and edge compute devices running IBVAP border video analytics pipelines
          </p>
        </div>
        <div className="flex items-center gap-2">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm">
                <Zap className="mr-1 h-4 w-4" />
                Bulk Actions
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => openDeployDialog(null)}>
                <Upload className="mr-2 h-4 w-4" />
                Deploy Model to All
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onClick={() => bulkRestartMutation.mutate()}
                className="text-amber-600 dark:text-amber-400"
              >
                <RotateCcw className="mr-2 h-4 w-4" />
                Restart All Online
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <Button onClick={() => { resetDeviceForm(); setAddDialogOpen(true); }}>
            <Plus className="mr-1 h-4 w-4" />
            Add Device
          </Button>
        </div>
      </div>

      {/* Analytics Panel */}
      {analytics && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <Card>
            <CardContent className="p-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-medium text-slate-500 dark:text-slate-400">Total Devices</p>
                  <p className="text-2xl font-bold text-slate-900 dark:text-white">{analytics.total_devices}</p>
                </div>
                <Server className="h-8 w-8 text-slate-300 dark:text-slate-600" />
              </div>
              <div className="mt-1 flex items-center gap-2 text-xs">
                <span className="flex items-center text-green-600 dark:text-green-400">
                  <Wifi className="mr-0.5 h-3 w-3" /> {analytics.online_devices} online
                </span>
                <span className="text-slate-400">|</span>
                <span className="flex items-center text-slate-400">
                  <WifiOff className="mr-0.5 h-3 w-3" /> {analytics.offline_devices} offline
                </span>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="p-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-medium text-slate-500 dark:text-slate-400">Aggregate FPS</p>
                  <p className="text-2xl font-bold text-slate-900 dark:text-white">{analytics.total_fps.toFixed(1)}</p>
                </div>
                <Gauge className="h-8 w-8 text-blue-300 dark:text-blue-600" />
              </div>
              <p className="mt-1 text-xs text-slate-400">
                {analytics.total_deployed_models} models deployed
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="p-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-medium text-slate-500 dark:text-slate-400">Avg Latency</p>
                  <p className="text-2xl font-bold text-slate-900 dark:text-white">
                    {analytics.avg_inference_latency_ms.toFixed(1)}<span className="text-sm font-normal text-slate-400">ms</span>
                  </p>
                </div>
                <Activity className="h-8 w-8 text-amber-300 dark:text-amber-600" />
              </div>
              <p className="mt-1 text-xs text-slate-400">
                Avg GPU: {analytics.avg_gpu_usage_pct.toFixed(0)}%
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="p-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-medium text-slate-500 dark:text-slate-400">Total Detections</p>
                  <p className="text-2xl font-bold text-slate-900 dark:text-white">
                    {analytics.total_detections.toLocaleString()}
                  </p>
                </div>
                <BarChart3 className="h-8 w-8 text-green-300 dark:text-green-600" />
              </div>
              <p className="mt-1 text-xs text-slate-400">Last hour</p>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="p-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-medium text-slate-500 dark:text-slate-400">Avg Temp</p>
                  <p className="text-2xl font-bold text-slate-900 dark:text-white">
                    {analytics.avg_temperature_celsius.toFixed(0)}<span className="text-sm font-normal text-slate-400">C</span>
                  </p>
                </div>
                <Thermometer className="h-8 w-8 text-red-300 dark:text-red-600" />
              </div>
              <p className="mt-1 text-xs text-slate-400">
                Avg Memory: {analytics.avg_memory_usage_pct.toFixed(0)}%
              </p>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Search */}
      <div className="relative max-w-sm">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
        <Input
          placeholder="Search devices by name, IP, or location..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="pl-10"
        />
      </div>

      {/* Device Grid */}
      {devicesLoading ? (
        <div className="flex h-64 items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
        </div>
      ) : filteredDevices.length === 0 ? (
        <Card>
          <CardContent className="flex h-64 flex-col items-center justify-center text-slate-400">
            <Server className="mb-3 h-10 w-10" />
            <p className="text-sm font-medium">No edge devices found</p>
            <p className="mt-1 text-xs">
              {search ? 'Try adjusting your search' : 'Register your first edge device to get started'}
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {filteredDevices.map((device) => {
            const dtConfig = deviceTypeConfig[device.device_type] || deviceTypeConfig.cpu_only;
            const cameraCount = device.assigned_cameras?.length || 0;
            return (
              <Card
                key={device.id}
                className={cn(
                  'cursor-pointer transition-shadow hover:shadow-md',
                  !device.is_online && 'opacity-70'
                )}
                onClick={() => openDetailDialog(device)}
              >
                <CardContent className="p-4">
                  {/* Header row */}
                  <div className="flex items-start justify-between">
                    <div className="min-w-0 flex-1">
                      <h3 className="truncate text-sm font-semibold text-slate-900 dark:text-white">
                        {device.name}
                      </h3>
                      <p className="mt-0.5 truncate text-xs text-slate-500 dark:text-slate-400">
                        {device.ip_address}
                      </p>
                    </div>
                    <div className="ml-2 flex items-center gap-1.5">
                      <Badge className={cn('text-[10px] border', dtConfig.color)}>
                        {dtConfig.label}
                      </Badge>
                      <div
                        className={cn(
                          'h-2.5 w-2.5 rounded-full',
                          device.is_online ? 'bg-green-500' : 'bg-slate-300 dark:bg-slate-600'
                        )}
                        title={device.is_online ? 'Online' : 'Offline'}
                      />
                    </div>
                  </div>

                  {/* Info row */}
                  <div className="mt-3 flex items-center gap-3 text-xs text-slate-500 dark:text-slate-400">
                    {device.location && (
                      <span className="truncate" title={device.location}>
                        {device.location}
                      </span>
                    )}
                    {cameraCount > 0 && (
                      <span>{cameraCount} cam{cameraCount !== 1 ? 's' : ''}</span>
                    )}
                  </div>

                  {/* Heartbeat */}
                  <div className="mt-2 flex items-center gap-1 text-xs text-slate-400">
                    <Clock className="h-3 w-3" />
                    {device.last_heartbeat
                      ? formatRelativeTime(device.last_heartbeat)
                      : 'No heartbeat'}
                  </div>

                  {/* Actions row */}
                  <div className="mt-3 flex items-center justify-end gap-1" onClick={(e) => e.stopPropagation()}>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      title="Deploy model"
                      onClick={() => openDeployDialog(device.id)}
                    >
                      <Upload className="h-3.5 w-3.5" />
                    </Button>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="icon" className="h-7 w-7">
                          <MoreHorizontal className="h-3.5 w-3.5" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={() => openEditDialog(device)}>
                          <Pencil className="mr-2 h-4 w-4" />
                          Edit
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={() => openDeployDialog(device.id)}>
                          <Upload className="mr-2 h-4 w-4" />
                          Deploy Model
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onClick={() => restartDeviceMutation.mutate(device.id)}
                          disabled={!device.is_online}
                        >
                          <RotateCcw className="mr-2 h-4 w-4" />
                          Restart
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          onClick={() => { if (confirm(`Remove "${device.name}"?`)) deleteDeviceMutation.mutate(device.id); }}
                          className="text-red-600 dark:text-red-400"
                        >
                          <Trash2 className="mr-2 h-4 w-4" />
                          Remove
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      {/* ── Add Device Dialog ────────────────────────────────────────── */}
      <Dialog open={addDialogOpen} onOpenChange={(open) => { setAddDialogOpen(open); if (!open) resetDeviceForm(); }}>
        <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Register Edge Device</DialogTitle>
            <DialogDescription>
              Add a new Jetson or edge compute device to your deployment.
            </DialogDescription>
          </DialogHeader>

          {addDeviceMutation.isError && (
            <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">
              <AlertCircle className="h-4 w-4 shrink-0" />
              {(addDeviceMutation.error as any)?.message || 'Failed to register device'}
            </div>
          )}

          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Device Name</Label>
              <Input
                placeholder="Warehouse Jetson 01"
                value={deviceForm.name}
                onChange={(e) => setDeviceForm((p) => ({ ...p, name: e.target.value }))}
              />
            </div>
            <div className="space-y-2">
              <Label>Device Type</Label>
              <Select value={deviceForm.device_type} onValueChange={(v) => setDeviceForm((p) => ({ ...p, device_type: v }))}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {DEVICE_TYPES.map((dt) => (
                    <SelectItem key={dt.value} value={dt.value}>{dt.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label>IP Address</Label>
                <Input
                  placeholder="192.168.1.50"
                  value={deviceForm.ip_address}
                  onChange={(e) => setDeviceForm((p) => ({ ...p, ip_address: e.target.value }))}
                />
              </div>
              <div className="space-y-2">
                <Label>API URL</Label>
                <Input
                  placeholder="http://192.168.1.50:8080"
                  value={deviceForm.api_url}
                  onChange={(e) => setDeviceForm((p) => ({ ...p, api_url: e.target.value }))}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label>API Key (optional)</Label>
              <Input
                type="password"
                placeholder="Shared secret for device authentication"
                value={deviceForm.api_key}
                onChange={(e) => setDeviceForm((p) => ({ ...p, api_key: e.target.value }))}
              />
            </div>
            <div className="space-y-2">
              <Label>Location</Label>
              <Input
                placeholder="Building A, Server Room 2"
                value={deviceForm.location}
                onChange={(e) => setDeviceForm((p) => ({ ...p, location: e.target.value }))}
              />
            </div>
            <div className="space-y-2">
              <Label>Firmware Version (optional)</Label>
              <Input
                placeholder="JetPack 6.0"
                value={deviceForm.firmware_version}
                onChange={(e) => setDeviceForm((p) => ({ ...p, firmware_version: e.target.value }))}
              />
            </div>
            <div className="space-y-2">
              <Label>Hardware Info (JSON, optional)</Label>
              <Textarea
                placeholder={'{\n  "gpu_model": "NVIDIA Orin Nano 8GB",\n  "cpu_cores": 6,\n  "ram_gb": 8,\n  "storage_gb": 256\n}'}
                value={deviceForm.hardware_info}
                onChange={(e) => setDeviceForm((p) => ({ ...p, hardware_info: e.target.value }))}
                rows={4}
                className="font-mono text-xs"
              />
            </div>

            {/* Test Connection */}
            <div className="flex items-center gap-3">
              <Button
                variant="outline"
                size="sm"
                onClick={testConnection}
                disabled={!deviceForm.api_url || testingConnection}
              >
                {testingConnection ? (
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                ) : (
                  <Signal className="mr-1 h-4 w-4" />
                )}
                Test Connection
              </Button>
              {connectionTestResult && (
                <span className={cn('text-xs font-medium', connectionTestResult.success ? 'text-green-600' : 'text-red-600')}>
                  {connectionTestResult.success ? (
                    <CheckCircle2 className="mr-1 inline h-3.5 w-3.5" />
                  ) : (
                    <AlertCircle className="mr-1 inline h-3.5 w-3.5" />
                  )}
                  {connectionTestResult.message}
                </span>
              )}
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => { setAddDialogOpen(false); resetDeviceForm(); }}>Cancel</Button>
            <Button onClick={() => addDeviceMutation.mutate(deviceForm)} disabled={addDeviceMutation.isPending || !deviceForm.name || !deviceForm.ip_address || !deviceForm.api_url}>
              {addDeviceMutation.isPending ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Plus className="mr-1 h-4 w-4" />}
              Register Device
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Edit Device Dialog ───────────────────────────────────────── */}
      <Dialog open={editDialogOpen} onOpenChange={(open) => { setEditDialogOpen(open); if (!open) { setSelectedDevice(null); resetDeviceForm(); } }}>
        <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Edit Edge Device</DialogTitle>
            <DialogDescription>Update device configuration. Leave API key empty to keep current.</DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Device Name</Label>
              <Input value={deviceForm.name} onChange={(e) => setDeviceForm((p) => ({ ...p, name: e.target.value }))} />
            </div>
            <div className="space-y-2">
              <Label>Device Type</Label>
              <Select value={deviceForm.device_type} onValueChange={(v) => setDeviceForm((p) => ({ ...p, device_type: v }))}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {DEVICE_TYPES.map((dt) => (
                    <SelectItem key={dt.value} value={dt.value}>{dt.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label>IP Address</Label>
                <Input value={deviceForm.ip_address} onChange={(e) => setDeviceForm((p) => ({ ...p, ip_address: e.target.value }))} />
              </div>
              <div className="space-y-2">
                <Label>API URL</Label>
                <Input value={deviceForm.api_url} onChange={(e) => setDeviceForm((p) => ({ ...p, api_url: e.target.value }))} />
              </div>
            </div>
            <div className="space-y-2">
              <Label>API Key (leave empty to keep current)</Label>
              <Input type="password" value={deviceForm.api_key} onChange={(e) => setDeviceForm((p) => ({ ...p, api_key: e.target.value }))} />
            </div>
            <div className="space-y-2">
              <Label>Location</Label>
              <Input value={deviceForm.location} onChange={(e) => setDeviceForm((p) => ({ ...p, location: e.target.value }))} />
            </div>
            <div className="space-y-2">
              <Label>Hardware Info (JSON)</Label>
              <Textarea
                value={deviceForm.hardware_info}
                onChange={(e) => setDeviceForm((p) => ({ ...p, hardware_info: e.target.value }))}
                rows={4}
                className="font-mono text-xs"
              />
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => { setEditDialogOpen(false); setSelectedDevice(null); resetDeviceForm(); }}>Cancel</Button>
            <Button
              onClick={() => selectedDevice && editDeviceMutation.mutate({ id: selectedDevice.id, data: deviceForm })}
              disabled={editDeviceMutation.isPending}
            >
              {editDeviceMutation.isPending ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <CheckCircle2 className="mr-1 h-4 w-4" />}
              Save Changes
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Device Detail Dialog ─────────────────────────────────────── */}
      <Dialog open={detailDialogOpen} onOpenChange={(open) => { setDetailDialogOpen(open); if (!open) setSelectedDevice(null); }}>
        <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
          {selectedDevice && (
            <>
              <DialogHeader>
                <div className="flex items-center gap-3">
                  <div className={cn('h-3 w-3 rounded-full', selectedDevice.is_online ? 'bg-green-500' : 'bg-slate-300')} />
                  <DialogTitle>{selectedDevice.name}</DialogTitle>
                  <Badge className={cn('text-xs border', (deviceTypeConfig[selectedDevice.device_type] || deviceTypeConfig.cpu_only).color)}>
                    {(deviceTypeConfig[selectedDevice.device_type] || deviceTypeConfig.cpu_only).label}
                  </Badge>
                </div>
                <DialogDescription>
                  {selectedDevice.ip_address} {selectedDevice.location && ` -- ${selectedDevice.location}`}
                </DialogDescription>
              </DialogHeader>

              <Tabs value={detailTab} onValueChange={setDetailTab}>
                <TabsList className="w-full grid grid-cols-3">
                  <TabsTrigger value="health">Health</TabsTrigger>
                  <TabsTrigger value="metrics">Metrics</TabsTrigger>
                  <TabsTrigger value="deployments">Models</TabsTrigger>
                </TabsList>

                {/* Health Tab */}
                <TabsContent value="health" className="space-y-4 mt-4">
                  {selectedHealth ? (
                    <>
                      <div className="grid grid-cols-2 gap-3">
                        {/* CPU */}
                        <div className="rounded-lg border p-3 dark:border-slate-700">
                          <div className="flex items-center justify-between mb-1">
                            <span className="text-xs font-medium text-slate-500">CPU</span>
                            <span className={cn('text-xs font-semibold', statusColor(selectedHealth.cpu_status))}>
                              {selectedHealth.cpu_usage_pct?.toFixed(0) ?? '--'}%
                            </span>
                          </div>
                          <Progress
                            value={selectedHealth.cpu_usage_pct ?? 0}
                            className="h-2"
                          />
                        </div>

                        {/* GPU */}
                        <div className="rounded-lg border p-3 dark:border-slate-700">
                          <div className="flex items-center justify-between mb-1">
                            <span className="text-xs font-medium text-slate-500">GPU</span>
                            <span className={cn('text-xs font-semibold', statusColor(selectedHealth.gpu_status))}>
                              {selectedHealth.gpu_usage_pct?.toFixed(0) ?? '--'}%
                            </span>
                          </div>
                          <Progress
                            value={selectedHealth.gpu_usage_pct ?? 0}
                            className="h-2"
                          />
                        </div>

                        {/* Memory */}
                        <div className="rounded-lg border p-3 dark:border-slate-700">
                          <div className="flex items-center justify-between mb-1">
                            <span className="text-xs font-medium text-slate-500">Memory</span>
                            <span className={cn('text-xs font-semibold', statusColor(selectedHealth.memory_status))}>
                              {selectedHealth.memory_usage_pct?.toFixed(0) ?? '--'}%
                            </span>
                          </div>
                          <Progress
                            value={selectedHealth.memory_usage_pct ?? 0}
                            className="h-2"
                          />
                        </div>

                        {/* Disk */}
                        <div className="rounded-lg border p-3 dark:border-slate-700">
                          <div className="flex items-center justify-between mb-1">
                            <span className="text-xs font-medium text-slate-500">Disk</span>
                            <span className={cn('text-xs font-semibold', statusColor(selectedHealth.disk_status))}>
                              {selectedHealth.disk_usage_pct?.toFixed(0) ?? '--'}%
                            </span>
                          </div>
                          <Progress
                            value={selectedHealth.disk_usage_pct ?? 0}
                            className="h-2"
                          />
                        </div>
                      </div>

                      {/* Temperature + Performance row */}
                      <div className="grid grid-cols-3 gap-3">
                        <div className="rounded-lg border p-3 text-center dark:border-slate-700">
                          <Thermometer className={cn('mx-auto h-5 w-5 mb-1', statusColor(selectedHealth.temperature_status))} />
                          <p className="text-lg font-bold text-slate-900 dark:text-white">
                            {selectedHealth.temperature_celsius?.toFixed(0) ?? '--'}
                            <span className="text-xs font-normal text-slate-400">C</span>
                          </p>
                          <p className="text-[10px] text-slate-400">Temperature</p>
                        </div>
                        <div className="rounded-lg border p-3 text-center dark:border-slate-700">
                          <Gauge className="mx-auto h-5 w-5 mb-1 text-blue-500" />
                          <p className="text-lg font-bold text-slate-900 dark:text-white">
                            {selectedHealth.fps_processing?.toFixed(1) ?? '--'}
                          </p>
                          <p className="text-[10px] text-slate-400">FPS</p>
                        </div>
                        <div className="rounded-lg border p-3 text-center dark:border-slate-700">
                          <Activity className="mx-auto h-5 w-5 mb-1 text-amber-500" />
                          <p className="text-lg font-bold text-slate-900 dark:text-white">
                            {selectedHealth.inference_latency_ms?.toFixed(1) ?? '--'}
                            <span className="text-xs font-normal text-slate-400">ms</span>
                          </p>
                          <p className="text-[10px] text-slate-400">Latency</p>
                        </div>
                      </div>

                      <div className="flex items-center justify-between rounded-lg border px-3 py-2 text-xs dark:border-slate-700">
                        <span className="text-slate-500">Uptime</span>
                        <span className="font-medium text-slate-700 dark:text-slate-300">{formatUptime(selectedHealth.uptime_seconds)}</span>
                      </div>
                      <div className="flex items-center justify-between rounded-lg border px-3 py-2 text-xs dark:border-slate-700">
                        <span className="text-slate-500">Last Heartbeat</span>
                        <span className="font-medium text-slate-700 dark:text-slate-300">
                          {selectedHealth.last_heartbeat ? formatRelativeTime(selectedHealth.last_heartbeat) : 'Never'}
                        </span>
                      </div>
                    </>
                  ) : (
                    <div className="flex h-32 items-center justify-center">
                      <Loader2 className="h-6 w-6 animate-spin text-blue-500" />
                    </div>
                  )}
                </TabsContent>

                {/* Metrics Tab */}
                <TabsContent value="metrics" className="space-y-4 mt-4">
                  <div className="flex items-center gap-2">
                    <Label className="text-xs">Range:</Label>
                    {['1h', '6h', '24h', '7d'].map((r) => (
                      <Button
                        key={r}
                        variant={metricsRange === r ? 'default' : 'outline'}
                        size="sm"
                        className="h-7 text-xs"
                        onClick={() => setMetricsRange(r)}
                      >
                        {r}
                      </Button>
                    ))}
                  </div>

                  {selectedMetrics && selectedMetrics.data_points.length > 0 ? (
                    <div className="grid grid-cols-2 gap-4">
                      <div className="space-y-1">
                        <p className="text-xs font-medium text-slate-500">CPU Usage %</p>
                        <Sparkline data={selectedMetrics.data_points.map((p) => p.cpu_usage_pct ?? 0)} color="#3b82f6" />
                      </div>
                      <div className="space-y-1">
                        <p className="text-xs font-medium text-slate-500">GPU Usage %</p>
                        <Sparkline data={selectedMetrics.data_points.map((p) => p.gpu_usage_pct ?? 0)} color="#8b5cf6" />
                      </div>
                      <div className="space-y-1">
                        <p className="text-xs font-medium text-slate-500">Memory Usage %</p>
                        <Sparkline data={selectedMetrics.data_points.map((p) => p.memory_usage_pct ?? 0)} color="#f59e0b" />
                      </div>
                      <div className="space-y-1">
                        <p className="text-xs font-medium text-slate-500">Temperature C</p>
                        <Sparkline data={selectedMetrics.data_points.map((p) => p.temperature_celsius ?? 0)} color="#ef4444" />
                      </div>
                      <div className="space-y-1">
                        <p className="text-xs font-medium text-slate-500">Processing FPS</p>
                        <Sparkline data={selectedMetrics.data_points.map((p) => p.fps_processing ?? 0)} color="#22c55e" />
                      </div>
                      <div className="space-y-1">
                        <p className="text-xs font-medium text-slate-500">Inference Latency ms</p>
                        <Sparkline data={selectedMetrics.data_points.map((p) => p.inference_latency_ms ?? 0)} color="#f97316" />
                      </div>
                    </div>
                  ) : selectedMetrics ? (
                    <div className="flex h-32 items-center justify-center text-sm text-slate-400">
                      No metrics data for this time range
                    </div>
                  ) : (
                    <div className="flex h-32 items-center justify-center">
                      <Loader2 className="h-6 w-6 animate-spin text-blue-500" />
                    </div>
                  )}
                </TabsContent>

                {/* Deployments Tab */}
                <TabsContent value="deployments" className="space-y-4 mt-4">
                  <div className="flex items-center justify-between">
                    <p className="text-sm font-medium text-slate-700 dark:text-slate-300">Deployed Models</p>
                    <Button size="sm" onClick={() => openDeployDialog(selectedDevice.id)}>
                      <Upload className="mr-1 h-3.5 w-3.5" />
                      Deploy
                    </Button>
                  </div>

                  {selectedDeployments && selectedDeployments.length > 0 ? (
                    <div className="space-y-2">
                      {selectedDeployments.map((dep) => {
                        const sc = deployStatusConfig[dep.status] || deployStatusConfig.failed;
                        return (
                          <div key={dep.id} className="flex items-center justify-between rounded-lg border px-3 py-2 dark:border-slate-700">
                            <div>
                              <p className="text-sm font-medium text-slate-900 dark:text-white">
                                {dep.model_name}
                                <span className="ml-1 text-xs text-slate-400">v{dep.model_version}</span>
                              </p>
                              <p className="text-xs text-slate-400">
                                {dep.model_format.toUpperCase()}
                                {dep.file_size && ` -- ${(dep.file_size / (1024 * 1024)).toFixed(1)} MB`}
                                {dep.deploy_completed_at && ` -- ${formatDate(dep.deploy_completed_at, 'MMM d HH:mm')}`}
                              </p>
                              {dep.error_message && (
                                <p className="mt-0.5 text-xs text-red-500 truncate max-w-xs" title={dep.error_message}>
                                  {dep.error_message}
                                </p>
                              )}
                            </div>
                            <Badge className={cn('text-xs', sc.color)}>{sc.label}</Badge>
                          </div>
                        );
                      })}
                    </div>
                  ) : selectedDeployments ? (
                    <div className="flex h-24 items-center justify-center text-sm text-slate-400">
                      No models deployed to this device
                    </div>
                  ) : (
                    <div className="flex h-24 items-center justify-center">
                      <Loader2 className="h-5 w-5 animate-spin text-blue-500" />
                    </div>
                  )}
                </TabsContent>
              </Tabs>
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* ── Deploy Model Dialog ──────────────────────────────────────── */}
      <Dialog open={deployDialogOpen} onOpenChange={(open) => { setDeployDialogOpen(open); if (!open) { setDeployTargetId(null); setDeployForm({ model_name: '', model_version: '', model_format: 'onnx' }); } }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>
              {deployTargetId ? 'Deploy Model' : 'Deploy Model to All Devices'}
            </DialogTitle>
            <DialogDescription>
              {deployTargetId
                ? 'Push a model to the selected edge device.'
                : 'Push a model to all online edge devices.'}
            </DialogDescription>
          </DialogHeader>

          {(deployModelMutation.isError || bulkDeployMutation.isError) && (
            <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">
              <AlertCircle className="h-4 w-4 shrink-0" />
              Deployment failed. Check device connectivity.
            </div>
          )}

          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Model Name</Label>
              <Input
                placeholder="yolov8n-coco"
                value={deployForm.model_name}
                onChange={(e) => setDeployForm((p) => ({ ...p, model_name: e.target.value }))}
              />
            </div>
            <div className="space-y-2">
              <Label>Model Version</Label>
              <Input
                placeholder="v1.2.0"
                value={deployForm.model_version}
                onChange={(e) => setDeployForm((p) => ({ ...p, model_version: e.target.value }))}
              />
            </div>
            <div className="space-y-2">
              <Label>Model Format</Label>
              <Select value={deployForm.model_format} onValueChange={(v) => setDeployForm((p) => ({ ...p, model_format: v }))}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {MODEL_FORMATS.map((f) => (
                    <SelectItem key={f.value} value={f.value}>{f.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setDeployDialogOpen(false)}>Cancel</Button>
            <Button
              onClick={() => {
                if (deployTargetId) {
                  deployModelMutation.mutate({ deviceId: deployTargetId, data: deployForm });
                } else {
                  bulkDeployMutation.mutate(deployForm);
                }
              }}
              disabled={
                deployModelMutation.isPending ||
                bulkDeployMutation.isPending ||
                !deployForm.model_name ||
                !deployForm.model_version
              }
            >
              {(deployModelMutation.isPending || bulkDeployMutation.isPending) ? (
                <Loader2 className="mr-1 h-4 w-4 animate-spin" />
              ) : (
                <Upload className="mr-1 h-4 w-4" />
              )}
              {deployTargetId ? 'Deploy' : 'Deploy to All'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
