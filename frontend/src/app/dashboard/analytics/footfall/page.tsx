'use client';

import { useState, useMemo, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  LineChart,
  Line,
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
  Download,
  Footprints,
  LogIn,
  LogOut,
  Users,
  Clock,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  CalendarDays,
  RefreshCw,
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
import { apiClient } from '@/lib/api-client';
import { cn, formatDate } from '@/lib/utils';

// -------------------------------------------------------------------
// Types
// -------------------------------------------------------------------

interface Camera {
  id: string;
  name: string;
}

interface Zone {
  id: string;
  name: string;
  camera_id: string;
}

interface FootfallSummary {
  total_entries: number;
  total_exits: number;
  avg_occupancy: number;
  peak_hour: string;
  peak_count: number;
}

interface FootfallTimeseriesPoint {
  timestamp: string;
  entries: number;
  exits: number;
  occupancy: number;
}

interface FootfallRecord {
  id: string;
  timestamp: string;
  camera_name: string;
  zone_name: string;
  entries: number;
  exits: number;
  occupancy: number;
}

interface FootfallResponse {
  summary: FootfallSummary;
  timeseries: FootfallTimeseriesPoint[];
  records: FootfallRecord[];
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

type SortField = 'timestamp' | 'camera_name' | 'zone_name' | 'entries' | 'exits' | 'occupancy';
type SortDir = 'asc' | 'desc';

// -------------------------------------------------------------------
// Page
// -------------------------------------------------------------------

export default function FootfallAnalyticsPage() {
  // Filters
  const [dateFrom, setDateFrom] = useState(() => format(subDays(new Date(), 7), 'yyyy-MM-dd'));
  const [dateTo, setDateTo] = useState(() => format(new Date(), 'yyyy-MM-dd'));
  const [selectedCamera, setSelectedCamera] = useState<string>('all');
  const [selectedZone, setSelectedZone] = useState<string>('all');

  // Table sorting
  const [sortField, setSortField] = useState<SortField>('timestamp');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  // -------------------------------------------------------------------
  // Queries
  // -------------------------------------------------------------------

  const { data: cameras } = useQuery<Camera[]>({
    queryKey: ['cameras-list'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/cameras', {
        params: { page_size: 200 },
      });
      return res.data?.results ?? res.data ?? [];
    },
  });

  const { data: zones } = useQuery<Zone[]>({
    queryKey: ['zones-list', selectedCamera],
    queryFn: async () => {
      const params: Record<string, string> = {};
      if (selectedCamera !== 'all') params.camera_id = selectedCamera;
      const res = await apiClient.get('/api/v1/zones', { params });
      return res.data?.results ?? res.data ?? [];
    },
  });

  const {
    data: footfallData,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery<FootfallResponse>({
    queryKey: ['footfall-analytics', dateFrom, dateTo, selectedCamera, selectedZone],
    queryFn: async () => {
      const params: Record<string, string> = {
        date_from: dateFrom,
        date_to: dateTo,
      };
      if (selectedCamera !== 'all') params.camera_id = selectedCamera;
      if (selectedZone !== 'all') params.zone_id = selectedZone;

      const res = await apiClient.get('/api/v1/analytics/footfall', { params });
      return res.data;
    },
  });

  // -------------------------------------------------------------------
  // Derived data
  // -------------------------------------------------------------------

  const summary = footfallData?.summary;
  const timeseries = footfallData?.timeseries ?? [];
  const records = footfallData?.records ?? [];

  const sortedRecords = useMemo(() => {
    return [...records].sort((a, b) => {
      const aVal = a[sortField];
      const bVal = b[sortField];

      if (aVal === null || aVal === undefined) return 1;
      if (bVal === null || bVal === undefined) return -1;

      let cmp = 0;
      if (typeof aVal === 'number' && typeof bVal === 'number') {
        cmp = aVal - bVal;
      } else {
        cmp = String(aVal).localeCompare(String(bVal));
      }

      return sortDir === 'asc' ? cmp : -cmp;
    });
  }, [records, sortField, sortDir]);

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

  const handleExport = useCallback(async () => {
    try {
      const params: Record<string, string> = {
        date_from: dateFrom,
        date_to: dateTo,
        format: 'csv',
      };
      if (selectedCamera !== 'all') params.camera_id = selectedCamera;
      if (selectedZone !== 'all') params.zone_id = selectedZone;

      const res = await apiClient.get('/api/v1/analytics/footfall/export', {
        params,
        responseType: 'blob',
      });

      const url = window.URL.createObjectURL(new Blob([res.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `footfall_${dateFrom}_${dateTo}.csv`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch {
      // silent – the query error boundary handles visibility
    }
  }, [dateFrom, dateTo, selectedCamera, selectedZone]);

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
  // Stat cards
  // -------------------------------------------------------------------

  const statCards = useMemo(
    () => [
      {
        title: 'Total Entries',
        value: summary?.total_entries ?? 0,
        icon: LogIn,
        color: 'bg-green-50 text-green-600 dark:bg-green-900/30 dark:text-green-400',
      },
      {
        title: 'Total Exits',
        value: summary?.total_exits ?? 0,
        icon: LogOut,
        color: 'bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-400',
      },
      {
        title: 'Avg Occupancy',
        value: summary?.avg_occupancy ?? 0,
        icon: Users,
        color: 'bg-blue-50 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400',
      },
      {
        title: 'Peak Hour',
        value: summary?.peak_hour ?? '--',
        subtitle: summary?.peak_count ? `${summary.peak_count.toLocaleString()} people` : undefined,
        icon: Clock,
        color: 'bg-purple-50 text-purple-600 dark:bg-purple-900/30 dark:text-purple-400',
      },
    ],
    [summary]
  );

  // -------------------------------------------------------------------
  // Loading state
  // -------------------------------------------------------------------

  if (isLoading && !footfallData) {
    return (
      <div className="flex h-96 items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
          <p className="text-sm text-slate-500">Loading footfall analytics...</p>
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
            Footfall Analytics
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Track entries, exits, and occupancy across cameras and zones
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => refetch()}>
            <RefreshCw className="mr-1.5 h-4 w-4" />
            Refresh
          </Button>
          <Button size="sm" onClick={handleExport}>
            <Download className="mr-1.5 h-4 w-4" />
            Export CSV
          </Button>
        </div>
      </div>

      {/* Filters */}
      <Card>
        <CardContent className="p-4">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {/* Date From */}
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

            {/* Date To */}
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

            {/* Camera Selector */}
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

            {/* Zone Selector */}
            <div className="space-y-1.5">
              <Label className="text-xs">Zone</Label>
              <Select value={selectedZone} onValueChange={setSelectedZone}>
                <SelectTrigger>
                  <SelectValue placeholder="All Zones" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Zones</SelectItem>
                  {(zones ?? []).map((zone) => (
                    <SelectItem key={zone.id} value={zone.id}>
                      {zone.name}
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
            <Footprints className="h-5 w-5 text-red-500" />
            <div>
              <p className="text-sm font-medium text-red-700 dark:text-red-400">
                Failed to load footfall data
              </p>
              <p className="text-xs text-red-600 dark:text-red-500">
                {(error as { message?: string })?.message ?? 'An unexpected error occurred. Please try again.'}
              </p>
            </div>
            <Button variant="outline" size="sm" className="ml-auto" onClick={() => refetch()}>
              Retry
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Summary Cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {statCards.map((stat) => {
          const Icon = stat.icon;
          return (
            <Card key={stat.title} className="transition-shadow hover:shadow-md">
              <CardContent className="p-6">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium text-slate-500 dark:text-slate-400">
                      {stat.title}
                    </p>
                    <p className="mt-1 text-3xl font-bold text-slate-900 dark:text-white tabular-nums">
                      {typeof stat.value === 'number'
                        ? stat.value.toLocaleString()
                        : stat.value}
                    </p>
                    {stat.subtitle && (
                      <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">
                        {stat.subtitle}
                      </p>
                    )}
                  </div>
                  <div className={cn('rounded-xl p-3', stat.color)}>
                    <Icon className="h-6 w-6" />
                  </div>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      {/* Entries / Exits Over Time Chart */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Entries & Exits Over Time</CardTitle>
          <CardDescription>
            Footfall trend from {formatDate(dateFrom, 'MMM d, yyyy')} to{' '}
            {formatDate(dateTo, 'MMM d, yyyy')}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {timeseries.length === 0 ? (
            <div className="flex h-64 flex-col items-center justify-center text-slate-400">
              <Footprints className="mb-2 h-8 w-8" />
              <p className="text-sm">No timeseries data available for the selected period</p>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={350}>
              <LineChart data={timeseries} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
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
                <Line
                  type="monotone"
                  dataKey="entries"
                  name="Entries"
                  stroke="hsl(142, 76%, 36%)"
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4 }}
                />
                <Line
                  type="monotone"
                  dataKey="exits"
                  name="Exits"
                  stroke="hsl(0, 84%, 60%)"
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4 }}
                />
                <Line
                  type="monotone"
                  dataKey="occupancy"
                  name="Occupancy"
                  stroke="hsl(221, 83%, 53%)"
                  strokeWidth={2}
                  strokeDasharray="5 5"
                  dot={false}
                  activeDot={{ r: 4 }}
                />
              </LineChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      {/* Footfall Data Table */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between pb-3">
          <div>
            <CardTitle className="text-base">Footfall Records</CardTitle>
            <CardDescription>
              {sortedRecords.length} record{sortedRecords.length !== 1 ? 's' : ''} found
            </CardDescription>
          </div>
          <Badge variant="secondary" className="tabular-nums">
            {sortedRecords.length}
          </Badge>
        </CardHeader>
        <CardContent>
          {sortedRecords.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12">
              <Footprints className="mb-3 h-10 w-10 text-slate-300 dark:text-slate-600" />
              <p className="text-sm font-medium text-slate-500 dark:text-slate-400">
                No footfall records found
              </p>
              <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">
                Adjust the date range or camera filters to view data
              </p>
            </div>
          ) : (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>
                      <button
                        className="flex items-center text-xs font-medium"
                        onClick={() => toggleSort('timestamp')}
                      >
                        Timestamp
                        <SortIcon field="timestamp" />
                      </button>
                    </TableHead>
                    <TableHead>
                      <button
                        className="flex items-center text-xs font-medium"
                        onClick={() => toggleSort('camera_name')}
                      >
                        Camera
                        <SortIcon field="camera_name" />
                      </button>
                    </TableHead>
                    <TableHead>
                      <button
                        className="flex items-center text-xs font-medium"
                        onClick={() => toggleSort('zone_name')}
                      >
                        Zone
                        <SortIcon field="zone_name" />
                      </button>
                    </TableHead>
                    <TableHead className="text-right">
                      <button
                        className="ml-auto flex items-center text-xs font-medium"
                        onClick={() => toggleSort('entries')}
                      >
                        Entries
                        <SortIcon field="entries" />
                      </button>
                    </TableHead>
                    <TableHead className="text-right">
                      <button
                        className="ml-auto flex items-center text-xs font-medium"
                        onClick={() => toggleSort('exits')}
                      >
                        Exits
                        <SortIcon field="exits" />
                      </button>
                    </TableHead>
                    <TableHead className="text-right">
                      <button
                        className="ml-auto flex items-center text-xs font-medium"
                        onClick={() => toggleSort('occupancy')}
                      >
                        Occupancy
                        <SortIcon field="occupancy" />
                      </button>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sortedRecords.map((record) => (
                    <TableRow key={record.id}>
                      <TableCell className="whitespace-nowrap">
                        {formatDate(record.timestamp, 'MMM d, yyyy HH:mm')}
                      </TableCell>
                      <TableCell>{record.camera_name}</TableCell>
                      <TableCell>{record.zone_name}</TableCell>
                      <TableCell className="text-right tabular-nums font-medium text-green-600 dark:text-green-400">
                        {record.entries.toLocaleString()}
                      </TableCell>
                      <TableCell className="text-right tabular-nums font-medium text-red-600 dark:text-red-400">
                        {record.exits.toLocaleString()}
                      </TableCell>
                      <TableCell className="text-right tabular-nums font-medium">
                        {record.occupancy.toLocaleString()}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
