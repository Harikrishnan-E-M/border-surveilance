'use client';

import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { format } from 'date-fns';
import {
  Bell,
  Filter,
  Search,
  ChevronDown,
  ChevronUp,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Info,
  Loader2,
  Image as ImageIcon,
  RefreshCw,
  CheckCheck,
  X,
} from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { apiClient } from '@/lib/api-client';
import { useAlertStore } from '@/stores/useAlertStore';

interface Alert {
  id: string;
  severity: 'critical' | 'warning' | 'info';
  type: string;
  camera_id: string;
  camera_name: string;
  message: string;
  status: 'new' | 'acknowledged' | 'resolved';
  snapshot_url: string | null;
  details: Record<string, any>;
  created_at: string;
  updated_at: string;
}

interface AlertFilters {
  severity: string[];
  type: string;
  camera_id: string;
  status: string;
  date_from: string;
  date_to: string;
  search: string;
}

interface CameraOption {
  id: string;
  name: string;
}

const severityOptions = ['critical', 'warning', 'info'];
const statusOptions = ['new', 'acknowledged', 'resolved'];
const typeOptions = [
  'intrusion',
  'loitering',
  'crowd',
  'fire',
  'ppe_violation',
  'face_recognized',
  'face_unknown',
  'vehicle_detected',
  'line_crossing',
  'abandoned_object',
];

export default function AlertsPage() {
  const queryClient = useQueryClient();
  const { setUnreadCount } = useAlertStore();

  const [filters, setFilters] = useState<AlertFilters>({
    severity: [],
    type: '',
    camera_id: '',
    status: '',
    date_from: '',
    date_to: '',
    search: '',
  });

  const [showFilters, setShowFilters] = useState(false);
  const [selectedAlerts, setSelectedAlerts] = useState<Set<string>>(new Set());
  const [expandedAlert, setExpandedAlert] = useState<string | null>(null);
  const [sortField, setSortField] = useState<'created_at' | 'severity' | 'type'>('created_at');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [page, setPage] = useState(1);
  const pageSize = 25;

  const { data: cameras } = useQuery<CameraOption[]>({
    queryKey: ['cameras', 'options'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/cameras', { params: { limit: 200 } });
      return (res.data.items || res.data).map((c: any) => ({ id: c.id, name: c.name }));
    },
  });

  const { data: alertsData, isLoading, isFetching } = useQuery({
    queryKey: ['alerts', filters, sortField, sortDir, page],
    queryFn: async () => {
      const params: Record<string, any> = {
        page,
        page_size: pageSize,
        sort_by: sortField,
        sort_dir: sortDir,
      };
      if (filters.severity.length) params.severity = filters.severity.join(',');
      if (filters.type) params.type = filters.type;
      if (filters.camera_id) params.camera_id = filters.camera_id;
      if (filters.status) params.status = filters.status;
      if (filters.date_from) params.date_from = filters.date_from;
      if (filters.date_to) params.date_to = filters.date_to;
      if (filters.search) params.search = filters.search;

      const res: any = await apiClient.get('/api/v1/alerts', { params });
      let items: any[] = [];
      let total = 0;

      if (Array.isArray(res)) {
        items = res;
        total = res.length;
      } else if (res && Array.isArray(res.data)) {
        items = res.data;
        total = res.total ?? res.data.length;
      } else if (res && Array.isArray(res.items)) {
        items = res.items;
        total = res.total ?? res.items.length;
      }

      return { items, total };
    },
    refetchInterval: 5000,
  });

  const alerts = alertsData?.items ?? [];
  const totalAlerts = alertsData?.total ?? 0;
  const totalPages = Math.ceil(totalAlerts / pageSize);

  // Update unread count — use alertsData.items directly to avoid unstable dependency
  useEffect(() => {
    if (alertsData) {
      const newCount = alertsData.items.filter((a: { status: string }) => a.status === 'new').length;
      setUnreadCount(newCount);
    }
  }, [alertsData, setUnreadCount]);

  const acknowledgeMutation = useMutation({
    mutationFn: async (alertIds: string[]) => {
      await apiClient.post('/api/v1/alerts/batch-acknowledge', { alert_ids: alertIds });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['alerts'] });
      setSelectedAlerts(new Set());
    },
  });

  const resolveMutation = useMutation({
    mutationFn: async (alertIds: string[]) => {
      await apiClient.post('/api/v1/alerts/batch-resolve', { alert_ids: alertIds });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['alerts'] });
      setSelectedAlerts(new Set());
    },
  });

  const toggleSort = (field: typeof sortField) => {
    if (sortField === field) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortField(field);
      setSortDir('desc');
    }
  };

  const toggleSeverity = (sev: string) => {
    setFilters((prev) => ({
      ...prev,
      severity: prev.severity.includes(sev)
        ? prev.severity.filter((s) => s !== sev)
        : [...prev.severity, sev],
    }));
    setPage(1);
  };

  const toggleSelectAlert = (id: string) => {
    setSelectedAlerts((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selectedAlerts.size === alerts.length) {
      setSelectedAlerts(new Set());
    } else {
      setSelectedAlerts(new Set(alerts.map((a: Alert) => a.id)));
    }
  };

  const severityColor = (severity: string) => {
    switch (severity) {
      case 'critical':
        return 'bg-red-100 text-red-700 border-red-200 dark:bg-red-900/30 dark:text-red-400 dark:border-red-800';
      case 'warning':
        return 'bg-amber-100 text-amber-700 border-amber-200 dark:bg-amber-900/30 dark:text-amber-400 dark:border-amber-800';
      default:
        return 'bg-blue-100 text-blue-700 border-blue-200 dark:bg-blue-900/30 dark:text-blue-400 dark:border-blue-800';
    }
  };

  const statusColor = (status: string) => {
    switch (status) {
      case 'new':
        return 'bg-red-50 text-red-600 dark:bg-red-900/20 dark:text-red-400';
      case 'acknowledged':
        return 'bg-amber-50 text-amber-600 dark:bg-amber-900/20 dark:text-amber-400';
      case 'resolved':
        return 'bg-green-50 text-green-600 dark:bg-green-900/20 dark:text-green-400';
      default:
        return 'bg-slate-50 text-slate-600 dark:bg-slate-800 dark:text-slate-400';
    }
  };

  const SeverityIcon = ({ severity }: { severity: string }) => {
    switch (severity) {
      case 'critical':
        return <XCircle className="h-4 w-4 text-red-500" />;
      case 'warning':
        return <AlertTriangle className="h-4 w-4 text-amber-500" />;
      default:
        return <Info className="h-4 w-4 text-blue-500" />;
    }
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Alerts</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {totalAlerts} total alert{totalAlerts !== 1 ? 's' : ''}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowFilters(!showFilters)}
          >
            <Filter className="mr-1 h-4 w-4" />
            Filters
            {(filters.severity.length > 0 || filters.type || filters.camera_id || filters.status) && (
              <Badge className="ml-1 h-5 w-5 rounded-full p-0 text-xs" variant="destructive">
                !
              </Badge>
            )}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => queryClient.invalidateQueries({ queryKey: ['alerts'] })}
          >
            <RefreshCw className={`mr-1 h-4 w-4 ${isFetching ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
        </div>
      </div>

      {/* Filters Panel */}
      {showFilters && (
        <Card>
          <CardContent className="p-4">
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {/* Severity Multi-select */}
              <div className="space-y-2">
                <Label className="text-xs font-medium">Severity</Label>
                <div className="flex flex-wrap gap-1">
                  {severityOptions.map((sev) => (
                    <button
                      key={sev}
                      onClick={() => toggleSeverity(sev)}
                      className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                        filters.severity.includes(sev)
                          ? severityColor(sev)
                          : 'border-slate-200 text-slate-500 dark:border-slate-700 dark:text-slate-400'
                      }`}
                    >
                      {sev.charAt(0).toUpperCase() + sev.slice(1)}
                    </button>
                  ))}
                </div>
              </div>

              {/* Type Dropdown */}
              <div className="space-y-2">
                <Label className="text-xs font-medium">Type</Label>
                <select
                  value={filters.type}
                  onChange={(e) => {
                    setFilters((prev) => ({ ...prev, type: e.target.value }));
                    setPage(1);
                  }}
                  className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                >
                  <option value="">All types</option>
                  {typeOptions.map((t) => (
                    <option key={t} value={t}>
                      {t.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase())}
                    </option>
                  ))}
                </select>
              </div>

              {/* Camera Dropdown */}
              <div className="space-y-2">
                <Label className="text-xs font-medium">Camera</Label>
                <select
                  value={filters.camera_id}
                  onChange={(e) => {
                    setFilters((prev) => ({ ...prev, camera_id: e.target.value }));
                    setPage(1);
                  }}
                  className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                >
                  <option value="">All cameras</option>
                  {cameras?.map((cam) => (
                    <option key={cam.id} value={cam.id}>
                      {cam.name}
                    </option>
                  ))}
                </select>
              </div>

              {/* Status */}
              <div className="space-y-2">
                <Label className="text-xs font-medium">Status</Label>
                <select
                  value={filters.status}
                  onChange={(e) => {
                    setFilters((prev) => ({ ...prev, status: e.target.value }));
                    setPage(1);
                  }}
                  className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                >
                  <option value="">All statuses</option>
                  {statusOptions.map((s) => (
                    <option key={s} value={s}>
                      {s.charAt(0).toUpperCase() + s.slice(1)}
                    </option>
                  ))}
                </select>
              </div>

              {/* Date From */}
              <div className="space-y-2">
                <Label className="text-xs font-medium">Date From</Label>
                <Input
                  type="datetime-local"
                  value={filters.date_from}
                  onChange={(e) => {
                    setFilters((prev) => ({ ...prev, date_from: e.target.value }));
                    setPage(1);
                  }}
                  className="text-sm"
                />
              </div>

              {/* Date To */}
              <div className="space-y-2">
                <Label className="text-xs font-medium">Date To</Label>
                <Input
                  type="datetime-local"
                  value={filters.date_to}
                  onChange={(e) => {
                    setFilters((prev) => ({ ...prev, date_to: e.target.value }));
                    setPage(1);
                  }}
                  className="text-sm"
                />
              </div>

              {/* Search */}
              <div className="space-y-2 sm:col-span-2">
                <Label className="text-xs font-medium">Search</Label>
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                  <Input
                    placeholder="Search alerts..."
                    value={filters.search}
                    onChange={(e) => {
                      setFilters((prev) => ({ ...prev, search: e.target.value }));
                      setPage(1);
                    }}
                    className="pl-10 text-sm"
                  />
                </div>
              </div>
            </div>

            <div className="mt-3 flex justify-end">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setFilters({
                    severity: [],
                    type: '',
                    camera_id: '',
                    status: '',
                    date_from: '',
                    date_to: '',
                    search: '',
                  });
                  setPage(1);
                }}
              >
                <X className="mr-1 h-3 w-3" />
                Clear filters
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Batch Actions */}
      {selectedAlerts.size > 0 && (
        <div className="flex items-center gap-3 rounded-lg border border-blue-200 bg-blue-50 px-4 py-2 dark:border-blue-800 dark:bg-blue-900/20">
          <span className="text-sm font-medium text-blue-700 dark:text-blue-400">
            {selectedAlerts.size} selected
          </span>
          <Button
            size="sm"
            variant="outline"
            onClick={() => acknowledgeMutation.mutate(Array.from(selectedAlerts))}
            disabled={acknowledgeMutation.isPending}
          >
            <CheckCircle2 className="mr-1 h-3 w-3" />
            Acknowledge
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => resolveMutation.mutate(Array.from(selectedAlerts))}
            disabled={resolveMutation.isPending}
          >
            <CheckCheck className="mr-1 h-3 w-3" />
            Resolve
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setSelectedAlerts(new Set())}
          >
            Cancel
          </Button>
        </div>
      )}

      {/* Alert Table */}
      <Card>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="flex h-64 items-center justify-center">
              <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
            </div>
          ) : alerts.length === 0 ? (
            <div className="flex h-64 flex-col items-center justify-center text-slate-400">
              <Bell className="mb-3 h-10 w-10" />
              <p className="text-sm font-medium">No alerts found</p>
              <p className="mt-1 text-xs">Adjust your filters or check back later</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-200 bg-slate-50/50 dark:border-slate-700 dark:bg-slate-800/50">
                    <th className="w-10 px-4 py-3">
                      <input
                        type="checkbox"
                        checked={selectedAlerts.size === alerts.length && alerts.length > 0}
                        onChange={toggleSelectAll}
                        className="rounded border-slate-300"
                      />
                    </th>
                    <th
                      className="cursor-pointer px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300"
                      onClick={() => toggleSort('severity')}
                    >
                      <div className="flex items-center gap-1">
                        Severity
                        {sortField === 'severity' &&
                          (sortDir === 'asc' ? (
                            <ChevronUp className="h-3 w-3" />
                          ) : (
                            <ChevronDown className="h-3 w-3" />
                          ))}
                      </div>
                    </th>
                    <th
                      className="cursor-pointer px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300"
                      onClick={() => toggleSort('type')}
                    >
                      <div className="flex items-center gap-1">
                        Type
                        {sortField === 'type' &&
                          (sortDir === 'asc' ? (
                            <ChevronUp className="h-3 w-3" />
                          ) : (
                            <ChevronDown className="h-3 w-3" />
                          ))}
                      </div>
                    </th>
                    <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                      Camera
                    </th>
                    <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                      Message
                    </th>
                    <th
                      className="cursor-pointer px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300"
                      onClick={() => toggleSort('created_at')}
                    >
                      <div className="flex items-center gap-1">
                        Time
                        {sortField === 'created_at' &&
                          (sortDir === 'asc' ? (
                            <ChevronUp className="h-3 w-3" />
                          ) : (
                            <ChevronDown className="h-3 w-3" />
                          ))}
                      </div>
                    </th>
                    <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                      Status
                    </th>
                    <th className="px-4 py-3 text-right font-medium text-slate-600 dark:text-slate-300">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {alerts.map((alert: Alert) => (
                    <>
                      <tr
                        key={alert.id}
                        className={`border-b border-slate-100 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50 ${
                          alert.status === 'new'
                            ? 'bg-white dark:bg-slate-900'
                            : 'bg-slate-50/30 dark:bg-slate-900/50'
                        } ${expandedAlert === alert.id ? 'bg-blue-50/50 dark:bg-blue-900/10' : ''}`}
                        onClick={() =>
                          setExpandedAlert(expandedAlert === alert.id ? null : alert.id)
                        }
                        style={{ cursor: 'pointer' }}
                      >
                        <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                          <input
                            type="checkbox"
                            checked={selectedAlerts.has(alert.id)}
                            onChange={() => toggleSelectAlert(alert.id)}
                            className="rounded border-slate-300"
                          />
                        </td>
                        <td className="px-4 py-3">
                          <Badge className={`text-xs ${severityColor(alert.severity)}`}>
                            <SeverityIcon severity={alert.severity} />
                            <span className="ml-1">{alert.severity}</span>
                          </Badge>
                        </td>
                        <td className="px-4 py-3 font-medium text-slate-700 dark:text-slate-300">
                          {alert.type.replace(/_/g, ' ').replace(/\b\w/g, (l: string) => l.toUpperCase())}
                        </td>
                        <td className="px-4 py-3 text-slate-600 dark:text-slate-400">
                          {alert.camera_name}
                        </td>
                        <td className="max-w-xs truncate px-4 py-3 text-slate-600 dark:text-slate-400">
                          {alert.message}
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 text-slate-500 dark:text-slate-400">
                          {format(new Date(alert.created_at), 'MMM dd, HH:mm:ss')}
                        </td>
                        <td className="px-4 py-3">
                          <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${statusColor(alert.status)}`}>
                            {alert.status}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-right" onClick={(e) => e.stopPropagation()}>
                          <div className="flex items-center justify-end gap-1">
                            {alert.status === 'new' && (
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => acknowledgeMutation.mutate([alert.id])}
                                title="Acknowledge"
                              >
                                <CheckCircle2 className="h-4 w-4" />
                              </Button>
                            )}
                            {alert.status !== 'resolved' && (
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => resolveMutation.mutate([alert.id])}
                                title="Resolve"
                              >
                                <CheckCheck className="h-4 w-4" />
                              </Button>
                            )}
                          </div>
                        </td>
                      </tr>

                      {/* Expanded Row */}
                      {expandedAlert === alert.id && (
                        <tr key={`${alert.id}-expanded`}>
                          <td
                            colSpan={8}
                            className="border-b border-slate-200 bg-slate-50 px-6 py-4 dark:border-slate-700 dark:bg-slate-800/50"
                          >
                            <div className="grid gap-4 md:grid-cols-2">
                              {/* Snapshot */}
                              <div>
                                {alert.snapshot_url ? (
                                  <img
                                    src={alert.snapshot_url}
                                    alt="Alert snapshot"
                                    className="w-full rounded-lg border border-slate-200 dark:border-slate-700"
                                  />
                                ) : (
                                  <div className="flex h-48 items-center justify-center rounded-lg border border-dashed border-slate-300 bg-slate-100 dark:border-slate-600 dark:bg-slate-800">
                                    <div className="flex flex-col items-center text-slate-400">
                                      <ImageIcon className="mb-1 h-6 w-6" />
                                      <span className="text-xs">No snapshot available</span>
                                    </div>
                                  </div>
                                )}
                              </div>

                              {/* Details */}
                              <div className="space-y-3">
                                <h4 className="font-medium text-slate-900 dark:text-white">
                                  Alert Details
                                </h4>
                                <div className="space-y-2 text-sm">
                                  <div className="flex justify-between">
                                    <span className="text-slate-500">ID</span>
                                    <span className="font-mono text-xs text-slate-700 dark:text-slate-300">
                                      {alert.id}
                                    </span>
                                  </div>
                                  <div className="flex justify-between">
                                    <span className="text-slate-500">Camera</span>
                                    <span className="text-slate-700 dark:text-slate-300">
                                      {alert.camera_name}
                                    </span>
                                  </div>
                                  <div className="flex justify-between">
                                    <span className="text-slate-500">Created</span>
                                    <span className="text-slate-700 dark:text-slate-300">
                                      {format(new Date(alert.created_at), 'PPpp')}
                                    </span>
                                  </div>
                                  <div className="flex justify-between">
                                    <span className="text-slate-500">Updated</span>
                                    <span className="text-slate-700 dark:text-slate-300">
                                      {format(new Date(alert.updated_at), 'PPpp')}
                                    </span>
                                  </div>
                                  <div className="flex justify-between">
                                    <span className="text-slate-500">Full Message</span>
                                    <span className="max-w-[250px] text-right text-slate-700 dark:text-slate-300">
                                      {alert.message}
                                    </span>
                                  </div>
                                  {Object.entries(alert.details || {}).map(([key, value]) => (
                                    <div key={key} className="flex justify-between">
                                      <span className="text-slate-500">
                                        {key.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase())}
                                      </span>
                                      <span className="text-slate-700 dark:text-slate-300">
                                        {typeof value === 'object'
                                          ? JSON.stringify(value)
                                          : String(value)}
                                      </span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between border-t border-slate-200 px-4 py-3 dark:border-slate-700">
              <span className="text-sm text-slate-500">
                Page {page} of {totalPages} ({totalAlerts} results)
              </span>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page === 1}
                >
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={page === totalPages}
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
