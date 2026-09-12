'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { format } from 'date-fns';
import {
  ClipboardList,
  Search,
  Filter,
  Loader2,
  Download,
  RefreshCw,
  ChevronDown,
  ChevronUp,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  X,
  Clock,
  User,
  Activity,
  Globe,
  Play,
} from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
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

// ---------- Types ----------

interface AuditLogEntry {
  id: string;
  timestamp: string;
  user_id: string;
  user_name: string;
  user_email: string;
  action: string;
  resource_type: string;
  resource_id: string;
  ip_address: string;
  user_agent: string;
  details: Record<string, unknown>;
  status: 'success' | 'failure';
}

interface AuditLogsResponse {
  items: AuditLogEntry[];
  total: number;
  page: number;
  page_size: number;
}

interface AuditFilters {
  search: string;
  user_id: string;
  action: string;
  resource_type: string;
  status: string;
  date_from: string;
  date_to: string;
}

// ---------- Constants ----------

const ACTION_TYPES = [
  'login',
  'logout',
  'create',
  'update',
  'delete',
  'view',
  'export',
  'import',
  'enable',
  'disable',
  'reset_password',
  'change_role',
  'acknowledge_alert',
  'resolve_alert',
  'api_key_create',
  'api_key_revoke',
  'settings_update',
];

const RESOURCE_TYPES = [
  'user',
  'camera',
  'alert',
  'face',
  'vehicle',
  'zone',
  'settings',
  'api_key',
  'notification_channel',
  'report',
  'recording',
  'session',
];

const PAGE_SIZE_OPTIONS = [10, 25, 50, 100];

const actionColors: Record<string, string> = {
  login: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
  logout: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400',
  create: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  update: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
  delete: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  view: 'bg-cyan-100 text-cyan-700 dark:bg-cyan-900/30 dark:text-cyan-400',
  export: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400',
  enable: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  disable: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
};

// ---------- Component ----------

export default function AuditPage() {
  const queryClient = useQueryClient();

  const [filters, setFilters] = useState<AuditFilters>({
    search: '',
    user_id: '',
    action: 'all',
    resource_type: 'all',
    status: 'all',
    date_from: '',
    date_to: '',
  });
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [showFilters, setShowFilters] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [sortField, setSortField] = useState<'timestamp' | 'action' | 'user_name'>('timestamp');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  // Debounce search
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(filters.search);
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [filters.search]);

  // Auto-refresh
  const autoRefreshRef = useRef(autoRefresh);
  autoRefreshRef.current = autoRefresh;

  const { data: auditData, isLoading, isFetching } = useQuery<AuditLogsResponse>({
    queryKey: ['audit-logs', debouncedSearch, filters.user_id, filters.action, filters.resource_type, filters.status, filters.date_from, filters.date_to, page, pageSize, sortField, sortDir],
    queryFn: async () => {
      const params: Record<string, string | number> = {
        page,
        page_size: pageSize,
        sort_by: sortField,
        sort_dir: sortDir,
      };
      if (debouncedSearch) params.search = debouncedSearch;
      if (filters.user_id) params.user_id = filters.user_id;
      if (filters.action !== 'all') params.action = filters.action;
      if (filters.resource_type !== 'all') params.resource_type = filters.resource_type;
      if (filters.status !== 'all') params.status = filters.status;
      if (filters.date_from) params.date_from = filters.date_from;
      if (filters.date_to) params.date_to = filters.date_to;

      const res = await apiClient.get('/api/v1/admin/audit-logs', { params });
      return res.data as AuditLogsResponse;
    },
    refetchInterval: autoRefresh ? 30000 : false,
  });

  const logs = auditData?.items || [];
  const totalLogs = auditData?.total || 0;
  const totalPages = Math.ceil(totalLogs / pageSize);

  // ----- Users list for filter -----
  const { data: usersData } = useQuery<{ id: string; name: string; email: string }[]>({
    queryKey: ['audit-users-list'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/admin/users', { params: { page_size: 500 } });
      return (res.data.items || res.data || []).map((u: any) => ({
        id: u.id,
        name: u.name,
        email: u.email,
      }));
    },
  });

  const usersList = usersData || [];

  // ----- Sort toggle -----
  const toggleSort = (field: typeof sortField) => {
    if (sortField === field) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortField(field);
      setSortDir('desc');
    }
    setPage(1);
  };

  // ----- Export CSV -----
  const handleExportCSV = useCallback(async () => {
    try {
      const params: Record<string, string | number> = {
        page_size: 10000,
        sort_by: sortField,
        sort_dir: sortDir,
        format: 'csv',
      };
      if (debouncedSearch) params.search = debouncedSearch;
      if (filters.action !== 'all') params.action = filters.action;
      if (filters.resource_type !== 'all') params.resource_type = filters.resource_type;
      if (filters.status !== 'all') params.status = filters.status;
      if (filters.date_from) params.date_from = filters.date_from;
      if (filters.date_to) params.date_to = filters.date_to;
      if (filters.user_id) params.user_id = filters.user_id;

      const res = await apiClient.get('/api/v1/admin/audit-logs/export', {
        params,
        responseType: 'blob',
      });

      const blob = new Blob([res.data], { type: 'text/csv' });
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `audit-logs-${format(new Date(), 'yyyy-MM-dd-HHmmss')}.csv`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    } catch (error) {
      // Fallback: generate CSV client-side from current data
      const headers = ['Timestamp', 'User', 'Email', 'Action', 'Resource Type', 'Resource ID', 'IP Address', 'Status', 'Details'];
      const csvRows = [
        headers.join(','),
        ...logs.map((log) =>
          [
            `"${formatDate(log.timestamp, 'yyyy-MM-dd HH:mm:ss')}"`,
            `"${log.user_name}"`,
            `"${log.user_email}"`,
            `"${log.action}"`,
            `"${log.resource_type}"`,
            `"${log.resource_id}"`,
            `"${log.ip_address}"`,
            `"${log.status}"`,
            `"${JSON.stringify(log.details).replace(/"/g, '""')}"`,
          ].join(',')
        ),
      ];

      const blob = new Blob([csvRows.join('\n')], { type: 'text/csv' });
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `audit-logs-${format(new Date(), 'yyyy-MM-dd-HHmmss')}.csv`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    }
  }, [debouncedSearch, filters, sortField, sortDir, logs]);

  // ----- Clear filters -----
  const clearFilters = () => {
    setFilters({
      search: '',
      user_id: '',
      action: 'all',
      resource_type: 'all',
      status: 'all',
      date_from: '',
      date_to: '',
    });
    setPage(1);
  };

  const hasActiveFilters =
    filters.action !== 'all' ||
    filters.resource_type !== 'all' ||
    filters.status !== 'all' ||
    filters.user_id !== '' ||
    filters.date_from !== '' ||
    filters.date_to !== '' ||
    debouncedSearch !== '';

  // ----- Render sort indicator -----
  const SortIcon = ({ field }: { field: typeof sortField }) => {
    if (sortField !== field) return <ChevronDown className="h-3 w-3 opacity-30" />;
    return sortDir === 'asc' ? (
      <ChevronUp className="h-3 w-3" />
    ) : (
      <ChevronDown className="h-3 w-3" />
    );
  };

  const getActionColor = (action: string) => {
    const baseAction = action.split('_')[0];
    return actionColors[action] || actionColors[baseAction] || 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400';
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Audit Log</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {totalLogs} log entr{totalLogs !== 1 ? 'ies' : 'y'} recorded
          </p>
        </div>
        <div className="flex items-center gap-2">
          {/* Auto-refresh toggle */}
          <div className="flex items-center gap-2 rounded-lg border px-3 py-1.5">
            <span className="text-xs text-slate-500">Auto-refresh</span>
            <Switch
              checked={autoRefresh}
              onCheckedChange={setAutoRefresh}
            />
            {autoRefresh && (
              <Badge variant="outline" className="text-xs">
                30s
              </Badge>
            )}
          </div>

          <Button
            variant="outline"
            size="sm"
            onClick={() => queryClient.invalidateQueries({ queryKey: ['audit-logs'] })}
          >
            <RefreshCw className={cn('mr-1 h-4 w-4', isFetching && 'animate-spin')} />
            Refresh
          </Button>

          <Button variant="outline" size="sm" onClick={handleExportCSV}>
            <Download className="mr-1 h-4 w-4" />
            Export CSV
          </Button>
        </div>
      </div>

      {/* Search & Filter Bar */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <Input
            placeholder="Search by user, action, resource, IP..."
            value={filters.search}
            onChange={(e) => setFilters((prev) => ({ ...prev, search: e.target.value }))}
            className="pl-10"
          />
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setShowFilters(!showFilters)}
        >
          <Filter className="mr-1 h-4 w-4" />
          Advanced Filters
          {hasActiveFilters && (
            <Badge className="ml-1 h-5 w-5 rounded-full p-0 text-xs" variant="destructive">
              !
            </Badge>
          )}
        </Button>
      </div>

      {/* Advanced Filters Panel */}
      {showFilters && (
        <Card>
          <CardContent className="p-4">
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {/* User filter */}
              <div className="space-y-2">
                <Label className="text-xs font-medium">User</Label>
                <Select value={filters.user_id || 'all'} onValueChange={(v) => { setFilters((prev) => ({ ...prev, user_id: v === 'all' ? '' : v })); setPage(1); }}>
                  <SelectTrigger className="h-9">
                    <SelectValue placeholder="All users" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All users</SelectItem>
                    {usersList.map((u) => (
                      <SelectItem key={u.id} value={u.id}>
                        {u.name} ({u.email})
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Action type */}
              <div className="space-y-2">
                <Label className="text-xs font-medium">Action Type</Label>
                <Select value={filters.action} onValueChange={(v) => { setFilters((prev) => ({ ...prev, action: v })); setPage(1); }}>
                  <SelectTrigger className="h-9">
                    <SelectValue placeholder="All actions" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All actions</SelectItem>
                    {ACTION_TYPES.map((action) => (
                      <SelectItem key={action} value={action}>
                        {action.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase())}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Resource type */}
              <div className="space-y-2">
                <Label className="text-xs font-medium">Resource Type</Label>
                <Select value={filters.resource_type} onValueChange={(v) => { setFilters((prev) => ({ ...prev, resource_type: v })); setPage(1); }}>
                  <SelectTrigger className="h-9">
                    <SelectValue placeholder="All resources" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All resources</SelectItem>
                    {RESOURCE_TYPES.map((rt) => (
                      <SelectItem key={rt} value={rt}>
                        {rt.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase())}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Status */}
              <div className="space-y-2">
                <Label className="text-xs font-medium">Status</Label>
                <Select value={filters.status} onValueChange={(v) => { setFilters((prev) => ({ ...prev, status: v })); setPage(1); }}>
                  <SelectTrigger className="h-9">
                    <SelectValue placeholder="All statuses" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All statuses</SelectItem>
                    <SelectItem value="success">Success</SelectItem>
                    <SelectItem value="failure">Failure</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {/* Date from */}
              <div className="space-y-2">
                <Label className="text-xs font-medium">Date From</Label>
                <Input
                  type="datetime-local"
                  value={filters.date_from}
                  onChange={(e) => { setFilters((prev) => ({ ...prev, date_from: e.target.value })); setPage(1); }}
                  className="h-9 text-sm"
                />
              </div>

              {/* Date to */}
              <div className="space-y-2">
                <Label className="text-xs font-medium">Date To</Label>
                <Input
                  type="datetime-local"
                  value={filters.date_to}
                  onChange={(e) => { setFilters((prev) => ({ ...prev, date_to: e.target.value })); setPage(1); }}
                  className="h-9 text-sm"
                />
              </div>

              <div className="flex items-end sm:col-span-2">
                <Button variant="ghost" size="sm" onClick={clearFilters}>
                  <X className="mr-1 h-3 w-3" />
                  Clear all filters
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Audit Log Table */}
      <Card>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="flex h-64 items-center justify-center">
              <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
            </div>
          ) : logs.length === 0 ? (
            <div className="flex h-64 flex-col items-center justify-center text-slate-400">
              <ClipboardList className="mb-3 h-10 w-10" />
              <p className="text-sm font-medium">No audit log entries found</p>
              <p className="mt-1 text-xs">
                {hasActiveFilters ? 'Try adjusting your filters' : 'Audit events will appear here as actions are performed'}
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow className="bg-slate-50/50 dark:bg-slate-800/50">
                    <TableHead className="w-8" />
                    <TableHead>
                      <button
                        className="flex items-center gap-1 text-xs font-medium hover:text-foreground"
                        onClick={() => toggleSort('timestamp')}
                      >
                        <Clock className="h-3 w-3" />
                        Timestamp
                        <SortIcon field="timestamp" />
                      </button>
                    </TableHead>
                    <TableHead>
                      <button
                        className="flex items-center gap-1 text-xs font-medium hover:text-foreground"
                        onClick={() => toggleSort('user_name')}
                      >
                        <User className="h-3 w-3" />
                        User
                        <SortIcon field="user_name" />
                      </button>
                    </TableHead>
                    <TableHead>
                      <button
                        className="flex items-center gap-1 text-xs font-medium hover:text-foreground"
                        onClick={() => toggleSort('action')}
                      >
                        <Activity className="h-3 w-3" />
                        Action
                        <SortIcon field="action" />
                      </button>
                    </TableHead>
                    <TableHead>Resource</TableHead>
                    <TableHead>
                      <div className="flex items-center gap-1">
                        <Globe className="h-3 w-3" />
                        IP Address
                      </div>
                    </TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {logs.map((log) => (
                    <>
                      <TableRow
                        key={log.id}
                        className={cn(
                          'cursor-pointer',
                          expandedRow === log.id && 'bg-blue-50/50 dark:bg-blue-900/10'
                        )}
                        onClick={() => setExpandedRow(expandedRow === log.id ? null : log.id)}
                      >
                        <TableCell className="w-8 pr-0">
                          {expandedRow === log.id ? (
                            <ChevronUp className="h-4 w-4 text-slate-400" />
                          ) : (
                            <ChevronDown className="h-4 w-4 text-slate-400" />
                          )}
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-sm text-slate-600 dark:text-slate-400">
                          {formatDate(log.timestamp, 'MMM d, yyyy HH:mm:ss')}
                        </TableCell>
                        <TableCell>
                          <div>
                            <p className="text-sm font-medium text-slate-900 dark:text-white">
                              {log.user_name}
                            </p>
                            <p className="text-xs text-slate-500 dark:text-slate-400">
                              {log.user_email}
                            </p>
                          </div>
                        </TableCell>
                        <TableCell>
                          <Badge className={cn('text-xs', getActionColor(log.action))}>
                            {log.action.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase())}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <div className="text-sm">
                            <span className="font-medium text-slate-700 dark:text-slate-300">
                              {log.resource_type.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase())}
                            </span>
                            {log.resource_id && (
                              <span className="ml-1 font-mono text-xs text-slate-400">
                                #{log.resource_id.substring(0, 8)}
                              </span>
                            )}
                          </div>
                        </TableCell>
                        <TableCell className="font-mono text-xs text-slate-500 dark:text-slate-400">
                          {log.ip_address}
                        </TableCell>
                        <TableCell>
                          {log.status === 'success' ? (
                            <Badge className="bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400 text-xs">
                              Success
                            </Badge>
                          ) : (
                            <Badge className="bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 text-xs">
                              Failure
                            </Badge>
                          )}
                        </TableCell>
                      </TableRow>

                      {/* Expanded Details Row */}
                      {expandedRow === log.id && (
                        <TableRow key={`${log.id}-details`} className="bg-slate-50 dark:bg-slate-800/50">
                          <TableCell colSpan={7} className="px-6 py-4">
                            <div className="space-y-3">
                              <h4 className="text-sm font-semibold text-slate-900 dark:text-white">
                                Log Details
                              </h4>
                              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                                <div className="space-y-1">
                                  <span className="text-xs font-medium text-slate-500">Log ID</span>
                                  <p className="font-mono text-xs text-slate-700 dark:text-slate-300">{log.id}</p>
                                </div>
                                <div className="space-y-1">
                                  <span className="text-xs font-medium text-slate-500">User ID</span>
                                  <p className="font-mono text-xs text-slate-700 dark:text-slate-300">{log.user_id}</p>
                                </div>
                                <div className="space-y-1">
                                  <span className="text-xs font-medium text-slate-500">Full Resource ID</span>
                                  <p className="font-mono text-xs text-slate-700 dark:text-slate-300">{log.resource_id || 'N/A'}</p>
                                </div>
                                <div className="space-y-1">
                                  <span className="text-xs font-medium text-slate-500">IP Address</span>
                                  <p className="font-mono text-xs text-slate-700 dark:text-slate-300">{log.ip_address}</p>
                                </div>
                                <div className="space-y-1">
                                  <span className="text-xs font-medium text-slate-500">Timestamp</span>
                                  <p className="text-xs text-slate-700 dark:text-slate-300">
                                    {formatDate(log.timestamp, 'PPpp')}
                                  </p>
                                </div>
                                <div className="space-y-1">
                                  <span className="text-xs font-medium text-slate-500">User Agent</span>
                                  <p className="truncate text-xs text-slate-700 dark:text-slate-300" title={log.user_agent}>
                                    {log.user_agent || 'N/A'}
                                  </p>
                                </div>
                              </div>

                              {/* Details JSON */}
                              {log.details && Object.keys(log.details).length > 0 && (
                                <div className="space-y-1">
                                  <span className="text-xs font-medium text-slate-500">Details</span>
                                  <pre className="max-h-64 overflow-auto rounded-lg border bg-slate-900 p-3 text-xs text-green-400 dark:bg-slate-950">
                                    {JSON.stringify(log.details, null, 2)}
                                  </pre>
                                </div>
                              )}
                            </div>
                          </TableCell>
                        </TableRow>
                      )}
                    </>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          {/* Pagination */}
          {totalLogs > 0 && (
            <div className="flex flex-col gap-3 border-t border-slate-200 px-4 py-3 dark:border-slate-700 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400">
                <span>Rows per page</span>
                <Select
                  value={String(pageSize)}
                  onValueChange={(val) => {
                    setPageSize(Number(val));
                    setPage(1);
                  }}
                >
                  <SelectTrigger className="h-8 w-[70px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {PAGE_SIZE_OPTIONS.map((size) => (
                      <SelectItem key={size} value={String(size)}>
                        {size}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <span>
                  {(page - 1) * pageSize + 1}-{Math.min(page * pageSize, totalLogs)} of {totalLogs}
                </span>
                {isFetching && <Loader2 className="h-3 w-3 animate-spin" />}
                {autoRefresh && (
                  <Badge variant="outline" className="gap-1 text-xs">
                    <Play className="h-2.5 w-2.5" />
                    Live
                  </Badge>
                )}
              </div>

              <div className="flex items-center gap-1">
                <Button
                  variant="outline"
                  size="icon"
                  className="h-8 w-8"
                  disabled={page <= 1}
                  onClick={() => setPage(1)}
                >
                  <ChevronsLeft className="h-4 w-4" />
                </Button>
                <Button
                  variant="outline"
                  size="icon"
                  className="h-8 w-8"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                >
                  <ChevronLeft className="h-4 w-4" />
                </Button>
                <span className="px-2 text-sm text-slate-500 dark:text-slate-400">
                  Page {page} of {totalPages}
                </span>
                <Button
                  variant="outline"
                  size="icon"
                  className="h-8 w-8"
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                >
                  <ChevronRight className="h-4 w-4" />
                </Button>
                <Button
                  variant="outline"
                  size="icon"
                  className="h-8 w-8"
                  disabled={page >= totalPages}
                  onClick={() => setPage(totalPages)}
                >
                  <ChevronsRight className="h-4 w-4" />
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
