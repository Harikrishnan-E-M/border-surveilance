'use client';

import { useState, useMemo, useCallback, useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts';
import { format, subDays, parseISO } from 'date-fns';
import {
  Loader2,
  AlertTriangle,
  ShieldAlert,
  Activity,
  Eye,
  CheckCircle2,
  XCircle,
  RefreshCw,
  Settings2,
  BarChart3,
  Clock,
  Camera,
  MapPin,
  TrendingUp,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  CalendarDays,
  Info,
  ChevronDown,
  Zap,
  Database,
} from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { apiClient } from '@/lib/api-client';
import { cn, formatDate, formatRelativeTime } from '@/lib/utils';

// -------------------------------------------------------------------
// Types
// -------------------------------------------------------------------

interface CameraOption {
  id: string;
  name: string;
}

interface ZoneOption {
  id: string;
  name: string;
  camera_id: string;
}

interface AnomalyEventItem {
  id: string;
  org_id: string;
  camera_id: string;
  camera_name: string;
  zone_id: string | null;
  zone_name: string | null;
  anomaly_type: string;
  severity: string;
  confidence: number;
  description: string;
  baseline_value: number | null;
  observed_value: number | null;
  deviation_sigma: number | null;
  metadata_json: Record<string, unknown> | null;
  thumbnail_path: string | null;
  is_acknowledged: boolean;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
  created_at: string;
}

interface AnomalyStatsBreakdown {
  label: string;
  count: number;
}

interface TimelinePoint {
  timestamp: string;
  count: number;
  critical: number;
  warning: number;
  info: number;
}

interface AnomalyStatsData {
  total: number;
  by_type: AnomalyStatsBreakdown[];
  by_severity: AnomalyStatsBreakdown[];
  by_camera: AnomalyStatsBreakdown[];
  acknowledged_count: number;
  unacknowledged_count: number;
  avg_confidence: number;
  timeline: TimelinePoint[];
}

interface BaselineCameraStatus {
  camera_id: string;
  camera_name: string;
  zone_id: string | null;
  zone_name: string | null;
  baseline_type: string;
  sample_count: number;
  is_stale: boolean;
  updated_at: string | null;
}

interface PaginationMeta {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

// -------------------------------------------------------------------
// Severity / Type config
// -------------------------------------------------------------------

const SEVERITY_CONFIG: Record<string, { color: string; bg: string; icon: typeof AlertTriangle }> = {
  critical: {
    color: 'text-red-700 dark:text-red-400',
    bg: 'bg-red-100 dark:bg-red-900/30 border-red-200 dark:border-red-800',
    icon: XCircle,
  },
  warning: {
    color: 'text-amber-700 dark:text-amber-400',
    bg: 'bg-amber-100 dark:bg-amber-900/30 border-amber-200 dark:border-amber-800',
    icon: AlertTriangle,
  },
  info: {
    color: 'text-blue-700 dark:text-blue-400',
    bg: 'bg-blue-100 dark:bg-blue-900/30 border-blue-200 dark:border-blue-800',
    icon: Info,
  },
};

const TYPE_LABELS: Record<string, string> = {
  count: 'Count',
  temporal: 'Temporal',
  spatial: 'Spatial',
  behavioral: 'Behavioral',
  frequency: 'Frequency',
};

// -------------------------------------------------------------------
// Custom Tooltip
// -------------------------------------------------------------------

interface TooltipPayloadEntry {
  name: string;
  value: number;
  color: string;
}

function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: TooltipPayloadEntry[];
  label?: string;
}) {
  if (!active || !payload || !label) return null;

  let formatted = label;
  try {
    formatted = format(parseISO(label), 'MMM d, HH:mm');
  } catch {
    // keep raw
  }

  return (
    <div className="rounded-lg border bg-background p-3 shadow-md">
      <p className="mb-1.5 text-xs font-medium text-muted-foreground">{formatted}</p>
      {payload.map((entry, idx) => (
        <div key={idx} className="flex items-center gap-2 text-sm">
          <div
            className="h-2.5 w-2.5 rounded-full"
            style={{ backgroundColor: entry.color }}
          />
          <span className="text-muted-foreground">{entry.name}:</span>
          <span className="font-semibold">{entry.value.toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}

// -------------------------------------------------------------------
// Sort helpers
// -------------------------------------------------------------------

type SortField = 'created_at' | 'anomaly_type' | 'severity' | 'confidence' | 'camera_name';
type SortDir = 'asc' | 'desc';

// -------------------------------------------------------------------
// Page component
// -------------------------------------------------------------------

export default function AnomalyDetectionPage() {
  const queryClient = useQueryClient();

  // Filters
  const [dateFrom, setDateFrom] = useState(() => format(subDays(new Date(), 7), 'yyyy-MM-dd'));
  const [dateTo, setDateTo] = useState(() => format(new Date(), 'yyyy-MM-dd'));
  const [selectedCamera, setSelectedCamera] = useState<string>('all');
  const [selectedZone, setSelectedZone] = useState<string>('all');
  const [selectedType, setSelectedType] = useState<string>('all');
  const [selectedSeverity, setSelectedSeverity] = useState<string>('all');
  const [selectedAckStatus, setSelectedAckStatus] = useState<string>('all');
  const [currentPage, setCurrentPage] = useState(1);
  const pageSize = 20;

  // Table sorting
  const [sortField, setSortField] = useState<SortField>('created_at');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  // Sensitivity dialog
  const [sensitivityOpen, setSensitivityOpen] = useState(false);
  const [sensitivityLevel, setSensitivityLevel] = useState<string>('medium');

  // WebSocket for real-time updates
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const wsUrl =
      process.env.NEXT_PUBLIC_WS_URL ||
      `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/api/v1/ws/anomalies`;

    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onmessage = () => {
        // Invalidate queries to refresh data on new anomaly
        queryClient.invalidateQueries({ queryKey: ['anomaly-events'] });
        queryClient.invalidateQueries({ queryKey: ['anomaly-stats'] });
      };

      ws.onerror = () => {
        // Silent - WebSocket is optional
      };

      return () => {
        ws.close();
      };
    } catch {
      // WebSocket not available
    }
  }, [queryClient]);

  // -------------------------------------------------------------------
  // Queries
  // -------------------------------------------------------------------

  const { data: cameras } = useQuery<CameraOption[]>({
    queryKey: ['cameras-list'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/cameras', {
        params: { page_size: 200 },
      });
      return res.data?.results ?? res.data?.data ?? res.data ?? [];
    },
  });

  const { data: zones } = useQuery<ZoneOption[]>({
    queryKey: ['zones-list', selectedCamera],
    queryFn: async () => {
      const params: Record<string, string> = {};
      if (selectedCamera !== 'all') params.camera_id = selectedCamera;
      const res = await apiClient.get('/api/v1/zones', { params });
      return res.data?.results ?? res.data?.data ?? res.data ?? [];
    },
  });

  const buildFilterParams = useCallback(() => {
    const params: Record<string, string> = {};
    if (dateFrom) params.start_date = new Date(dateFrom).toISOString();
    if (dateTo) params.end_date = new Date(dateTo + 'T23:59:59').toISOString();
    if (selectedCamera !== 'all') params.camera_id = selectedCamera;
    if (selectedZone !== 'all') params.zone_id = selectedZone;
    if (selectedType !== 'all') params.anomaly_type = selectedType;
    if (selectedSeverity !== 'all') params.severity = selectedSeverity;
    if (selectedAckStatus !== 'all') params.is_acknowledged = selectedAckStatus;
    return params;
  }, [dateFrom, dateTo, selectedCamera, selectedZone, selectedType, selectedSeverity, selectedAckStatus]);

  const {
    data: eventsResponse,
    isLoading: eventsLoading,
    isError: eventsError,
    refetch: refetchEvents,
  } = useQuery<{ data: AnomalyEventItem[]; meta: PaginationMeta }>({
    queryKey: ['anomaly-events', dateFrom, dateTo, selectedCamera, selectedZone, selectedType, selectedSeverity, selectedAckStatus, currentPage],
    queryFn: async () => {
      const params = {
        ...buildFilterParams(),
        page: String(currentPage),
        page_size: String(pageSize),
      };
      const res = await apiClient.get('/api/v1/anomalies', { params });
      return res.data;
    },
  });

  const {
    data: statsResponse,
    isLoading: statsLoading,
  } = useQuery<{ data: AnomalyStatsData }>({
    queryKey: ['anomaly-stats', dateFrom, dateTo, selectedCamera],
    queryFn: async () => {
      const params: Record<string, string> = {};
      if (dateFrom) params.start_date = new Date(dateFrom).toISOString();
      if (dateTo) params.end_date = new Date(dateTo + 'T23:59:59').toISOString();
      if (selectedCamera !== 'all') params.camera_id = selectedCamera;
      const res = await apiClient.get('/api/v1/anomalies/stats', { params });
      return res.data;
    },
  });

  const {
    data: baselinesResponse,
    isLoading: baselinesLoading,
    refetch: refetchBaselines,
  } = useQuery<{ data: BaselineCameraStatus[] }>({
    queryKey: ['anomaly-baselines'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/anomalies/baselines');
      return res.data;
    },
  });

  // -------------------------------------------------------------------
  // Mutations
  // -------------------------------------------------------------------

  const acknowledgeMutation = useMutation({
    mutationFn: async (anomalyId: string) => {
      const res = await apiClient.post(`/api/v1/anomalies/${anomalyId}/acknowledge`, {});
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['anomaly-events'] });
      queryClient.invalidateQueries({ queryKey: ['anomaly-stats'] });
    },
  });

  const rebuildMutation = useMutation({
    mutationFn: async () => {
      const res = await apiClient.post('/api/v1/anomalies/baselines/rebuild', { days: 30 });
      return res.data;
    },
    onSuccess: () => {
      refetchBaselines();
    },
  });

  const sensitivityMutation = useMutation({
    mutationFn: async (level: string) => {
      const res = await apiClient.put('/api/v1/anomalies/sensitivity', {
        sensitivity: level,
      });
      return res.data;
    },
    onSuccess: () => {
      setSensitivityOpen(false);
    },
  });

  // -------------------------------------------------------------------
  // Derived data
  // -------------------------------------------------------------------

  const events = eventsResponse?.data ?? [];
  const meta = eventsResponse?.meta ?? { page: 1, page_size: pageSize, total: 0, total_pages: 0 };
  const stats = statsResponse?.data;
  const baselines = baselinesResponse?.data ?? [];

  const sortedEvents = useMemo(() => {
    return [...events].sort((a, b) => {
      let aVal: string | number = '';
      let bVal: string | number = '';

      switch (sortField) {
        case 'created_at':
          aVal = a.created_at;
          bVal = b.created_at;
          break;
        case 'anomaly_type':
          aVal = a.anomaly_type;
          bVal = b.anomaly_type;
          break;
        case 'severity':
          aVal = a.severity === 'critical' ? 3 : a.severity === 'warning' ? 2 : 1;
          bVal = b.severity === 'critical' ? 3 : b.severity === 'warning' ? 2 : 1;
          break;
        case 'confidence':
          aVal = a.confidence;
          bVal = b.confidence;
          break;
        case 'camera_name':
          aVal = a.camera_name;
          bVal = b.camera_name;
          break;
      }

      let cmp = 0;
      if (typeof aVal === 'number' && typeof bVal === 'number') {
        cmp = aVal - bVal;
      } else {
        cmp = String(aVal).localeCompare(String(bVal));
      }

      return sortDir === 'asc' ? cmp : -cmp;
    });
  }, [events, sortField, sortDir]);

  const severityCounts = useMemo(() => {
    const map: Record<string, number> = { critical: 0, warning: 0, info: 0 };
    if (stats?.by_severity) {
      for (const item of stats.by_severity) {
        map[item.label] = item.count;
      }
    }
    return map;
  }, [stats]);

  const healthyBaselines = useMemo(
    () => baselines.filter((b) => !b.is_stale).length,
    [baselines]
  );
  const staleBaselines = useMemo(
    () => baselines.filter((b) => b.is_stale).length,
    [baselines]
  );

  // -------------------------------------------------------------------
  // Handlers
  // -------------------------------------------------------------------

  const toggleSort = useCallback(
    (field: SortField) => {
      if (sortField === field) {
        setSortDir((prev) => (prev === 'asc' ? 'desc' : 'asc'));
      } else {
        setSortField(field);
        setSortDir('asc');
      }
    },
    [sortField]
  );

  const handleRefresh = useCallback(() => {
    refetchEvents();
    queryClient.invalidateQueries({ queryKey: ['anomaly-stats'] });
    refetchBaselines();
  }, [refetchEvents, queryClient, refetchBaselines]);

  // -------------------------------------------------------------------
  // Sort icon helper
  // -------------------------------------------------------------------

  const SortIcon = ({ field }: { field: SortField }) => {
    if (sortField !== field) return <ArrowUpDown className="ml-1 h-3.5 w-3.5" />;
    return sortDir === 'asc' ? (
      <ArrowUp className="ml-1 h-3.5 w-3.5" />
    ) : (
      <ArrowDown className="ml-1 h-3.5 w-3.5" />
    );
  };

  // -------------------------------------------------------------------
  // Loading state
  // -------------------------------------------------------------------

  if (eventsLoading && statsLoading && !eventsResponse) {
    return (
      <div className="flex h-96 items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
          <p className="text-sm text-slate-500">Loading anomaly detection data...</p>
        </div>
      </div>
    );
  }

  // -------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">
            Anomaly Detection
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Unsupervised anomaly detection across cameras and zones
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={handleRefresh}>
            <RefreshCw className="mr-1.5 h-4 w-4" />
            Refresh
          </Button>
          <Dialog open={sensitivityOpen} onOpenChange={setSensitivityOpen}>
            <DialogTrigger asChild>
              <Button variant="outline" size="sm">
                <Settings2 className="mr-1.5 h-4 w-4" />
                Sensitivity
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Detection Sensitivity</DialogTitle>
                <DialogDescription>
                  Adjust how sensitive the anomaly detection engine is. Higher
                  sensitivity detects more anomalies but may produce more
                  false positives.
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-4 py-4">
                <div className="space-y-2">
                  <Label>Sensitivity Level</Label>
                  <Select value={sensitivityLevel} onValueChange={setSensitivityLevel}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="low">
                        Low - Only significant deviations (3+ sigma)
                      </SelectItem>
                      <SelectItem value="medium">
                        Medium - Moderate deviations (2+ sigma)
                      </SelectItem>
                      <SelectItem value="high">
                        High - Minor deviations (1.5+ sigma)
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="rounded-md bg-muted p-3 text-sm text-muted-foreground">
                  <p className="font-medium">Preset thresholds:</p>
                  <ul className="mt-1 space-y-0.5">
                    {sensitivityLevel === 'low' && (
                      <>
                        <li>Warning: 3.0 sigma | Critical: 4.0 sigma</li>
                        <li>Min confidence: 0.7</li>
                      </>
                    )}
                    {sensitivityLevel === 'medium' && (
                      <>
                        <li>Warning: 2.0 sigma | Critical: 3.0 sigma</li>
                        <li>Min confidence: 0.5</li>
                      </>
                    )}
                    {sensitivityLevel === 'high' && (
                      <>
                        <li>Warning: 1.5 sigma | Critical: 2.5 sigma</li>
                        <li>Min confidence: 0.3</li>
                      </>
                    )}
                  </ul>
                </div>
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => setSensitivityOpen(false)}>
                  Cancel
                </Button>
                <Button
                  onClick={() => sensitivityMutation.mutate(sensitivityLevel)}
                  disabled={sensitivityMutation.isPending}
                >
                  {sensitivityMutation.isPending && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
                  Apply
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      {/* Summary Cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <Card className="transition-shadow hover:shadow-md">
          <CardContent className="p-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-slate-500 dark:text-slate-400">
                  Total Anomalies
                </p>
                <p className="mt-1 text-3xl font-bold text-slate-900 dark:text-white tabular-nums">
                  {(stats?.total ?? 0).toLocaleString()}
                </p>
              </div>
              <div className="rounded-xl p-3 bg-purple-50 text-purple-600 dark:bg-purple-900/30 dark:text-purple-400">
                <Activity className="h-6 w-6" />
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="transition-shadow hover:shadow-md">
          <CardContent className="p-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-slate-500 dark:text-slate-400">
                  Critical
                </p>
                <p className="mt-1 text-3xl font-bold text-red-600 dark:text-red-400 tabular-nums">
                  {(severityCounts.critical).toLocaleString()}
                </p>
              </div>
              <div className="rounded-xl p-3 bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-400">
                <XCircle className="h-6 w-6" />
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="transition-shadow hover:shadow-md">
          <CardContent className="p-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-slate-500 dark:text-slate-400">
                  Warnings
                </p>
                <p className="mt-1 text-3xl font-bold text-amber-600 dark:text-amber-400 tabular-nums">
                  {(severityCounts.warning).toLocaleString()}
                </p>
              </div>
              <div className="rounded-xl p-3 bg-amber-50 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400">
                <AlertTriangle className="h-6 w-6" />
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="transition-shadow hover:shadow-md">
          <CardContent className="p-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-slate-500 dark:text-slate-400">
                  Unacknowledged
                </p>
                <p className="mt-1 text-3xl font-bold text-slate-900 dark:text-white tabular-nums">
                  {(stats?.unacknowledged_count ?? 0).toLocaleString()}
                </p>
              </div>
              <div className="rounded-xl p-3 bg-orange-50 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400">
                <Eye className="h-6 w-6" />
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="transition-shadow hover:shadow-md">
          <CardContent className="p-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-slate-500 dark:text-slate-400">
                  Avg Confidence
                </p>
                <p className="mt-1 text-3xl font-bold text-slate-900 dark:text-white tabular-nums">
                  {((stats?.avg_confidence ?? 0) * 100).toFixed(1)}%
                </p>
              </div>
              <div className="rounded-xl p-3 bg-blue-50 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400">
                <TrendingUp className="h-6 w-6" />
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Timeline Chart */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Anomaly Timeline</CardTitle>
          <CardDescription>
            Anomaly events over time by severity
          </CardDescription>
        </CardHeader>
        <CardContent>
          {(stats?.timeline ?? []).length === 0 ? (
            <div className="flex h-64 flex-col items-center justify-center text-slate-400">
              <BarChart3 className="mb-2 h-8 w-8" />
              <p className="text-sm">No timeline data available for the selected period</p>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={300}>
              <AreaChart data={stats?.timeline ?? []} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                <XAxis
                  dataKey="timestamp"
                  tickFormatter={(val: string) => {
                    try {
                      return format(parseISO(val), 'MMM d HH:mm');
                    } catch {
                      return val;
                    }
                  }}
                  tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }}
                />
                <YAxis tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }} />
                <Tooltip content={<ChartTooltip />} />
                <Legend wrapperStyle={{ fontSize: 12, paddingTop: '8px' }} />
                <Area
                  type="monotone"
                  dataKey="critical"
                  name="Critical"
                  stackId="1"
                  stroke="hsl(0, 84%, 60%)"
                  fill="hsl(0, 84%, 60%)"
                  fillOpacity={0.6}
                />
                <Area
                  type="monotone"
                  dataKey="warning"
                  name="Warning"
                  stackId="1"
                  stroke="hsl(38, 92%, 50%)"
                  fill="hsl(38, 92%, 50%)"
                  fillOpacity={0.5}
                />
                <Area
                  type="monotone"
                  dataKey="info"
                  name="Info"
                  stackId="1"
                  stroke="hsl(221, 83%, 53%)"
                  fill="hsl(221, 83%, 53%)"
                  fillOpacity={0.4}
                />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      {/* Filters */}
      <Card>
        <CardContent className="p-4">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7">
            <div className="space-y-1.5">
              <Label className="text-xs">Date From</Label>
              <div className="relative">
                <CalendarDays className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  type="date"
                  value={dateFrom}
                  onChange={(e) => { setDateFrom(e.target.value); setCurrentPage(1); }}
                  className="pl-9"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs">Date To</Label>
              <div className="relative">
                <CalendarDays className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  type="date"
                  value={dateTo}
                  onChange={(e) => { setDateTo(e.target.value); setCurrentPage(1); }}
                  className="pl-9"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs">Camera</Label>
              <Select value={selectedCamera} onValueChange={(v) => { setSelectedCamera(v); setCurrentPage(1); }}>
                <SelectTrigger>
                  <SelectValue placeholder="All Cameras" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Cameras</SelectItem>
                  {(cameras ?? []).map((cam) => (
                    <SelectItem key={cam.id} value={cam.id}>{cam.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs">Zone</Label>
              <Select value={selectedZone} onValueChange={(v) => { setSelectedZone(v); setCurrentPage(1); }}>
                <SelectTrigger>
                  <SelectValue placeholder="All Zones" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Zones</SelectItem>
                  {(zones ?? []).map((zone) => (
                    <SelectItem key={zone.id} value={zone.id}>{zone.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs">Type</Label>
              <Select value={selectedType} onValueChange={(v) => { setSelectedType(v); setCurrentPage(1); }}>
                <SelectTrigger>
                  <SelectValue placeholder="All Types" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Types</SelectItem>
                  <SelectItem value="count">Count</SelectItem>
                  <SelectItem value="temporal">Temporal</SelectItem>
                  <SelectItem value="spatial">Spatial</SelectItem>
                  <SelectItem value="behavioral">Behavioral</SelectItem>
                  <SelectItem value="frequency">Frequency</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs">Severity</Label>
              <Select value={selectedSeverity} onValueChange={(v) => { setSelectedSeverity(v); setCurrentPage(1); }}>
                <SelectTrigger>
                  <SelectValue placeholder="All Severities" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Severities</SelectItem>
                  <SelectItem value="critical">Critical</SelectItem>
                  <SelectItem value="warning">Warning</SelectItem>
                  <SelectItem value="info">Info</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs">Status</Label>
              <Select value={selectedAckStatus} onValueChange={(v) => { setSelectedAckStatus(v); setCurrentPage(1); }}>
                <SelectTrigger>
                  <SelectValue placeholder="All" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All</SelectItem>
                  <SelectItem value="false">Unacknowledged</SelectItem>
                  <SelectItem value="true">Acknowledged</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Error */}
      {eventsError && (
        <Card className="border-red-200 bg-red-50 dark:border-red-800 dark:bg-red-950/30">
          <CardContent className="flex items-center gap-3 p-4">
            <ShieldAlert className="h-5 w-5 text-red-500" />
            <div>
              <p className="text-sm font-medium text-red-700 dark:text-red-400">
                Failed to load anomaly data
              </p>
              <p className="text-xs text-red-600 dark:text-red-500">
                An unexpected error occurred. Please try again.
              </p>
            </div>
            <Button variant="outline" size="sm" className="ml-auto" onClick={() => refetchEvents()}>
              Retry
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Anomaly Event Table */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-3">
          <div>
            <CardTitle className="text-base">Anomaly Events</CardTitle>
            <CardDescription>
              {meta.total} event{meta.total !== 1 ? 's' : ''} found
            </CardDescription>
          </div>
          <Badge variant="secondary" className="tabular-nums">
            Page {meta.page} of {meta.total_pages || 1}
          </Badge>
        </CardHeader>
        <CardContent>
          {sortedEvents.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12">
              <ShieldAlert className="mb-3 h-10 w-10 text-slate-300 dark:text-slate-600" />
              <p className="text-sm font-medium text-slate-500 dark:text-slate-400">
                No anomaly events found
              </p>
              <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">
                Adjust filters or wait for the detection engine to process new data
              </p>
            </div>
          ) : (
            <>
              <div className="rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>
                        <button className="flex items-center text-xs font-medium" onClick={() => toggleSort('created_at')}>
                          Timestamp <SortIcon field="created_at" />
                        </button>
                      </TableHead>
                      <TableHead>
                        <button className="flex items-center text-xs font-medium" onClick={() => toggleSort('anomaly_type')}>
                          Type <SortIcon field="anomaly_type" />
                        </button>
                      </TableHead>
                      <TableHead>
                        <button className="flex items-center text-xs font-medium" onClick={() => toggleSort('severity')}>
                          Severity <SortIcon field="severity" />
                        </button>
                      </TableHead>
                      <TableHead>
                        <button className="flex items-center text-xs font-medium" onClick={() => toggleSort('camera_name')}>
                          Camera <SortIcon field="camera_name" />
                        </button>
                      </TableHead>
                      <TableHead>Zone</TableHead>
                      <TableHead>Description</TableHead>
                      <TableHead>
                        <button className="flex items-center text-xs font-medium" onClick={() => toggleSort('confidence')}>
                          Confidence <SortIcon field="confidence" />
                        </button>
                      </TableHead>
                      <TableHead>Deviation</TableHead>
                      <TableHead className="text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {sortedEvents.map((event) => {
                      const sevConfig = SEVERITY_CONFIG[event.severity] || SEVERITY_CONFIG.info;
                      const SevIcon = sevConfig.icon;

                      return (
                        <TableRow key={event.id} className={event.is_acknowledged ? 'opacity-60' : ''}>
                          <TableCell className="whitespace-nowrap text-xs">
                            <div>{formatDate(event.created_at, 'MMM d, yyyy')}</div>
                            <div className="text-muted-foreground">{formatDate(event.created_at, 'HH:mm:ss')}</div>
                          </TableCell>
                          <TableCell>
                            <Badge variant="outline" className="text-xs capitalize">
                              {TYPE_LABELS[event.anomaly_type] || event.anomaly_type}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            <Badge className={cn('text-xs capitalize gap-1', sevConfig.bg, sevConfig.color)}>
                              <SevIcon className="h-3 w-3" />
                              {event.severity}
                            </Badge>
                          </TableCell>
                          <TableCell className="text-sm">
                            <div className="flex items-center gap-1.5">
                              <Camera className="h-3.5 w-3.5 text-muted-foreground" />
                              {event.camera_name || '--'}
                            </div>
                          </TableCell>
                          <TableCell className="text-sm">
                            {event.zone_name ? (
                              <div className="flex items-center gap-1.5">
                                <MapPin className="h-3.5 w-3.5 text-muted-foreground" />
                                {event.zone_name}
                              </div>
                            ) : (
                              <span className="text-muted-foreground">--</span>
                            )}
                          </TableCell>
                          <TableCell className="max-w-xs truncate text-xs text-muted-foreground" title={event.description}>
                            {event.description}
                          </TableCell>
                          <TableCell className="tabular-nums text-sm font-medium">
                            {(event.confidence * 100).toFixed(1)}%
                          </TableCell>
                          <TableCell className="tabular-nums text-sm">
                            {event.deviation_sigma !== null ? (
                              <span className={cn(
                                'font-medium',
                                Math.abs(event.deviation_sigma) >= 3 ? 'text-red-600 dark:text-red-400' :
                                Math.abs(event.deviation_sigma) >= 2 ? 'text-amber-600 dark:text-amber-400' :
                                'text-blue-600 dark:text-blue-400'
                              )}>
                                {event.deviation_sigma > 0 ? '+' : ''}{event.deviation_sigma.toFixed(1)} sigma
                              </span>
                            ) : (
                              <span className="text-muted-foreground">--</span>
                            )}
                          </TableCell>
                          <TableCell className="text-right">
                            {event.is_acknowledged ? (
                              <Badge variant="outline" className="text-xs text-green-600 dark:text-green-400">
                                <CheckCircle2 className="mr-1 h-3 w-3" />
                                Ack
                              </Badge>
                            ) : (
                              <Button
                                variant="outline"
                                size="sm"
                                className="h-7 text-xs"
                                onClick={() => acknowledgeMutation.mutate(event.id)}
                                disabled={acknowledgeMutation.isPending}
                              >
                                {acknowledgeMutation.isPending ? (
                                  <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                                ) : (
                                  <CheckCircle2 className="mr-1 h-3 w-3" />
                                )}
                                Acknowledge
                              </Button>
                            )}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>

              {/* Pagination */}
              {meta.total_pages > 1 && (
                <div className="flex items-center justify-between mt-4">
                  <p className="text-sm text-muted-foreground">
                    Showing {((meta.page - 1) * meta.page_size) + 1} to{' '}
                    {Math.min(meta.page * meta.page_size, meta.total)} of{' '}
                    {meta.total} results
                  </p>
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                      disabled={currentPage <= 1}
                    >
                      Previous
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setCurrentPage((p) => Math.min(meta.total_pages, p + 1))}
                      disabled={currentPage >= meta.total_pages}
                    >
                      Next
                    </Button>
                  </div>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {/* Baseline Health Panel */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-3">
          <div>
            <CardTitle className="text-base flex items-center gap-2">
              <Database className="h-4 w-4" />
              Baseline Health
            </CardTitle>
            <CardDescription>
              Statistical baseline freshness per camera ({healthyBaselines} healthy, {staleBaselines} stale)
            </CardDescription>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => rebuildMutation.mutate()}
            disabled={rebuildMutation.isPending}
          >
            {rebuildMutation.isPending ? (
              <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
            ) : (
              <Zap className="mr-1.5 h-4 w-4" />
            )}
            Rebuild All
          </Button>
        </CardHeader>
        <CardContent>
          {baselinesLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : baselines.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-8">
              <Database className="mb-3 h-8 w-8 text-slate-300 dark:text-slate-600" />
              <p className="text-sm font-medium text-slate-500 dark:text-slate-400">
                No baselines configured
              </p>
              <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">
                Click "Rebuild All" to compute baselines from historical data
              </p>
            </div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {baselines.map((bl, idx) => (
                <div
                  key={`${bl.camera_id}-${bl.zone_id || 'global'}-${idx}`}
                  className={cn(
                    'rounded-lg border p-3 transition-colors',
                    bl.is_stale
                      ? 'border-amber-200 bg-amber-50/50 dark:border-amber-800 dark:bg-amber-950/20'
                      : 'border-green-200 bg-green-50/50 dark:border-green-800 dark:bg-green-950/20'
                  )}
                >
                  <div className="flex items-start justify-between">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium truncate">{bl.camera_name}</p>
                      {bl.zone_name && (
                        <p className="text-xs text-muted-foreground flex items-center gap-1 mt-0.5">
                          <MapPin className="h-3 w-3" />
                          {bl.zone_name}
                        </p>
                      )}
                    </div>
                    {bl.is_stale ? (
                      <Badge variant="outline" className="text-xs text-amber-600 dark:text-amber-400 shrink-0">
                        Stale
                      </Badge>
                    ) : (
                      <Badge variant="outline" className="text-xs text-green-600 dark:text-green-400 shrink-0">
                        Healthy
                      </Badge>
                    )}
                  </div>
                  <div className="mt-2 flex items-center gap-3 text-xs text-muted-foreground">
                    <span>{bl.sample_count.toLocaleString()} samples</span>
                    {bl.updated_at && (
                      <span className="flex items-center gap-1">
                        <Clock className="h-3 w-3" />
                        {formatRelativeTime(bl.updated_at)}
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
