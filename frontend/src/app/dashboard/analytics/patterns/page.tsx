'use client';

import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts';
import { format, subDays } from 'date-fns';
import {
  Loader2,
  TrendingUp,
  TrendingDown,
  Minus,
  Clock,
  CalendarDays,
  RefreshCw,
  Activity,
  ArrowUpRight,
  BarChart3,
  Filter,
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
import { apiClient } from '@/lib/api-client';
import { cn } from '@/lib/utils';

// -------------------------------------------------------------------
// Types
// -------------------------------------------------------------------

interface HourlyDayCell {
  day: string; // Mon, Tue, ...
  day_index: number; // 0-6
  hour: number; // 0-23
  value: number;
}

interface DayOfWeekData {
  day: string;
  avg_footfall: number;
  avg_occupancy: number;
  avg_incidents: number;
}

interface PeakHourData {
  hour: number;
  label: string; // "08:00"
  avg_value: number;
  max_value: number;
  is_peak: boolean;
}

interface TrendIndicator {
  metric: string;
  current_value: number;
  previous_value: number;
  change_percent: number;
  direction: 'increasing' | 'decreasing' | 'stable';
}

interface PatternType {
  id: string;
  name: string;
  description: string;
  detected: boolean;
  confidence: number;
  details?: string;
}

interface PatternsResponse {
  hourly_grid: HourlyDayCell[];
  day_of_week: DayOfWeekData[];
  peak_hours: PeakHourData[];
  trends: TrendIndicator[];
  patterns: PatternType[];
}

interface CameraItem {
  id: string;
  name: string;
}

// -------------------------------------------------------------------
// Constants
// -------------------------------------------------------------------

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const HOURS = Array.from({ length: 24 }, (_, i) => i);

const PATTERN_TYPE_FILTERS = [
  { value: 'all', label: 'All Patterns' },
  { value: 'congestion', label: 'Congestion' },
  { value: 'anomaly', label: 'Anomaly' },
  { value: 'recurring', label: 'Recurring' },
  { value: 'seasonal', label: 'Seasonal' },
  { value: 'trend', label: 'Trend Shift' },
];

// -------------------------------------------------------------------
// Heatmap cell color
// -------------------------------------------------------------------

function getHeatmapColor(value: number, maxVal: number): string {
  if (maxVal === 0) return 'bg-slate-100 dark:bg-slate-800';
  const ratio = value / maxVal;

  if (ratio === 0) return 'bg-slate-100 dark:bg-slate-800';
  if (ratio < 0.15) return 'bg-blue-100 dark:bg-blue-950';
  if (ratio < 0.3) return 'bg-cyan-200 dark:bg-cyan-900';
  if (ratio < 0.45) return 'bg-green-200 dark:bg-green-900';
  if (ratio < 0.6) return 'bg-yellow-200 dark:bg-yellow-900';
  if (ratio < 0.75) return 'bg-orange-300 dark:bg-orange-900';
  if (ratio < 0.9) return 'bg-red-300 dark:bg-red-900';
  return 'bg-red-500 dark:bg-red-700';
}

// -------------------------------------------------------------------
// Trend icon helper
// -------------------------------------------------------------------

function TrendIcon({ direction }: { direction: 'increasing' | 'decreasing' | 'stable' }) {
  if (direction === 'increasing') {
    return <TrendingUp className="h-4 w-4 text-green-500" />;
  }
  if (direction === 'decreasing') {
    return <TrendingDown className="h-4 w-4 text-red-500" />;
  }
  return <Minus className="h-4 w-4 text-slate-400" />;
}

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

  return (
    <div className="rounded-lg border bg-background p-3 shadow-md">
      <p className="mb-1.5 text-xs font-medium text-muted-foreground">{label}</p>
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
// Page
// -------------------------------------------------------------------

export default function BehavioralPatternsPage() {
  // Filters
  const [dateFrom, setDateFrom] = useState(() => format(subDays(new Date(), 30), 'yyyy-MM-dd'));
  const [dateTo, setDateTo] = useState(() => format(new Date(), 'yyyy-MM-dd'));
  const [selectedCamera, setSelectedCamera] = useState<string>('all');
  const [patternFilter, setPatternFilter] = useState<string>('all');
  const [activeTab, setActiveTab] = useState<string>('heatmap');

  // -------------------------------------------------------------------
  // Queries
  // -------------------------------------------------------------------

  const { data: cameras } = useQuery<CameraItem[]>({
    queryKey: ['cameras-list-patterns'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/cameras', {
        params: { page_size: 200 },
      });
      return res.data?.results ?? res.data ?? [];
    },
  });

  const {
    data: patternsData,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery<PatternsResponse>({
    queryKey: ['behavioral-patterns', dateFrom, dateTo, selectedCamera],
    queryFn: async () => {
      const params: Record<string, string> = {
        date_from: dateFrom,
        date_to: dateTo,
      };
      if (selectedCamera !== 'all') params.camera_id = selectedCamera;

      const res = await apiClient.get('/api/v1/analytics/patterns', { params });
      return res.data;
    },
  });

  // -------------------------------------------------------------------
  // Derived
  // -------------------------------------------------------------------

  const hourlyGrid = patternsData?.hourly_grid ?? [];
  const dayOfWeek = patternsData?.day_of_week ?? [];
  const peakHours = patternsData?.peak_hours ?? [];
  const trends = patternsData?.trends ?? [];
  const patterns = patternsData?.patterns ?? [];

  // Build 2D heatmap matrix
  const heatmapMatrix = useMemo(() => {
    const matrix: Record<string, Record<number, number>> = {};
    let maxVal = 0;

    for (const day of DAYS) {
      matrix[day] = {};
      for (const h of HOURS) {
        matrix[day][h] = 0;
      }
    }

    for (const cell of hourlyGrid) {
      const dayName = DAYS[cell.day_index] ?? cell.day;
      if (matrix[dayName] !== undefined) {
        matrix[dayName][cell.hour] = cell.value;
        if (cell.value > maxVal) maxVal = cell.value;
      }
    }

    return { matrix, maxVal };
  }, [hourlyGrid]);

  // Filter patterns by type
  const filteredPatterns = useMemo(() => {
    if (patternFilter === 'all') return patterns;
    return patterns.filter(
      (p) => p.id.toLowerCase().includes(patternFilter) || p.name.toLowerCase().includes(patternFilter)
    );
  }, [patterns, patternFilter]);

  // Peak hours analysis
  const topPeakHours = useMemo(() => {
    return [...peakHours].filter((h) => h.is_peak).sort((a, b) => b.avg_value - a.avg_value);
  }, [peakHours]);

  // -------------------------------------------------------------------
  // Loading
  // -------------------------------------------------------------------

  if (isLoading && !patternsData) {
    return (
      <div className="flex h-96 items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
          <p className="text-sm text-slate-500">Loading behavioral patterns...</p>
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
            Behavioral Patterns
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Analyze activity patterns, peak hours, and behavioral trends
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
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
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
            <div className="space-y-1.5">
              <Label className="flex items-center gap-1.5 text-xs">
                <Filter className="h-3.5 w-3.5" />
                Pattern Type
              </Label>
              <Select value={patternFilter} onValueChange={setPatternFilter}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PATTERN_TYPE_FILTERS.map((pf) => (
                    <SelectItem key={pf.value} value={pf.value}>
                      {pf.label}
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
            <Activity className="h-5 w-5 text-red-500" />
            <div>
              <p className="text-sm font-medium text-red-700 dark:text-red-400">
                Failed to load pattern data
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

      {/* Trend Indicators */}
      {trends.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {trends.map((trend) => (
            <Card key={trend.metric} className="transition-shadow hover:shadow-md">
              <CardContent className="p-5">
                <div className="flex items-center justify-between">
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-slate-500 dark:text-slate-400">
                      {trend.metric}
                    </p>
                    <p className="mt-1 text-2xl font-bold text-slate-900 dark:text-white tabular-nums">
                      {trend.current_value.toLocaleString()}
                    </p>
                    <div className="mt-1 flex items-center gap-1.5">
                      <TrendIcon direction={trend.direction} />
                      <span
                        className={cn(
                          'text-xs font-medium tabular-nums',
                          trend.direction === 'increasing'
                            ? 'text-green-600 dark:text-green-400'
                            : trend.direction === 'decreasing'
                            ? 'text-red-600 dark:text-red-400'
                            : 'text-slate-400'
                        )}
                      >
                        {trend.change_percent > 0 ? '+' : ''}
                        {trend.change_percent}%
                      </span>
                      <span className="text-xs text-slate-400">vs previous period</span>
                    </div>
                  </div>
                  <Badge
                    variant={
                      trend.direction === 'increasing'
                        ? 'default'
                        : trend.direction === 'decreasing'
                        ? 'critical'
                        : 'secondary'
                    }
                    className="capitalize"
                  >
                    {trend.direction}
                  </Badge>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Tabbed Content */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="heatmap">Hourly Heatmap</TabsTrigger>
          <TabsTrigger value="dayofweek">Day of Week</TabsTrigger>
          <TabsTrigger value="peakhours">Peak Hours</TabsTrigger>
          <TabsTrigger value="patterns">Detected Patterns</TabsTrigger>
        </TabsList>

        {/* Hourly Heatmap Grid */}
        <TabsContent value="heatmap">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Activity Heatmap (24h x 7 Days)</CardTitle>
              <CardDescription>
                Intensity of activity by hour of day and day of week
              </CardDescription>
            </CardHeader>
            <CardContent>
              {hourlyGrid.length === 0 ? (
                <div className="flex h-64 flex-col items-center justify-center text-slate-400">
                  <BarChart3 className="mb-2 h-8 w-8" />
                  <p className="text-sm">No hourly data available for the selected period</p>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <div className="min-w-[700px]">
                    {/* Hour labels */}
                    <div className="mb-1 flex">
                      <div className="w-12 shrink-0" />
                      {HOURS.map((h) => (
                        <div
                          key={h}
                          className="flex-1 text-center text-[10px] text-muted-foreground"
                        >
                          {h.toString().padStart(2, '0')}
                        </div>
                      ))}
                    </div>

                    {/* Grid rows */}
                    {DAYS.map((day) => (
                      <div key={day} className="mb-0.5 flex items-center">
                        <div className="w-12 shrink-0 text-xs font-medium text-slate-600 dark:text-slate-400">
                          {day}
                        </div>
                        {HOURS.map((hour) => {
                          const val = heatmapMatrix.matrix[day]?.[hour] ?? 0;
                          return (
                            <div
                              key={hour}
                              className={cn(
                                'flex-1 mx-[1px] aspect-square rounded-sm transition-colors cursor-default',
                                getHeatmapColor(val, heatmapMatrix.maxVal)
                              )}
                              title={`${day} ${hour.toString().padStart(2, '0')}:00 - Value: ${val}`}
                            />
                          );
                        })}
                      </div>
                    ))}

                    {/* Color legend */}
                    <div className="mt-4 flex items-center justify-center gap-2">
                      <span className="text-xs text-muted-foreground">Low</span>
                      <div className="flex h-3 w-40 overflow-hidden rounded-sm">
                        <div className="flex-1 bg-slate-100 dark:bg-slate-800" />
                        <div className="flex-1 bg-blue-100 dark:bg-blue-950" />
                        <div className="flex-1 bg-cyan-200 dark:bg-cyan-900" />
                        <div className="flex-1 bg-green-200 dark:bg-green-900" />
                        <div className="flex-1 bg-yellow-200 dark:bg-yellow-900" />
                        <div className="flex-1 bg-orange-300 dark:bg-orange-900" />
                        <div className="flex-1 bg-red-300 dark:bg-red-900" />
                        <div className="flex-1 bg-red-500 dark:bg-red-700" />
                      </div>
                      <span className="text-xs text-muted-foreground">High</span>
                    </div>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* Day of Week Comparison */}
        <TabsContent value="dayofweek">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Day-of-Week Comparison</CardTitle>
              <CardDescription>
                Average footfall, occupancy, and incident counts by day of the week
              </CardDescription>
            </CardHeader>
            <CardContent>
              {dayOfWeek.length === 0 ? (
                <div className="flex h-64 flex-col items-center justify-center text-slate-400">
                  <BarChart3 className="mb-2 h-8 w-8" />
                  <p className="text-sm">No day-of-week data available</p>
                </div>
              ) : (
                <ResponsiveContainer width="100%" height={380}>
                  <BarChart data={dayOfWeek} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                    <XAxis
                      dataKey="day"
                      tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 12 }}
                    />
                    <YAxis tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }} />
                    <Tooltip content={<ChartTooltip />} />
                    <Legend wrapperStyle={{ fontSize: 12, paddingTop: '8px' }} />
                    <Bar
                      dataKey="avg_footfall"
                      name="Avg Footfall"
                      fill="hsl(221, 83%, 53%)"
                      radius={[4, 4, 0, 0]}
                    />
                    <Bar
                      dataKey="avg_occupancy"
                      name="Avg Occupancy"
                      fill="hsl(142, 76%, 36%)"
                      radius={[4, 4, 0, 0]}
                    />
                    <Bar
                      dataKey="avg_incidents"
                      name="Avg Incidents"
                      fill="hsl(0, 84%, 60%)"
                      radius={[4, 4, 0, 0]}
                    />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* Peak Hours */}
        <TabsContent value="peakhours">
          <div className="space-y-6">
            {/* Peak hours chart */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Hourly Activity Distribution</CardTitle>
                <CardDescription>
                  Average and peak activity levels across the 24-hour period
                </CardDescription>
              </CardHeader>
              <CardContent>
                {peakHours.length === 0 ? (
                  <div className="flex h-64 flex-col items-center justify-center text-slate-400">
                    <Clock className="mb-2 h-8 w-8" />
                    <p className="text-sm">No peak hour data available</p>
                  </div>
                ) : (
                  <ResponsiveContainer width="100%" height={350}>
                    <BarChart data={peakHours} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                      <XAxis
                        dataKey="label"
                        tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 10 }}
                      />
                      <YAxis tick={{ fill: 'hsl(var(--muted-foreground))', fontSize: 11 }} />
                      <Tooltip content={<ChartTooltip />} />
                      <Legend wrapperStyle={{ fontSize: 12, paddingTop: '8px' }} />
                      <Bar dataKey="avg_value" name="Average" fill="hsl(221, 83%, 53%)" radius={[2, 2, 0, 0]}>
                        {peakHours.map((entry, idx) => (
                          <rect
                            key={idx}
                            fill={entry.is_peak ? 'hsl(0, 84%, 60%)' : 'hsl(221, 83%, 53%)'}
                          />
                        ))}
                      </Bar>
                      <Bar
                        dataKey="max_value"
                        name="Peak"
                        fill="hsl(45, 93%, 47%)"
                        radius={[2, 2, 0, 0]}
                        opacity={0.5}
                      />
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </CardContent>
            </Card>

            {/* Top peak hours cards */}
            {topPeakHours.length > 0 && (
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {topPeakHours.slice(0, 4).map((ph, idx) => (
                  <Card key={ph.hour} className="transition-shadow hover:shadow-md">
                    <CardContent className="p-5">
                      <div className="flex items-center justify-between">
                        <div>
                          <div className="flex items-center gap-1.5">
                            <Badge
                              variant={idx === 0 ? 'critical' : idx === 1 ? 'high' : 'medium'}
                              className="text-xs"
                            >
                              #{idx + 1}
                            </Badge>
                            <span className="text-xs text-slate-400">Peak Hour</span>
                          </div>
                          <p className="mt-1 text-2xl font-bold text-slate-900 dark:text-white">
                            {ph.label}
                          </p>
                          <div className="mt-1 flex items-baseline gap-2">
                            <span className="text-sm text-slate-500">
                              Avg: {ph.avg_value.toLocaleString()}
                            </span>
                            <span className="text-xs text-slate-400">
                              Max: {ph.max_value.toLocaleString()}
                            </span>
                          </div>
                        </div>
                        <div className="rounded-xl p-3 bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-400">
                          <Clock className="h-5 w-5" />
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            )}
          </div>
        </TabsContent>

        {/* Detected Patterns */}
        <TabsContent value="patterns">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Detected Behavioral Patterns</CardTitle>
              <CardDescription>
                AI-detected patterns and anomalies in activity data
              </CardDescription>
            </CardHeader>
            <CardContent>
              {filteredPatterns.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12">
                  <Activity className="mb-3 h-10 w-10 text-slate-300 dark:text-slate-600" />
                  <p className="text-sm font-medium text-slate-500 dark:text-slate-400">
                    No patterns detected
                  </p>
                  <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">
                    {patternFilter !== 'all'
                      ? 'Try adjusting the pattern type filter or date range'
                      : 'Expand the date range for more data to analyze'}
                  </p>
                </div>
              ) : (
                <div className="space-y-3">
                  {filteredPatterns.map((pattern) => (
                    <div
                      key={pattern.id}
                      className={cn(
                        'flex items-start gap-4 rounded-lg border p-4 transition-colors',
                        pattern.detected
                          ? 'border-l-4 hover:bg-muted/50'
                          : 'opacity-60 hover:bg-muted/30',
                        pattern.detected && pattern.confidence > 0.8
                          ? 'border-l-red-500'
                          : pattern.detected && pattern.confidence > 0.5
                          ? 'border-l-yellow-500'
                          : 'border-l-slate-300 dark:border-l-slate-600'
                      )}
                    >
                      {/* Status indicator */}
                      <div
                        className={cn(
                          'mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full',
                          pattern.detected
                            ? 'bg-blue-100 text-blue-600 dark:bg-blue-900/40 dark:text-blue-400'
                            : 'bg-slate-100 text-slate-400 dark:bg-slate-800'
                        )}
                      >
                        {pattern.detected ? (
                          <ArrowUpRight className="h-4 w-4" />
                        ) : (
                          <Minus className="h-4 w-4" />
                        )}
                      </div>

                      {/* Info */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <p className="text-sm font-medium text-slate-900 dark:text-white">
                            {pattern.name}
                          </p>
                          {pattern.detected && (
                            <Badge
                              variant={
                                pattern.confidence > 0.8
                                  ? 'critical'
                                  : pattern.confidence > 0.5
                                  ? 'medium'
                                  : 'low'
                              }
                              className="text-xs"
                            >
                              {Math.round(pattern.confidence * 100)}% confidence
                            </Badge>
                          )}
                          {!pattern.detected && (
                            <Badge variant="secondary" className="text-xs">
                              Not detected
                            </Badge>
                          )}
                        </div>
                        <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                          {pattern.description}
                        </p>
                        {pattern.details && (
                          <p className="mt-1.5 text-xs text-slate-600 dark:text-slate-300 rounded-md bg-slate-50 dark:bg-slate-800 p-2">
                            {pattern.details}
                          </p>
                        )}
                      </div>
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
