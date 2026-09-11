'use client';

import { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
  Cell,
} from 'recharts';
import { format, parseISO, addDays } from 'date-fns';
import {
  Loader2,
  RefreshCw,
  TrendingUp,
  TrendingDown,
  Minus,
  ShieldAlert,
  Users,
  Activity,
  BarChart3,
  CalendarDays,
  Target,
  AlertTriangle,
  Clock,
  ChevronRight,
  Gauge,
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
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/components/ui/tabs';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import api from '@/lib/api-client';
import { cn } from '@/lib/utils';

// -------------------------------------------------------------------
// Types
// -------------------------------------------------------------------

interface Camera {
  id: string;
  name: string;
}

interface PredictionPoint {
  timestamp: string;
  value: number;
  confidence_lower: number;
  confidence_upper: number;
  confidence_lower_95?: number;
  confidence_upper_95?: number;
}

interface FootfallPrediction {
  camera_id: string;
  camera_name: string;
  predictions: PredictionPoint[];
  accuracy_pct: number | null;
  model_version: string;
  message?: string;
}

interface AlertTypePrediction {
  alert_type: string;
  predicted_count: number;
  probability: number;
  hourly_breakdown: PredictionPoint[];
}

interface AlertPrediction {
  org_id: string;
  predictions: AlertTypePrediction[];
  total_predicted: number;
  hours_ahead: number;
}

interface ContributingFactor {
  factor: string;
  weight: number;
  value: number;
  description: string;
}

interface RiskForecastItem {
  camera_id: string | null;
  camera_name: string | null;
  zone_id: string | null;
  zone_name: string | null;
  risk_level: string;
  risk_score: number;
  contributing_factors: ContributingFactor[];
  valid_until: string;
}

interface RiskForecast {
  org_id: string;
  forecasts: RiskForecastItem[];
  overall_risk_level: string;
  overall_risk_score: number;
}

interface HourlyAllocation {
  hour: number;
  recommended_staff: number;
  risk_level: string;
  predicted_alerts: number;
  predicted_footfall: number;
  notes: string | null;
}

interface StaffScheduleData {
  org_id: string;
  date: string;
  hourly_allocations: HourlyAllocation[];
  total_staff_hours: number;
  peak_hour: number | null;
  peak_staff: number | null;
}

interface TrendData {
  metric: string;
  direction: string;
  slope: number;
  p_value: number;
  confidence: string;
  description: string;
  period_days: number;
  data_points: number;
  change_pct: number | null;
}

interface AccuracyMetric {
  metric: string;
  mape: number;
  accuracy_pct: number;
  predictions_count: number;
  period_days: number;
}

interface AccuracyData {
  org_id: string;
  metrics: AccuracyMetric[];
  overall_accuracy_pct: number | null;
}

// -------------------------------------------------------------------
// Colour helpers
// -------------------------------------------------------------------

const RISK_COLORS: Record<string, string> = {
  low: 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-400',
  medium: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-400',
  high: 'bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-400',
  critical: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-400',
};

const RISK_BORDER: Record<string, string> = {
  low: 'border-l-green-500',
  medium: 'border-l-yellow-500',
  high: 'border-l-orange-500',
  critical: 'border-l-red-500',
};

const RISK_BG: Record<string, string> = {
  low: 'bg-green-500',
  medium: 'bg-yellow-500',
  high: 'bg-orange-500',
  critical: 'bg-red-500',
};

const BAR_COLORS = [
  'hsl(221, 83%, 53%)',
  'hsl(142, 76%, 36%)',
  'hsl(0, 84%, 60%)',
  'hsl(38, 92%, 50%)',
  'hsl(262, 83%, 58%)',
  'hsl(178, 70%, 40%)',
  'hsl(330, 81%, 60%)',
  'hsl(200, 70%, 50%)',
];

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
          <span className="font-semibold">{typeof entry.value === 'number' ? entry.value.toLocaleString(undefined, { maximumFractionDigits: 1 }) : entry.value}</span>
        </div>
      ))}
    </div>
  );
}

// -------------------------------------------------------------------
// Accuracy Gauge Component
// -------------------------------------------------------------------

function AccuracyGauge({ value, label }: { value: number; label: string }) {
  const clampedValue = Math.max(0, Math.min(100, value));
  const colour =
    clampedValue >= 85
      ? 'text-green-600 dark:text-green-400'
      : clampedValue >= 70
        ? 'text-yellow-600 dark:text-yellow-400'
        : 'text-red-600 dark:text-red-400';

  const bgColour =
    clampedValue >= 85
      ? 'bg-green-500'
      : clampedValue >= 70
        ? 'bg-yellow-500'
        : 'bg-red-500';

  return (
    <div className="flex flex-col items-center gap-2">
      <div className="relative h-24 w-24">
        <svg className="h-24 w-24 -rotate-90" viewBox="0 0 96 96">
          <circle
            cx="48"
            cy="48"
            r="40"
            fill="none"
            stroke="currentColor"
            strokeWidth="8"
            className="text-muted/30"
          />
          <circle
            cx="48"
            cy="48"
            r="40"
            fill="none"
            stroke="currentColor"
            strokeWidth="8"
            strokeDasharray={`${(clampedValue / 100) * 251.2} 251.2`}
            strokeLinecap="round"
            className={colour}
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className={cn('text-lg font-bold tabular-nums', colour)}>
            {clampedValue.toFixed(1)}%
          </span>
        </div>
      </div>
      <span className="text-xs font-medium text-muted-foreground capitalize">{label}</span>
    </div>
  );
}

// -------------------------------------------------------------------
// Page Component
// -------------------------------------------------------------------

export default function PredictionsPage() {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState('footfall');
  const [selectedCamera, setSelectedCamera] = useState<string>('');
  const [selectedMetric, setSelectedMetric] = useState<string>('footfall');
  const [scheduleDate, setScheduleDate] = useState<string>(
    format(addDays(new Date(), 1), 'yyyy-MM-dd')
  );

  // -------------------------------------------------------------------
  // Queries
  // -------------------------------------------------------------------

  const { data: cameras } = useQuery<Camera[]>({
    queryKey: ['cameras-list'],
    queryFn: async () => {
      const res = await api.get<Camera[]>('/cameras', { params: { page_size: 200 } });
      return Array.isArray(res) ? res : [];
    },
  });

  // Set default camera when cameras load
  const defaultCameraId = cameras?.[0]?.id ?? '';
  const activeCameraId = selectedCamera || defaultCameraId;

  const {
    data: footfallPrediction,
    isLoading: footfallLoading,
    refetch: refetchFootfall,
  } = useQuery<FootfallPrediction>({
    queryKey: ['prediction-footfall', activeCameraId],
    queryFn: () => api.get<FootfallPrediction>(`/predictions/footfall/${activeCameraId}`),
    enabled: !!activeCameraId,
  });

  const {
    data: alertPrediction,
    isLoading: alertsLoading,
    refetch: refetchAlerts,
  } = useQuery<AlertPrediction>({
    queryKey: ['prediction-alerts'],
    queryFn: () => api.get<AlertPrediction>('/predictions/alerts'),
  });

  const {
    data: riskForecast,
    isLoading: riskLoading,
    refetch: refetchRisk,
  } = useQuery<RiskForecast>({
    queryKey: ['prediction-risk'],
    queryFn: () => api.get<RiskForecast>('/predictions/risk-forecast'),
  });

  const {
    data: staffSchedule,
    isLoading: scheduleLoading,
    refetch: refetchSchedule,
  } = useQuery<StaffScheduleData>({
    queryKey: ['prediction-schedule', scheduleDate],
    queryFn: () => api.get<StaffScheduleData>(`/predictions/staff-schedule/${scheduleDate}`),
    enabled: !!scheduleDate,
    retry: false,
  });

  const {
    data: trendData,
    isLoading: trendLoading,
    refetch: refetchTrend,
  } = useQuery<TrendData>({
    queryKey: ['prediction-trend', selectedMetric],
    queryFn: () =>
      api.get<TrendData>('/predictions/trends', {
        params: { metric: selectedMetric, period_days: 30 },
      }),
  });

  const {
    data: accuracyData,
    isLoading: accuracyLoading,
    refetch: refetchAccuracy,
  } = useQuery<AccuracyData>({
    queryKey: ['prediction-accuracy'],
    queryFn: () => api.get<AccuracyData>('/predictions/accuracy'),
  });

  // Generate schedule mutation
  const generateSchedule = useMutation({
    mutationFn: async (targetDate: string) => {
      return api.post<StaffScheduleData>('/predictions/staff-schedule', { date: targetDate });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['prediction-schedule'] });
    },
  });

  // Refresh all data
  const refreshAll = () => {
    refetchFootfall();
    refetchAlerts();
    refetchRisk();
    refetchSchedule();
    refetchTrend();
    refetchAccuracy();
  };

  // -------------------------------------------------------------------
  // Derived data for charts
  // -------------------------------------------------------------------

  const footfallChartData = useMemo(() => {
    if (!footfallPrediction?.predictions) return [];
    return footfallPrediction.predictions.map((p) => ({
      timestamp: p.timestamp,
      predicted: p.value,
      lower80: p.confidence_lower,
      upper80: p.confidence_upper,
      lower95: p.confidence_lower_95 ?? p.confidence_lower,
      upper95: p.confidence_upper_95 ?? p.confidence_upper,
    }));
  }, [footfallPrediction]);

  const alertBarData = useMemo(() => {
    if (!alertPrediction?.predictions) return [];
    // Aggregate hourly data into 4-hour blocks for readability
    const blocks: Record<string, Record<string, number>> = {};

    for (const typePred of alertPrediction.predictions) {
      for (const hp of typePred.hourly_breakdown) {
        let blockLabel: string;
        try {
          const dt = parseISO(hp.timestamp);
          const blockStart = Math.floor(dt.getHours() / 4) * 4;
          blockLabel = `${format(dt, 'MMM d')} ${String(blockStart).padStart(2, '0')}:00-${String(blockStart + 3).padStart(2, '0')}:59`;
        } catch {
          blockLabel = hp.timestamp;
        }

        if (!blocks[blockLabel]) blocks[blockLabel] = {};
        blocks[blockLabel][typePred.alert_type] =
          (blocks[blockLabel][typePred.alert_type] ?? 0) + hp.value;
      }
    }

    return Object.entries(blocks).map(([label, types]) => ({
      label,
      ...types,
    }));
  }, [alertPrediction]);

  const alertTypes = useMemo(() => {
    return alertPrediction?.predictions?.map((p) => p.alert_type) ?? [];
  }, [alertPrediction]);

  // -------------------------------------------------------------------
  // Loading state
  // -------------------------------------------------------------------

  const isLoading = footfallLoading && alertsLoading && riskLoading;

  if (isLoading) {
    return (
      <div className="flex h-96 items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
          <p className="text-sm text-slate-500">Loading predictive analytics...</p>
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
            Predictive Analytics
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            AI-powered forecasting, trend detection, and intelligent scheduling
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={refreshAll}>
          <RefreshCw className="mr-1.5 h-4 w-4" />
          Refresh All
        </Button>
      </div>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="grid w-full grid-cols-3 lg:grid-cols-6">
          <TabsTrigger value="footfall" className="text-xs">
            <Activity className="mr-1 h-3.5 w-3.5" />
            Footfall
          </TabsTrigger>
          <TabsTrigger value="alerts" className="text-xs">
            <AlertTriangle className="mr-1 h-3.5 w-3.5" />
            Alerts
          </TabsTrigger>
          <TabsTrigger value="risk" className="text-xs">
            <ShieldAlert className="mr-1 h-3.5 w-3.5" />
            Risk Map
          </TabsTrigger>
          <TabsTrigger value="schedule" className="text-xs">
            <Users className="mr-1 h-3.5 w-3.5" />
            Staff
          </TabsTrigger>
          <TabsTrigger value="trends" className="text-xs">
            <TrendingUp className="mr-1 h-3.5 w-3.5" />
            Trends
          </TabsTrigger>
          <TabsTrigger value="accuracy" className="text-xs">
            <Target className="mr-1 h-3.5 w-3.5" />
            Accuracy
          </TabsTrigger>
        </TabsList>

        {/* ============================================================
            TAB: Footfall Forecast
            ============================================================ */}
        <TabsContent value="footfall" className="space-y-4 mt-4">
          {/* Camera selector */}
          <Card>
            <CardContent className="p-4">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-end">
                <div className="space-y-1.5 flex-1 max-w-xs">
                  <Label className="text-xs">Camera</Label>
                  <Select
                    value={activeCameraId}
                    onValueChange={setSelectedCamera}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Select camera" />
                    </SelectTrigger>
                    <SelectContent>
                      {(cameras ?? []).map((cam) => (
                        <SelectItem key={cam.id} value={cam.id}>
                          {cam.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                {footfallPrediction?.accuracy_pct != null && (
                  <Badge variant="secondary" className="h-fit">
                    Model accuracy: {footfallPrediction.accuracy_pct.toFixed(1)}%
                  </Badge>
                )}
              </div>
            </CardContent>
          </Card>

          {/* Footfall Chart */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Footfall Forecast (Next 24h)</CardTitle>
              <CardDescription>
                Predicted entries with 80% (dark) and 95% (light) confidence intervals
              </CardDescription>
            </CardHeader>
            <CardContent>
              {footfallLoading ? (
                <div className="flex h-64 items-center justify-center">
                  <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
                </div>
              ) : footfallChartData.length === 0 ? (
                <div className="flex h-64 flex-col items-center justify-center text-slate-400">
                  <BarChart3 className="mb-2 h-8 w-8" />
                  <p className="text-sm">
                    {footfallPrediction?.message ?? 'No prediction data available. Select a camera with historical data.'}
                  </p>
                </div>
              ) : (
                <ResponsiveContainer width="100%" height={380}>
                  <AreaChart
                    data={footfallChartData}
                    margin={{ top: 5, right: 10, left: 0, bottom: 0 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                    <XAxis
                      dataKey="timestamp"
                      tickFormatter={(val: string) => {
                        try {
                          return format(parseISO(val), 'HH:mm');
                        } catch {
                          return val;
                        }
                      }}
                      tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }}
                    />
                    <YAxis tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }} />
                    <Tooltip content={<ChartTooltip />} />
                    <Legend wrapperStyle={{ fontSize: 12, paddingTop: '8px' }} />

                    {/* 95% confidence interval (light shade) */}
                    <Area
                      type="monotone"
                      dataKey="upper95"
                      stackId="ci95"
                      stroke="none"
                      fill="hsl(221, 83%, 53%)"
                      fillOpacity={0.08}
                      name="95% CI Upper"
                    />
                    <Area
                      type="monotone"
                      dataKey="lower95"
                      stackId="ci95"
                      stroke="none"
                      fill="hsl(221, 83%, 53%)"
                      fillOpacity={0.08}
                      name="95% CI Lower"
                    />

                    {/* 80% confidence interval (darker shade) */}
                    <Area
                      type="monotone"
                      dataKey="upper80"
                      stackId="ci80"
                      stroke="none"
                      fill="hsl(221, 83%, 53%)"
                      fillOpacity={0.15}
                      name="80% CI Upper"
                    />
                    <Area
                      type="monotone"
                      dataKey="lower80"
                      stackId="ci80"
                      stroke="none"
                      fill="hsl(221, 83%, 53%)"
                      fillOpacity={0.15}
                      name="80% CI Lower"
                    />

                    {/* Predicted line (dashed) */}
                    <Line
                      type="monotone"
                      dataKey="predicted"
                      name="Predicted Footfall"
                      stroke="hsl(221, 83%, 53%)"
                      strokeWidth={2.5}
                      strokeDasharray="6 3"
                      dot={false}
                      activeDot={{ r: 4, fill: 'hsl(221, 83%, 53%)' }}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ============================================================
            TAB: Alert Forecast
            ============================================================ */}
        <TabsContent value="alerts" className="space-y-4 mt-4">
          {/* Summary cards */}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Card>
              <CardContent className="p-6">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium text-slate-500">Total Predicted</p>
                    <p className="mt-1 text-3xl font-bold tabular-nums">
                      {alertPrediction?.total_predicted?.toFixed(0) ?? '0'}
                    </p>
                    <p className="text-xs text-slate-400">next 24 hours</p>
                  </div>
                  <div className="rounded-xl bg-orange-50 p-3 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400">
                    <AlertTriangle className="h-6 w-6" />
                  </div>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="p-6">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium text-slate-500">Alert Types</p>
                    <p className="mt-1 text-3xl font-bold tabular-nums">
                      {alertPrediction?.predictions?.length ?? 0}
                    </p>
                    <p className="text-xs text-slate-400">types forecasted</p>
                  </div>
                  <div className="rounded-xl bg-blue-50 p-3 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400">
                    <BarChart3 className="h-6 w-6" />
                  </div>
                </div>
              </CardContent>
            </Card>
            {(alertPrediction?.predictions ?? []).slice(0, 2).map((ap, i) => (
              <Card key={ap.alert_type}>
                <CardContent className="p-6">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm font-medium text-slate-500 capitalize">
                        {ap.alert_type.replace(/_/g, ' ')}
                      </p>
                      <p className="mt-1 text-3xl font-bold tabular-nums">
                        {ap.predicted_count.toFixed(0)}
                      </p>
                      <p className="text-xs text-slate-400">
                        {(ap.probability * 100).toFixed(0)}% probability
                      </p>
                    </div>
                    <div className="rounded-xl bg-purple-50 p-3 text-purple-600 dark:bg-purple-900/30 dark:text-purple-400">
                      <ShieldAlert className="h-6 w-6" />
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>

          {/* Alert Bar Chart */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Predicted Alerts by Type (Next 24h)</CardTitle>
              <CardDescription>
                Stacked 4-hour blocks showing predicted alert volumes per type
              </CardDescription>
            </CardHeader>
            <CardContent>
              {alertsLoading ? (
                <div className="flex h-64 items-center justify-center">
                  <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
                </div>
              ) : alertBarData.length === 0 ? (
                <div className="flex h-64 flex-col items-center justify-center text-slate-400">
                  <BarChart3 className="mb-2 h-8 w-8" />
                  <p className="text-sm">No alert prediction data available</p>
                </div>
              ) : (
                <ResponsiveContainer width="100%" height={380}>
                  <BarChart data={alertBarData} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                    <XAxis
                      dataKey="label"
                      tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 10 }}
                      angle={-25}
                      textAnchor="end"
                      height={60}
                    />
                    <YAxis tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }} />
                    <Tooltip content={<ChartTooltip />} />
                    <Legend wrapperStyle={{ fontSize: 12, paddingTop: '8px' }} />
                    {alertTypes.map((type, idx) => (
                      <Bar
                        key={type}
                        dataKey={type}
                        stackId="alerts"
                        fill={BAR_COLORS[idx % BAR_COLORS.length]}
                        name={type.replace(/_/g, ' ')}
                        radius={idx === alertTypes.length - 1 ? [2, 2, 0, 0] : [0, 0, 0, 0]}
                      />
                    ))}
                  </BarChart>
                </ResponsiveContainer>
              )}
            </CardContent>
          </Card>

          {/* Alert type table */}
          {alertPrediction?.predictions && alertPrediction.predictions.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Alert Type Breakdown</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="rounded-md border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="text-xs">Alert Type</TableHead>
                        <TableHead className="text-xs text-right">Predicted Count</TableHead>
                        <TableHead className="text-xs text-right">Probability</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {alertPrediction.predictions.map((ap) => (
                        <TableRow key={ap.alert_type}>
                          <TableCell className="capitalize font-medium">
                            {ap.alert_type.replace(/_/g, ' ')}
                          </TableCell>
                          <TableCell className="text-right tabular-nums font-semibold">
                            {ap.predicted_count.toFixed(1)}
                          </TableCell>
                          <TableCell className="text-right">
                            <Badge
                              variant={ap.probability > 0.7 ? 'destructive' : 'secondary'}
                              className="tabular-nums"
                            >
                              {(ap.probability * 100).toFixed(0)}%
                            </Badge>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* ============================================================
            TAB: Risk Map
            ============================================================ */}
        <TabsContent value="risk" className="space-y-4 mt-4">
          {/* Overall risk banner */}
          {riskForecast && (
            <Card className={cn('border-l-4', RISK_BORDER[riskForecast.overall_risk_level] ?? RISK_BORDER.low)}>
              <CardContent className="flex items-center gap-4 p-4">
                <div className={cn('rounded-full p-2', RISK_BG[riskForecast.overall_risk_level] ?? RISK_BG.low)}>
                  <ShieldAlert className="h-5 w-5 text-white" />
                </div>
                <div className="flex-1">
                  <p className="text-sm font-medium text-slate-900 dark:text-white">
                    Overall Risk Level:{' '}
                    <span className="capitalize">{riskForecast.overall_risk_level}</span>
                  </p>
                  <p className="text-xs text-slate-500">
                    Risk score: {(riskForecast.overall_risk_score * 100).toFixed(1)}% |{' '}
                    {riskForecast.forecasts.length} cameras assessed
                  </p>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Risk grid */}
          {riskLoading ? (
            <div className="flex h-64 items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {(riskForecast?.forecasts ?? []).map((item, idx) => (
                <Card
                  key={idx}
                  className={cn(
                    'border-l-4 transition-shadow hover:shadow-md',
                    RISK_BORDER[item.risk_level] ?? RISK_BORDER.low
                  )}
                >
                  <CardContent className="p-4 space-y-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <div className={cn('h-3 w-3 rounded-full', RISK_BG[item.risk_level])} />
                        <span className="text-sm font-semibold text-slate-900 dark:text-white">
                          {item.camera_name ?? item.zone_name ?? 'Unknown'}
                        </span>
                      </div>
                      <Badge className={cn('capitalize', RISK_COLORS[item.risk_level])}>
                        {item.risk_level}
                      </Badge>
                    </div>

                    {/* Risk score bar */}
                    <div className="space-y-1">
                      <div className="flex justify-between text-xs text-slate-500">
                        <span>Risk Score</span>
                        <span className="tabular-nums font-medium">
                          {(item.risk_score * 100).toFixed(1)}%
                        </span>
                      </div>
                      <div className="h-2 rounded-full bg-slate-100 dark:bg-slate-800">
                        <div
                          className={cn(
                            'h-2 rounded-full transition-all',
                            RISK_BG[item.risk_level]
                          )}
                          style={{ width: `${item.risk_score * 100}%` }}
                        />
                      </div>
                    </div>

                    {/* Contributing factors */}
                    {item.contributing_factors.length > 0 && (
                      <div className="space-y-1">
                        <p className="text-xs font-medium text-slate-500">Contributing Factors</p>
                        {item.contributing_factors.slice(0, 3).map((f, fi) => (
                          <div key={fi} className="flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-400">
                            <ChevronRight className="h-3 w-3 text-slate-400" />
                            <span>{f.description}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </CardContent>
                </Card>
              ))}

              {(riskForecast?.forecasts ?? []).length === 0 && (
                <div className="col-span-full flex flex-col items-center justify-center py-16 text-slate-400">
                  <ShieldAlert className="mb-2 h-10 w-10" />
                  <p className="text-sm">No risk forecast data available</p>
                </div>
              )}
            </div>
          )}
        </TabsContent>

        {/* ============================================================
            TAB: Staff Schedule
            ============================================================ */}
        <TabsContent value="schedule" className="space-y-4 mt-4">
          {/* Date selector and generate button */}
          <Card>
            <CardContent className="p-4">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-end">
                <div className="space-y-1.5 max-w-xs">
                  <Label className="text-xs">Schedule Date</Label>
                  <div className="relative">
                    <CalendarDays className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      type="date"
                      value={scheduleDate}
                      onChange={(e) => setScheduleDate(e.target.value)}
                      className="pl-9"
                    />
                  </div>
                </div>
                <Button
                  size="sm"
                  onClick={() => generateSchedule.mutate(scheduleDate)}
                  disabled={generateSchedule.isPending}
                >
                  {generateSchedule.isPending ? (
                    <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                  ) : (
                    <Users className="mr-1.5 h-4 w-4" />
                  )}
                  Generate Schedule
                </Button>
                {staffSchedule && (
                  <div className="flex gap-3 ml-auto">
                    <Badge variant="secondary">
                      Total: {staffSchedule.total_staff_hours} staff-hours
                    </Badge>
                    {staffSchedule.peak_hour != null && (
                      <Badge variant="outline">
                        Peak: {String(staffSchedule.peak_hour).padStart(2, '0')}:00 ({staffSchedule.peak_staff} staff)
                      </Badge>
                    )}
                  </div>
                )}
              </div>
            </CardContent>
          </Card>

          {/* Schedule timeline */}
          {scheduleLoading ? (
            <div className="flex h-64 items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
            </div>
          ) : staffSchedule?.hourly_allocations && staffSchedule.hourly_allocations.length > 0 ? (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Hourly Staff Allocation</CardTitle>
                <CardDescription>
                  Recommended staffing levels for {staffSchedule.date}
                </CardDescription>
              </CardHeader>
              <CardContent>
                {/* Visual timeline */}
                <div className="mb-6 flex gap-1">
                  {staffSchedule.hourly_allocations.map((alloc) => (
                    <div
                      key={alloc.hour}
                      className="flex-1 group relative"
                    >
                      <div
                        className={cn(
                          'rounded-sm transition-all cursor-default',
                          RISK_BG[alloc.risk_level] ?? RISK_BG.low
                        )}
                        style={{
                          height: `${Math.max(alloc.recommended_staff * 16, 12)}px`,
                        }}
                        title={`${String(alloc.hour).padStart(2, '0')}:00 - ${alloc.recommended_staff} staff (${alloc.risk_level})`}
                      />
                      <span className="mt-0.5 block text-center text-[9px] text-slate-400">
                        {alloc.hour % 3 === 0 ? `${String(alloc.hour).padStart(2, '0')}` : ''}
                      </span>
                    </div>
                  ))}
                </div>

                {/* Detail table */}
                <div className="rounded-md border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="text-xs">Hour</TableHead>
                        <TableHead className="text-xs text-center">Staff</TableHead>
                        <TableHead className="text-xs text-center">Risk</TableHead>
                        <TableHead className="text-xs text-right">Pred. Alerts</TableHead>
                        <TableHead className="text-xs text-right">Pred. Footfall</TableHead>
                        <TableHead className="text-xs">Notes</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {staffSchedule.hourly_allocations.map((alloc) => (
                        <TableRow key={alloc.hour}>
                          <TableCell className="font-mono text-xs">
                            <Clock className="mr-1 inline h-3 w-3 text-slate-400" />
                            {String(alloc.hour).padStart(2, '0')}:00
                          </TableCell>
                          <TableCell className="text-center">
                            <span className="inline-flex items-center gap-1 font-bold tabular-nums">
                              <Users className="h-3 w-3 text-slate-400" />
                              {alloc.recommended_staff}
                            </span>
                          </TableCell>
                          <TableCell className="text-center">
                            <Badge className={cn('capitalize text-xs', RISK_COLORS[alloc.risk_level])}>
                              {alloc.risk_level}
                            </Badge>
                          </TableCell>
                          <TableCell className="text-right tabular-nums text-sm">
                            {alloc.predicted_alerts.toFixed(1)}
                          </TableCell>
                          <TableCell className="text-right tabular-nums text-sm">
                            {alloc.predicted_footfall.toFixed(0)}
                          </TableCell>
                          <TableCell className="text-xs text-slate-500 max-w-[200px] truncate">
                            {alloc.notes ?? '-'}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </CardContent>
            </Card>
          ) : (
            <Card>
              <CardContent className="flex flex-col items-center justify-center py-16">
                <Users className="mb-3 h-10 w-10 text-slate-300 dark:text-slate-600" />
                <p className="text-sm font-medium text-slate-500">No schedule found for this date</p>
                <p className="mt-1 text-xs text-slate-400">
                  Click "Generate Schedule" to create an optimised staff allocation
                </p>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* ============================================================
            TAB: Trend Analysis
            ============================================================ */}
        <TabsContent value="trends" className="space-y-4 mt-4">
          {/* Metric selector */}
          <Card>
            <CardContent className="p-4">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-end">
                <div className="space-y-1.5 max-w-xs">
                  <Label className="text-xs">Metric</Label>
                  <Select value={selectedMetric} onValueChange={setSelectedMetric}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="footfall">Footfall</SelectItem>
                      <SelectItem value="alerts">Alerts</SelectItem>
                      <SelectItem value="occupancy">Occupancy</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Trend cards */}
          {trendLoading ? (
            <div className="flex h-64 items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
            </div>
          ) : trendData ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {/* Direction */}
              <Card className="transition-shadow hover:shadow-md">
                <CardContent className="p-6">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm font-medium text-slate-500">Direction</p>
                      <p className="mt-1 text-2xl font-bold capitalize text-slate-900 dark:text-white">
                        {trendData.direction}
                      </p>
                    </div>
                    <div
                      className={cn(
                        'rounded-xl p-3',
                        trendData.direction === 'increasing'
                          ? 'bg-green-50 text-green-600 dark:bg-green-900/30 dark:text-green-400'
                          : trendData.direction === 'decreasing'
                            ? 'bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-400'
                            : 'bg-slate-50 text-slate-600 dark:bg-slate-800 dark:text-slate-400'
                      )}
                    >
                      {trendData.direction === 'increasing' ? (
                        <TrendingUp className="h-6 w-6" />
                      ) : trendData.direction === 'decreasing' ? (
                        <TrendingDown className="h-6 w-6" />
                      ) : (
                        <Minus className="h-6 w-6" />
                      )}
                    </div>
                  </div>
                </CardContent>
              </Card>

              {/* Slope */}
              <Card className="transition-shadow hover:shadow-md">
                <CardContent className="p-6">
                  <p className="text-sm font-medium text-slate-500">Slope</p>
                  <p className="mt-1 text-2xl font-bold tabular-nums">
                    {trendData.slope > 0 ? '+' : ''}
                    {trendData.slope.toFixed(4)}
                  </p>
                  <p className="text-xs text-slate-400">units per day</p>
                </CardContent>
              </Card>

              {/* P-Value */}
              <Card className="transition-shadow hover:shadow-md">
                <CardContent className="p-6">
                  <p className="text-sm font-medium text-slate-500">Significance</p>
                  <p className="mt-1 text-2xl font-bold capitalize">{trendData.confidence}</p>
                  <p className="text-xs text-slate-400 tabular-nums">p = {trendData.p_value.toFixed(4)}</p>
                </CardContent>
              </Card>

              {/* Change */}
              <Card className="transition-shadow hover:shadow-md">
                <CardContent className="p-6">
                  <p className="text-sm font-medium text-slate-500">Period Change</p>
                  <p
                    className={cn(
                      'mt-1 text-2xl font-bold tabular-nums',
                      (trendData.change_pct ?? 0) > 0
                        ? 'text-green-600'
                        : (trendData.change_pct ?? 0) < 0
                          ? 'text-red-600'
                          : ''
                    )}
                  >
                    {trendData.change_pct != null
                      ? `${trendData.change_pct > 0 ? '+' : ''}${trendData.change_pct.toFixed(1)}%`
                      : 'N/A'}
                  </p>
                  <p className="text-xs text-slate-400">last {trendData.period_days} days</p>
                </CardContent>
              </Card>
            </div>
          ) : (
            <Card>
              <CardContent className="flex flex-col items-center justify-center py-16 text-slate-400">
                <TrendingUp className="mb-2 h-10 w-10" />
                <p className="text-sm">No trend data available</p>
              </CardContent>
            </Card>
          )}

          {/* Trend description */}
          {trendData?.description && (
            <Card>
              <CardContent className="p-4">
                <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed">
                  {trendData.description}
                </p>
                <p className="mt-2 text-xs text-slate-400">
                  Based on {trendData.data_points} data points over {trendData.period_days} days
                </p>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* ============================================================
            TAB: Prediction Accuracy
            ============================================================ */}
        <TabsContent value="accuracy" className="space-y-4 mt-4">
          {accuracyLoading ? (
            <div className="flex h-64 items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
            </div>
          ) : accuracyData ? (
            <>
              {/* Overall accuracy */}
              {accuracyData.overall_accuracy_pct != null && (
                <Card>
                  <CardContent className="flex items-center gap-6 p-6">
                    <AccuracyGauge
                      value={accuracyData.overall_accuracy_pct}
                      label="Overall"
                    />
                    <div>
                      <p className="text-lg font-bold text-slate-900 dark:text-white">
                        Overall Prediction Accuracy
                      </p>
                      <p className="text-sm text-slate-500">
                        Weighted average across all prediction types (100 - MAPE)
                      </p>
                    </div>
                  </CardContent>
                </Card>
              )}

              {/* Per-type accuracy */}
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {accuracyData.metrics.map((m) => (
                  <Card key={m.metric} className="transition-shadow hover:shadow-md">
                    <CardContent className="flex flex-col items-center p-6">
                      <AccuracyGauge value={m.accuracy_pct} label={m.metric} />
                      <div className="mt-3 text-center">
                        <p className="text-xs text-slate-400 tabular-nums">
                          MAPE: {m.mape.toFixed(1)}%
                        </p>
                        <p className="text-xs text-slate-400">
                          {m.predictions_count} predictions evaluated
                        </p>
                      </div>
                    </CardContent>
                  </Card>
                ))}

                {accuracyData.metrics.length === 0 && (
                  <div className="col-span-full flex flex-col items-center justify-center py-16 text-slate-400">
                    <Gauge className="mb-2 h-10 w-10" />
                    <p className="text-sm font-medium">No accuracy data available yet</p>
                    <p className="mt-1 text-xs">
                      Accuracy metrics appear after predictions have been evaluated against actual data
                    </p>
                  </div>
                )}
              </div>
            </>
          ) : (
            <Card>
              <CardContent className="flex flex-col items-center justify-center py-16 text-slate-400">
                <Target className="mb-2 h-10 w-10" />
                <p className="text-sm">Unable to load accuracy data</p>
              </CardContent>
            </Card>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
