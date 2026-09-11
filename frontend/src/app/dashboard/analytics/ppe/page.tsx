'use client';

import { useState, useMemo, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
  Cell,
} from 'recharts';
import { format, subDays, parseISO } from 'date-fns';
import {
  Loader2,
  HardHat,
  ShieldAlert,
  ShieldCheck,
  Camera,
  AlertTriangle,
  Eye,
  CalendarDays,
  RefreshCw,
  ChevronRight,
  Clock,
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { PPEGauge } from '@/components/analytics/ppe-gauge';
import { apiClient } from '@/lib/api-client';
import { cn, formatDate, formatRelativeTime } from '@/lib/utils';

// -------------------------------------------------------------------
// Types
// -------------------------------------------------------------------

interface PPESummary {
  overall_compliance: number;
  total_detections: number;
  total_violations: number;
  compliance_change: number; // percentage change from previous period
}

interface ComplianceTrendPoint {
  date: string;
  compliance: number;
  violations: number;
  detections: number;
}

interface ViolationBreakdown {
  type: string;
  count: number;
  percentage: number;
  color: string;
}

interface CameraCompliance {
  camera_id: string;
  camera_name: string;
  compliance: number;
  violations: number;
  detections: number;
}

interface RecentViolation {
  id: string;
  timestamp: string;
  camera_name: string;
  violation_type: string;
  thumbnail_url: string;
  confidence: number;
  person_id?: string;
  person_name?: string;
}

interface PPEResponse {
  summary: PPESummary;
  compliance_trend: ComplianceTrendPoint[];
  violation_breakdown: ViolationBreakdown[];
  camera_compliance: CameraCompliance[];
  recent_violations: RecentViolation[];
}

interface CameraItem {
  id: string;
  name: string;
}

// -------------------------------------------------------------------
// Constants
// -------------------------------------------------------------------

const PPE_TYPE_ICONS: Record<string, string> = {
  helmet: '🪖',
  vest: '🦺',
  goggles: '🥽',
  gloves: '🧤',
  boots: '👢',
  mask: '😷',
  harness: '🪢',
};

const VIOLATION_COLORS: Record<string, string> = {
  helmet: '#ef4444',
  vest: '#f97316',
  goggles: '#eab308',
  gloves: '#22c55e',
  boots: '#3b82f6',
  mask: '#8b5cf6',
  harness: '#ec4899',
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
    formatted = format(parseISO(label), 'MMM d, yyyy');
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
          <span className="font-semibold">
            {entry.name === 'Compliance' ? `${entry.value}%` : entry.value.toLocaleString()}
          </span>
        </div>
      ))}
    </div>
  );
}

// -------------------------------------------------------------------
// Page
// -------------------------------------------------------------------

export default function PPECompliancePage() {
  // Filters
  const [dateFrom, setDateFrom] = useState(() => format(subDays(new Date(), 30), 'yyyy-MM-dd'));
  const [dateTo, setDateTo] = useState(() => format(new Date(), 'yyyy-MM-dd'));
  const [selectedCamera, setSelectedCamera] = useState<string>('all');
  const [activeTab, setActiveTab] = useState<string>('overview');

  // -------------------------------------------------------------------
  // Queries
  // -------------------------------------------------------------------

  const { data: cameras } = useQuery<CameraItem[]>({
    queryKey: ['cameras-list-ppe'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/cameras', {
        params: { page_size: 200 },
      });
      return res.data?.results ?? res.data ?? [];
    },
  });

  const {
    data: ppeData,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery<PPEResponse>({
    queryKey: ['ppe-compliance', dateFrom, dateTo, selectedCamera],
    queryFn: async () => {
      const params: Record<string, string> = {
        date_from: dateFrom,
        date_to: dateTo,
      };
      if (selectedCamera !== 'all') params.camera_id = selectedCamera;

      const res = await apiClient.get('/api/v1/analytics/ppe', { params });
      return res.data;
    },
  });

  // -------------------------------------------------------------------
  // Derived
  // -------------------------------------------------------------------

  const summary = ppeData?.summary;
  const complianceTrend = ppeData?.compliance_trend ?? [];
  const violationBreakdown = ppeData?.violation_breakdown ?? [];
  const cameraCompliance = ppeData?.camera_compliance ?? [];
  const recentViolations = ppeData?.recent_violations ?? [];

  // Per-type gauges
  const ppeTypeGauges = useMemo(() => {
    if (violationBreakdown.length === 0) return [];
    const totalViolations = violationBreakdown.reduce((acc, v) => acc + v.count, 0);
    return violationBreakdown.map((v) => ({
      ...v,
      complianceForType:
        totalViolations > 0 ? Math.round(100 - v.percentage) : 100,
    }));
  }, [violationBreakdown]);

  // Camera bar chart data sorted by compliance
  const sortedCameraCompliance = useMemo(() => {
    return [...cameraCompliance].sort((a, b) => a.compliance - b.compliance);
  }, [cameraCompliance]);

  // -------------------------------------------------------------------
  // Loading
  // -------------------------------------------------------------------

  if (isLoading && !ppeData) {
    return (
      <div className="flex h-96 items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
          <p className="text-sm text-slate-500">Loading PPE compliance data...</p>
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
            PPE Compliance Dashboard
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Monitor personal protective equipment compliance across all zones
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => refetch()}>
          <RefreshCw className="mr-1.5 h-4 w-4" />
          Refresh
        </Button>
      </div>

      {/* Filters */}
      <Card>
        <CardContent className="p-4">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <div className="space-y-1.5">
              <Label className="text-xs">Date From</Label>
              <div className="relative">
                <CalendarDays className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  type="date"
                  value={dateFrom}
                  onChange={(e) => setDateFrom(e.target.value)}
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
                  onChange={(e) => setDateTo(e.target.value)}
                  className="pl-9"
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Camera</Label>
              <Select value={selectedCamera} onValueChange={setSelectedCamera}>
                <SelectTrigger>
                  <SelectValue placeholder="All Cameras" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Cameras</SelectItem>
                  {(cameras ?? []).map((cam) => (
                    <SelectItem key={cam.id} value={cam.id}>
                      {cam.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Error */}
      {isError && (
        <Card className="border-red-200 bg-red-50 dark:border-red-800 dark:bg-red-950/30">
          <CardContent className="flex items-center gap-3 p-4">
            <ShieldAlert className="h-5 w-5 text-red-500" />
            <div>
              <p className="text-sm font-medium text-red-700 dark:text-red-400">
                Failed to load PPE compliance data
              </p>
              <p className="text-xs text-red-600 dark:text-red-500">
                {(error as { message?: string })?.message ?? 'An unexpected error occurred.'}
              </p>
            </div>
            <Button variant="outline" size="sm" className="ml-auto" onClick={() => refetch()}>
              Retry
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Overall Compliance Gauge + Summary Cards */}
      <div className="grid gap-4 lg:grid-cols-5">
        {/* Main Gauge */}
        <Card className="lg:col-span-2">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Overall Compliance</CardTitle>
            <CardDescription>
              Across all cameras and PPE types
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col items-center pb-6">
            <PPEGauge
              percentage={summary?.overall_compliance ?? 0}
              label="Overall Compliance"
              size={200}
              strokeWidth={16}
            />
            <div className="mt-4 flex items-center gap-2">
              {(summary?.compliance_change ?? 0) > 0 ? (
                <Badge className="bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400">
                  +{summary?.compliance_change}% from previous period
                </Badge>
              ) : (summary?.compliance_change ?? 0) < 0 ? (
                <Badge variant="critical">
                  {summary?.compliance_change}% from previous period
                </Badge>
              ) : (
                <Badge variant="secondary">
                  No change from previous period
                </Badge>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Summary Stat Cards */}
        <div className="grid gap-4 sm:grid-cols-3 lg:col-span-3 lg:grid-cols-1 lg:grid-rows-3">
          {/* Total Detections */}
          <Card className="transition-shadow hover:shadow-md">
            <CardContent className="p-5">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-slate-500 dark:text-slate-400">
                    Total Detections
                  </p>
                  <p className="mt-1 text-2xl font-bold text-slate-900 dark:text-white tabular-nums">
                    {(summary?.total_detections ?? 0).toLocaleString()}
                  </p>
                </div>
                <div className="rounded-xl p-3 bg-blue-50 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400">
                  <Eye className="h-5 w-5" />
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Total Violations */}
          <Card className="transition-shadow hover:shadow-md">
            <CardContent className="p-5">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-slate-500 dark:text-slate-400">
                    Total Violations
                  </p>
                  <p className="mt-1 text-2xl font-bold text-red-600 dark:text-red-400 tabular-nums">
                    {(summary?.total_violations ?? 0).toLocaleString()}
                  </p>
                </div>
                <div className="rounded-xl p-3 bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-400">
                  <ShieldAlert className="h-5 w-5" />
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Compliance Rate */}
          <Card className="transition-shadow hover:shadow-md">
            <CardContent className="p-5">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-slate-500 dark:text-slate-400">
                    Compliance Rate
                  </p>
                  <p className="mt-1 text-2xl font-bold text-slate-900 dark:text-white tabular-nums">
                    {summary?.overall_compliance ?? 0}%
                  </p>
                </div>
                <div className="rounded-xl p-3 bg-green-50 text-green-600 dark:bg-green-900/30 dark:text-green-400">
                  <ShieldCheck className="h-5 w-5" />
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Tabbed Content */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="overview">Trend & Breakdown</TabsTrigger>
          <TabsTrigger value="cameras">Camera Comparison</TabsTrigger>
          <TabsTrigger value="violations">Recent Violations</TabsTrigger>
        </TabsList>

        {/* Overview Tab */}
        <TabsContent value="overview" className="space-y-6">
          {/* Compliance Trend Chart */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Compliance Trend</CardTitle>
              <CardDescription>
                PPE compliance rate and violation count over time
              </CardDescription>
            </CardHeader>
            <CardContent>
              {complianceTrend.length === 0 ? (
                <div className="flex h-64 flex-col items-center justify-center text-slate-400">
                  <HardHat className="mb-2 h-8 w-8" />
                  <p className="text-sm">No trend data available for the selected period</p>
                </div>
              ) : (
                <ResponsiveContainer width="100%" height={350}>
                  <LineChart data={complianceTrend} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                    <XAxis
                      dataKey="date"
                      tickFormatter={(val: string) => {
                        try {
                          return format(parseISO(val), 'MMM d');
                        } catch {
                          return val;
                        }
                      }}
                      tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }}
                    />
                    <YAxis
                      yAxisId="left"
                      domain={[0, 100]}
                      tickFormatter={(val: number) => `${val}%`}
                      tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }}
                    />
                    <YAxis
                      yAxisId="right"
                      orientation="right"
                      tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }}
                    />
                    <Tooltip content={<ChartTooltip />} />
                    <Legend wrapperStyle={{ fontSize: 12, paddingTop: '8px' }} />
                    <Line
                      yAxisId="left"
                      type="monotone"
                      dataKey="compliance"
                      name="Compliance"
                      stroke="hsl(142, 76%, 36%)"
                      strokeWidth={2}
                      dot={false}
                      activeDot={{ r: 4 }}
                    />
                    <Line
                      yAxisId="right"
                      type="monotone"
                      dataKey="violations"
                      name="Violations"
                      stroke="hsl(0, 84%, 60%)"
                      strokeWidth={2}
                      dot={false}
                      activeDot={{ r: 4 }}
                    />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </CardContent>
          </Card>

          {/* Violation Breakdown by Type */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Violations by PPE Type</CardTitle>
              <CardDescription>
                Breakdown of non-compliance by equipment type
              </CardDescription>
            </CardHeader>
            <CardContent>
              {violationBreakdown.length === 0 ? (
                <div className="flex h-48 flex-col items-center justify-center text-slate-400">
                  <ShieldCheck className="mb-2 h-8 w-8" />
                  <p className="text-sm">No violation breakdown available</p>
                </div>
              ) : (
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                  {violationBreakdown.map((item) => {
                    const icon = PPE_TYPE_ICONS[item.type.toLowerCase()] ?? '🛡️';
                    return (
                      <div
                        key={item.type}
                        className="flex items-center gap-3 rounded-lg border p-4 transition-colors hover:bg-muted/50"
                      >
                        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-slate-100 text-lg dark:bg-slate-800">
                          {icon}
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium capitalize text-slate-900 dark:text-white">
                            {item.type}
                          </p>
                          <div className="flex items-center gap-2">
                            <span className="text-lg font-bold tabular-nums text-slate-900 dark:text-white">
                              {item.count.toLocaleString()}
                            </span>
                            <Badge
                              variant={item.percentage > 30 ? 'critical' : item.percentage > 15 ? 'medium' : 'low'}
                              className="text-xs"
                            >
                              {item.percentage}%
                            </Badge>
                          </div>
                        </div>
                        <div
                          className="h-12 w-1.5 rounded-full"
                          style={{
                            backgroundColor: VIOLATION_COLORS[item.type.toLowerCase()] ?? '#6b7280',
                          }}
                        />
                      </div>
                    );
                  })}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Per-type gauge row */}
          {ppeTypeGauges.length > 0 && (
            <div className="grid gap-4 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
              {ppeTypeGauges.map((g) => (
                <PPEGauge
                  key={g.type}
                  percentage={g.complianceForType}
                  label={g.type.charAt(0).toUpperCase() + g.type.slice(1)}
                  size={120}
                  strokeWidth={10}
                />
              ))}
            </div>
          )}
        </TabsContent>

        {/* Cameras Tab */}
        <TabsContent value="cameras" className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Camera-wise Compliance Comparison</CardTitle>
              <CardDescription>
                PPE compliance rate per camera (sorted lowest to highest)
              </CardDescription>
            </CardHeader>
            <CardContent>
              {sortedCameraCompliance.length === 0 ? (
                <div className="flex h-64 flex-col items-center justify-center text-slate-400">
                  <Camera className="mb-2 h-8 w-8" />
                  <p className="text-sm">No camera compliance data available</p>
                </div>
              ) : (
                <ResponsiveContainer width="100%" height={Math.max(300, sortedCameraCompliance.length * 45)}>
                  <BarChart
                    data={sortedCameraCompliance}
                    layout="vertical"
                    margin={{ top: 5, right: 30, left: 120, bottom: 5 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                    <XAxis
                      type="number"
                      domain={[0, 100]}
                      tickFormatter={(val: number) => `${val}%`}
                      tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }}
                    />
                    <YAxis
                      type="category"
                      dataKey="camera_name"
                      tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }}
                      width={110}
                    />
                    <Tooltip
                      formatter={(val: number) => [`${val}%`, 'Compliance']}
                      contentStyle={{
                        backgroundColor: 'var(--tooltip-bg, #fff)',
                        border: '1px solid #e2e8f0',
                        borderRadius: '8px',
                        fontSize: '12px',
                      }}
                    />
                    <Bar dataKey="compliance" name="Compliance %" radius={[0, 4, 4, 0]}>
                      {sortedCameraCompliance.map((entry) => (
                        <Cell
                          key={entry.camera_id}
                          fill={
                            entry.compliance >= 90
                              ? 'hsl(142, 76%, 36%)'
                              : entry.compliance >= 70
                              ? 'hsl(45, 93%, 47%)'
                              : 'hsl(0, 84%, 60%)'
                          }
                        />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              )}
            </CardContent>
          </Card>

          {/* Camera compliance cards */}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {cameraCompliance.map((cam) => (
              <Card key={cam.camera_id} className="transition-shadow hover:shadow-md">
                <CardContent className="p-5">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <div className="rounded-lg bg-slate-100 p-2 dark:bg-slate-800">
                        <Camera className="h-4 w-4 text-slate-500" />
                      </div>
                      <div>
                        <p className="text-sm font-medium text-slate-900 dark:text-white">
                          {cam.camera_name}
                        </p>
                        <p className="text-xs text-slate-400">
                          {cam.detections.toLocaleString()} detections
                        </p>
                      </div>
                    </div>
                    <div className="text-right">
                      <p
                        className={cn(
                          'text-xl font-bold tabular-nums',
                          cam.compliance >= 90
                            ? 'text-green-600 dark:text-green-400'
                            : cam.compliance >= 70
                            ? 'text-yellow-600 dark:text-yellow-400'
                            : 'text-red-600 dark:text-red-400'
                        )}
                      >
                        {cam.compliance}%
                      </p>
                      <p className="text-xs text-red-500">
                        {cam.violations} violation{cam.violations !== 1 ? 's' : ''}
                      </p>
                    </div>
                  </div>
                  <div className="mt-3 h-1.5 w-full rounded-full bg-slate-100 dark:bg-slate-700">
                    <div
                      className={cn(
                        'h-1.5 rounded-full transition-all duration-500',
                        cam.compliance >= 90
                          ? 'bg-green-500'
                          : cam.compliance >= 70
                          ? 'bg-yellow-500'
                          : 'bg-red-500'
                      )}
                      style={{ width: `${cam.compliance}%` }}
                    />
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </TabsContent>

        {/* Recent Violations Tab */}
        <TabsContent value="violations" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Recent PPE Violations</CardTitle>
              <CardDescription>
                Latest detected PPE compliance violations with thumbnails
              </CardDescription>
            </CardHeader>
            <CardContent>
              {recentViolations.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12">
                  <ShieldCheck className="mb-3 h-10 w-10 text-slate-300 dark:text-slate-600" />
                  <p className="text-sm font-medium text-slate-500 dark:text-slate-400">
                    No recent violations
                  </p>
                  <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">
                    All personnel are currently in compliance
                  </p>
                </div>
              ) : (
                <div className="space-y-3">
                  {recentViolations.map((violation) => (
                    <div
                      key={violation.id}
                      className="flex items-start gap-4 rounded-lg border p-4 transition-colors hover:bg-muted/50"
                    >
                      {/* Thumbnail */}
                      <div className="relative h-16 w-24 shrink-0 overflow-hidden rounded-md bg-slate-100 dark:bg-slate-800">
                        {violation.thumbnail_url ? (
                          <img
                            src={violation.thumbnail_url}
                            alt={`Violation: ${violation.violation_type}`}
                            className="h-full w-full object-cover"
                          />
                        ) : (
                          <div className="flex h-full w-full items-center justify-center">
                            <AlertTriangle className="h-6 w-6 text-slate-400" />
                          </div>
                        )}
                        <div className="absolute inset-0 bg-gradient-to-t from-black/30 to-transparent" />
                      </div>

                      {/* Info */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <Badge variant="critical" className="text-xs capitalize">
                            {violation.violation_type}
                          </Badge>
                          <Badge variant="outline" className="text-xs">
                            {Math.round(violation.confidence * 100)}% confidence
                          </Badge>
                        </div>
                        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500 dark:text-slate-400">
                          <span className="flex items-center gap-1">
                            <Camera className="h-3 w-3" />
                            {violation.camera_name}
                          </span>
                          <span className="flex items-center gap-1">
                            <Clock className="h-3 w-3" />
                            {formatDate(violation.timestamp, 'MMM d, HH:mm:ss')}
                          </span>
                          <span className="text-slate-400 dark:text-slate-600">
                            {formatRelativeTime(violation.timestamp)}
                          </span>
                        </div>
                        {violation.person_name && (
                          <p className="mt-1 text-xs text-slate-600 dark:text-slate-300">
                            Person: {violation.person_name}
                          </p>
                        )}
                      </div>

                      {/* Arrow */}
                      <ChevronRight className="mt-2 h-4 w-4 shrink-0 text-slate-300 dark:text-slate-600" />
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
