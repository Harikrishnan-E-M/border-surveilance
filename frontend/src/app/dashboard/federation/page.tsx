'use client';

import { useState, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Globe2,
  Server,
  Shield,
  Camera,
  AlertTriangle,
  RefreshCw,
  Plus,
  Search,
  Loader2,
  CheckCircle2,
  XCircle,
  Clock,
  ChevronLeft,
  ChevronRight,
  Wifi,
  WifiOff,
  MapPin,
  BarChart3,
  Link2,
  Trash2,
  X,
  ArrowUpDown,
} from 'lucide-react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { apiClient } from '@/lib/api-client';

// ── Types ─────────────────────────────────────────────────────────────────

interface FederatedSite {
  id: string;
  org_id: string;
  name: string;
  code: string;
  address: string | null;
  city: string | null;
  state: string | null;
  country: string | null;
  latitude: number | null;
  longitude: number | null;
  timezone: string;
  api_url: string;
  is_primary: boolean;
  is_online: boolean;
  last_heartbeat: string | null;
  camera_count: number;
  alert_count_today: number;
  metadata_json: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

interface SyncRecord {
  id: string;
  source_site_id: string;
  target_site_id: string;
  source_site_name: string | null;
  target_site_name: string | null;
  sync_type: string;
  last_synced_at: string | null;
  status: string;
  error_message: string | null;
  records_synced: number;
  created_at: string;
}

interface FederatedAlert {
  id: string;
  org_id: string;
  source_site_id: string;
  source_site_name: string | null;
  source_site_code: string | null;
  original_alert_id: string;
  alert_type: string;
  severity: string;
  camera_name: string;
  description: string | null;
  thumbnail_path: string | null;
  original_created_at: string;
  synced_at: string;
}

interface DashboardData {
  total_sites: number;
  sites_online: number;
  sites_offline: number;
  total_cameras: number;
  total_alerts_today: number;
  total_federated_alerts: number;
  sites: Array<{
    site_id: string;
    site_name: string;
    site_code: string;
    is_online: boolean;
    camera_count: number;
    alert_count_today: number;
    last_heartbeat: string | null;
    timezone: string;
  }>;
}

interface ComparisonMetrics {
  site_id: string;
  site_name: string;
  site_code: string;
  camera_count: number;
  alert_count_today: number;
  total_federated_alerts: number;
  is_online: boolean;
  last_heartbeat: string | null;
}

interface SearchResultItem {
  site_id: string;
  site_name: string;
  site_code: string;
  result_type: string;
  result_id: string;
  title: string;
  description: string | null;
  confidence: number | null;
  thumbnail_url: string | null;
  matched_at: string | null;
  metadata: Record<string, unknown> | null;
}

interface SearchResult {
  query: string;
  search_type: string;
  total_results: number;
  sites_searched: number;
  sites_responded: number;
  results: SearchResultItem[];
}

// ── Helpers ───────────────────────────────────────────────────────────────

function relativeTime(dateStr: string | null): string {
  if (!dateStr) return 'Never';
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  return `${diffDay}d ago`;
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return '--';
  return new Date(dateStr).toLocaleString();
}

const severityColors: Record<string, string> = {
  critical: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  high: 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400',
  medium: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
  low: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
};

const syncStatusColors: Record<string, string> = {
  synced: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  syncing: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
  failed: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
};

// ── Page Component ────────────────────────────────────────────────────────

export default function FederationPage() {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState('overview');

  // Site list state
  const [selectedSite, setSelectedSite] = useState<FederatedSite | null>(null);
  const [showSiteDetail, setShowSiteDetail] = useState(false);

  // Add site dialog
  const [showAddDialog, setShowAddDialog] = useState(false);
  const [addForm, setAddForm] = useState({
    name: '',
    code: '',
    address: '',
    city: '',
    state: '',
    country: '',
    latitude: '',
    longitude: '',
    timezone: 'UTC',
    api_url: '',
    api_key: '',
    is_primary: false,
  });

  // Alerts state
  const [alertPage, setAlertPage] = useState(1);
  const [alertSiteFilter, setAlertSiteFilter] = useState<string>('all');
  const [alertSeverityFilter, setAlertSeverityFilter] = useState<string>('all');

  // Search state
  const [searchQuery, setSearchQuery] = useState('');
  const [searchType, setSearchType] = useState('all');
  const [searchResults, setSearchResults] = useState<SearchResult | null>(null);

  // Sync history state
  const [syncSiteId, setSyncSiteId] = useState<string | null>(null);
  const [syncPage, _setSyncPage] = useState(1);

  // ── Queries ─────────────────────────────────────────────────────────

  const { data: sitesData, isLoading: sitesLoading } = useQuery({
    queryKey: ['federation-sites'],
    queryFn: async () => {
      const res = await apiClient.get('/federation/sites', { params: { page_size: 100 } });
      return res.data as { data: FederatedSite[]; meta: { total: number } };
    },
    refetchInterval: 30000,
  });

  const sites = sitesData?.data || [];

  const { data: dashboardData, isLoading: dashboardLoading } = useQuery({
    queryKey: ['federation-dashboard'],
    queryFn: async () => {
      const res = await apiClient.get('/federation/dashboard');
      return res.data?.data as DashboardData;
    },
    refetchInterval: 30000,
  });

  const buildAlertParams = useCallback(() => {
    const params: Record<string, string | number> = { page: alertPage, page_size: 15 };
    if (alertSiteFilter !== 'all') params.site_id = alertSiteFilter;
    if (alertSeverityFilter !== 'all') params.severity = alertSeverityFilter;
    return params;
  }, [alertPage, alertSiteFilter, alertSeverityFilter]);

  const {
    data: alertsData,
    isLoading: alertsLoading,
  } = useQuery({
    queryKey: ['federation-alerts', alertPage, alertSiteFilter, alertSeverityFilter],
    queryFn: async () => {
      const res = await apiClient.get('/federation/alerts', { params: buildAlertParams() });
      return res.data as {
        data: FederatedAlert[];
        meta: { page: number; page_size: number; total: number; total_pages: number };
      };
    },
    refetchInterval: 15000,
  });

  const federatedAlerts = alertsData?.data || [];
  const alertTotal = alertsData?.meta?.total || 0;
  const alertTotalPages = alertsData?.meta?.total_pages || 0;

  const { data: comparisonData } = useQuery({
    queryKey: ['federation-comparison'],
    queryFn: async () => {
      const res = await apiClient.get('/federation/comparison');
      return (res.data?.data || []) as ComparisonMetrics[];
    },
    refetchInterval: 60000,
  });

  const { data: syncHistoryData, isLoading: syncLoading } = useQuery({
    queryKey: ['federation-sync-history', syncSiteId, syncPage],
    queryFn: async () => {
      if (!syncSiteId) return null;
      const res = await apiClient.get(`/federation/sites/${syncSiteId}/sync-history`, {
        params: { page: syncPage, page_size: 15 },
      });
      return res.data as {
        data: SyncRecord[];
        meta: { page: number; page_size: number; total: number; total_pages: number };
      };
    },
    enabled: !!syncSiteId,
  });

  // ── Mutations ───────────────────────────────────────────────────────

  const addSiteMutation = useMutation({
    mutationFn: async (payload: typeof addForm) => {
      const body = {
        ...payload,
        latitude: payload.latitude ? parseFloat(payload.latitude) : null,
        longitude: payload.longitude ? parseFloat(payload.longitude) : null,
      };
      const res = await apiClient.post('/federation/sites', body);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['federation-sites'] });
      queryClient.invalidateQueries({ queryKey: ['federation-dashboard'] });
      setShowAddDialog(false);
      resetAddForm();
    },
  });

  const deleteSiteMutation = useMutation({
    mutationFn: async (siteId: string) => {
      const res = await apiClient.delete(`/federation/sites/${siteId}`);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['federation-sites'] });
      queryClient.invalidateQueries({ queryKey: ['federation-dashboard'] });
      setShowSiteDetail(false);
      setSelectedSite(null);
    },
  });

  const syncMutation = useMutation({
    mutationFn: async ({ siteId, syncType }: { siteId: string; syncType: string }) => {
      const res = await apiClient.post(`/federation/sites/${siteId}/sync`, {
        sync_type: syncType,
      });
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['federation-sites'] });
      queryClient.invalidateQueries({ queryKey: ['federation-dashboard'] });
      queryClient.invalidateQueries({ queryKey: ['federation-alerts'] });
      queryClient.invalidateQueries({ queryKey: ['federation-sync-history'] });
    },
  });

  const testConnectionMutation = useMutation({
    mutationFn: async (payload: { api_url: string; api_key: string }) => {
      const res = await apiClient.post('/federation/test-connection', payload);
      return res.data?.data as { reachable: boolean; latency_ms: number; error: string | null };
    },
  });

  const searchMutation = useMutation({
    mutationFn: async (payload: { query: string; search_type: string; limit: number }) => {
      const res = await apiClient.post('/federation/search', payload);
      return res.data?.data as SearchResult;
    },
    onSuccess: (data) => {
      setSearchResults(data);
    },
  });

  // ── Handlers ────────────────────────────────────────────────────────

  const resetAddForm = () => {
    setAddForm({
      name: '',
      code: '',
      address: '',
      city: '',
      state: '',
      country: '',
      latitude: '',
      longitude: '',
      timezone: 'UTC',
      api_url: '',
      api_key: '',
      is_primary: false,
    });
    testConnectionMutation.reset();
  };

  const handleAddSite = () => {
    if (!addForm.name || !addForm.code || !addForm.api_url || !addForm.api_key) return;
    addSiteMutation.mutate(addForm);
  };

  const handleTestConnection = () => {
    if (!addForm.api_url || !addForm.api_key) return;
    testConnectionMutation.mutate({
      api_url: addForm.api_url,
      api_key: addForm.api_key,
    });
  };

  const handleSearch = () => {
    if (!searchQuery.trim()) return;
    searchMutation.mutate({
      query: searchQuery.trim(),
      search_type: searchType,
      limit: 20,
    });
  };

  const openSiteDetail = (site: FederatedSite) => {
    setSelectedSite(site);
    setSyncSiteId(site.id);
    setShowSiteDetail(true);
  };

  // ── Chart data ──────────────────────────────────────────────────────

  const comparisonChartData = (comparisonData || []).map((m) => ({
    name: m.site_name.length > 15 ? m.site_name.slice(0, 12) + '...' : m.site_name,
    Cameras: m.camera_count,
    'Alerts Today': m.alert_count_today,
    'Total Alerts': m.total_federated_alerts,
  }));

  // ── Render ──────────────────────────────────────────────────────────

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">
            Multi-Site Federation
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Manage and monitor all federated IBVAP border outposts and checkposts from a single dashboard
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              queryClient.invalidateQueries({ queryKey: ['federation-sites'] });
              queryClient.invalidateQueries({ queryKey: ['federation-dashboard'] });
              queryClient.invalidateQueries({ queryKey: ['federation-comparison'] });
            }}
          >
            <RefreshCw className="mr-1.5 h-4 w-4" />
            Refresh
          </Button>
          <Button size="sm" onClick={() => { resetAddForm(); setShowAddDialog(true); }}>
            <Plus className="mr-1.5 h-4 w-4" />
            Add Site
          </Button>
        </div>
      </div>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="overview">
            <Globe2 className="mr-1 h-4 w-4" />
            Overview
          </TabsTrigger>
          <TabsTrigger value="alerts">
            <AlertTriangle className="mr-1 h-4 w-4" />
            Alerts
          </TabsTrigger>
          <TabsTrigger value="comparison">
            <BarChart3 className="mr-1 h-4 w-4" />
            Comparison
          </TabsTrigger>
          <TabsTrigger value="search">
            <Search className="mr-1 h-4 w-4" />
            Search
          </TabsTrigger>
        </TabsList>

        {/* ── Overview Tab ──────────────────────────────────────────── */}
        <TabsContent value="overview" className="space-y-4">
          {/* Dashboard Stats */}
          {dashboardLoading ? (
            <div className="flex h-32 items-center justify-center">
              <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
            </div>
          ) : dashboardData ? (
            <>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
                <Card>
                  <CardContent className="flex items-center gap-3 p-4">
                    <div className="rounded-lg bg-blue-100 p-2.5 dark:bg-blue-900/30">
                      <Globe2 className="h-5 w-5 text-blue-600 dark:text-blue-400" />
                    </div>
                    <div>
                      <p className="text-2xl font-bold text-slate-900 dark:text-white">
                        {dashboardData.total_sites}
                      </p>
                      <p className="text-xs text-slate-500 dark:text-slate-400">Total Sites</p>
                    </div>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="flex items-center gap-3 p-4">
                    <div className="rounded-lg bg-green-100 p-2.5 dark:bg-green-900/30">
                      <Wifi className="h-5 w-5 text-green-600 dark:text-green-400" />
                    </div>
                    <div>
                      <p className="text-2xl font-bold text-green-600">{dashboardData.sites_online}</p>
                      <p className="text-xs text-slate-500 dark:text-slate-400">Sites Online</p>
                    </div>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="flex items-center gap-3 p-4">
                    <div className="rounded-lg bg-red-100 p-2.5 dark:bg-red-900/30">
                      <WifiOff className="h-5 w-5 text-red-600 dark:text-red-400" />
                    </div>
                    <div>
                      <p className="text-2xl font-bold text-red-600">{dashboardData.sites_offline}</p>
                      <p className="text-xs text-slate-500 dark:text-slate-400">Sites Offline</p>
                    </div>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="flex items-center gap-3 p-4">
                    <div className="rounded-lg bg-purple-100 p-2.5 dark:bg-purple-900/30">
                      <Camera className="h-5 w-5 text-purple-600 dark:text-purple-400" />
                    </div>
                    <div>
                      <p className="text-2xl font-bold text-slate-900 dark:text-white">
                        {dashboardData.total_cameras}
                      </p>
                      <p className="text-xs text-slate-500 dark:text-slate-400">Total Cameras</p>
                    </div>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="flex items-center gap-3 p-4">
                    <div className="rounded-lg bg-amber-100 p-2.5 dark:bg-amber-900/30">
                      <AlertTriangle className="h-5 w-5 text-amber-600 dark:text-amber-400" />
                    </div>
                    <div>
                      <p className="text-2xl font-bold text-slate-900 dark:text-white">
                        {dashboardData.total_alerts_today}
                      </p>
                      <p className="text-xs text-slate-500 dark:text-slate-400">Alerts Today</p>
                    </div>
                  </CardContent>
                </Card>
              </div>

              {/* Site Map / Grid */}
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Server className="h-4 w-4" />
                    Federated Sites
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {sitesLoading ? (
                    <div className="flex h-48 items-center justify-center">
                      <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
                    </div>
                  ) : sites.length === 0 ? (
                    <div className="flex h-48 flex-col items-center justify-center text-slate-400">
                      <Globe2 className="mb-3 h-10 w-10" />
                      <p className="text-sm font-medium">No sites registered</p>
                      <p className="mt-1 text-xs">Add your first site to start federating</p>
                      <Button
                        variant="link"
                        className="mt-2"
                        onClick={() => { resetAddForm(); setShowAddDialog(true); }}
                      >
                        Add a site
                      </Button>
                    </div>
                  ) : (
                    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                      {sites.map((site) => (
                        <div
                          key={site.id}
                          onClick={() => openSiteDetail(site)}
                          className="cursor-pointer rounded-lg border border-slate-200 p-3 transition-all hover:border-blue-300 hover:shadow-md dark:border-slate-700 dark:hover:border-blue-600"
                        >
                          <div className="mb-2 flex items-start justify-between">
                            <div className="flex items-center gap-2">
                              <div
                                className={`h-2.5 w-2.5 rounded-full ${
                                  site.is_online
                                    ? 'bg-green-500 shadow-sm shadow-green-300'
                                    : 'bg-red-500 shadow-sm shadow-red-300'
                                }`}
                              />
                              <span className="text-sm font-semibold text-slate-800 dark:text-slate-200">
                                {site.name}
                              </span>
                            </div>
                            {site.is_primary && (
                              <Badge className="bg-blue-100 text-[10px] text-blue-700 dark:bg-blue-900/30 dark:text-blue-400">
                                Primary
                              </Badge>
                            )}
                          </div>
                          <div className="space-y-1.5 text-xs text-slate-500 dark:text-slate-400">
                            {(site.city || site.country) && (
                              <div className="flex items-center gap-1">
                                <MapPin className="h-3 w-3" />
                                {[site.city, site.state, site.country].filter(Boolean).join(', ')}
                              </div>
                            )}
                            <div className="flex items-center justify-between">
                              <span className="flex items-center gap-1">
                                <Camera className="h-3 w-3" />
                                {site.camera_count} cameras
                              </span>
                              <span className="flex items-center gap-1">
                                <AlertTriangle className="h-3 w-3" />
                                {site.alert_count_today} alerts
                              </span>
                            </div>
                            <div className="flex items-center gap-1">
                              <Clock className="h-3 w-3" />
                              Heartbeat: {relativeTime(site.last_heartbeat)}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* Per-site bar chart breakdown */}
              {dashboardData.sites.length > 0 && (
                <Card>
                  <CardHeader className="pb-3">
                    <CardTitle className="flex items-center gap-2 text-base">
                      <BarChart3 className="h-4 w-4" />
                      Per-Site Breakdown
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <ResponsiveContainer width="100%" height={300}>
                      <BarChart
                        data={dashboardData.sites.map((s) => ({
                          name: s.site_name.length > 12 ? s.site_name.slice(0, 10) + '...' : s.site_name,
                          Cameras: s.camera_count,
                          'Alerts Today': s.alert_count_today,
                        }))}
                        margin={{ top: 5, right: 20, left: 0, bottom: 5 }}
                      >
                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                        <XAxis dataKey="name" tick={{ fontSize: 12 }} />
                        <YAxis tick={{ fontSize: 12 }} />
                        <Tooltip />
                        <Legend />
                        <Bar dataKey="Cameras" fill="#8b5cf6" radius={[4, 4, 0, 0]} />
                        <Bar dataKey="Alerts Today" fill="#f59e0b" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </CardContent>
                </Card>
              )}
            </>
          ) : null}
        </TabsContent>

        {/* ── Alerts Tab ────────────────────────────────────────────── */}
        <TabsContent value="alerts" className="space-y-4">
          {/* Filters */}
          <Card>
            <CardContent className="flex flex-wrap items-end gap-3 p-4">
              <div className="space-y-1">
                <Label className="text-xs">Site</Label>
                <Select value={alertSiteFilter} onValueChange={(v) => { setAlertSiteFilter(v); setAlertPage(1); }}>
                  <SelectTrigger className="h-9 w-[180px] text-sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Sites</SelectItem>
                    {sites.map((s) => (
                      <SelectItem key={s.id} value={s.id}>{s.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Severity</Label>
                <Select value={alertSeverityFilter} onValueChange={(v) => { setAlertSeverityFilter(v); setAlertPage(1); }}>
                  <SelectTrigger className="h-9 w-[140px] text-sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Severities</SelectItem>
                    <SelectItem value="critical">Critical</SelectItem>
                    <SelectItem value="high">High</SelectItem>
                    <SelectItem value="medium">Medium</SelectItem>
                    <SelectItem value="low">Low</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {(alertSiteFilter !== 'all' || alertSeverityFilter !== 'all') && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-9"
                  onClick={() => { setAlertSiteFilter('all'); setAlertSeverityFilter('all'); setAlertPage(1); }}
                >
                  <X className="mr-1 h-3.5 w-3.5" />
                  Clear
                </Button>
              )}
            </CardContent>
          </Card>

          {/* Alert Table */}
          <Card>
            <CardContent className="p-0">
              {alertsLoading ? (
                <div className="flex h-64 items-center justify-center">
                  <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
                </div>
              ) : federatedAlerts.length === 0 ? (
                <div className="flex h-64 flex-col items-center justify-center text-slate-400">
                  <Shield className="mb-3 h-10 w-10" />
                  <p className="text-sm font-medium">No federated alerts</p>
                  <p className="mt-1 text-xs">Sync alerts from remote sites to see them here</p>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-200 bg-slate-50/50 dark:border-slate-700 dark:bg-slate-800/50">
                        <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">Site</th>
                        <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">Type</th>
                        <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">Severity</th>
                        <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">Camera</th>
                        <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">Description</th>
                        <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">Time</th>
                      </tr>
                    </thead>
                    <tbody>
                      {federatedAlerts.map((alert) => (
                        <tr
                          key={alert.id}
                          className="border-b border-slate-100 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50"
                        >
                          <td className="px-4 py-3">
                            <Badge className="bg-slate-100 text-xs text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                              {alert.source_site_name || alert.source_site_code || 'Unknown'}
                            </Badge>
                          </td>
                          <td className="px-4 py-3 font-medium text-slate-700 dark:text-slate-300">
                            {alert.alert_type}
                          </td>
                          <td className="px-4 py-3">
                            <Badge className={`text-xs ${severityColors[alert.severity] || severityColors.medium}`}>
                              {alert.severity}
                            </Badge>
                          </td>
                          <td className="px-4 py-3 text-slate-600 dark:text-slate-400">
                            {alert.camera_name}
                          </td>
                          <td className="max-w-[250px] truncate px-4 py-3 text-slate-600 dark:text-slate-400">
                            {alert.description || '--'}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-slate-600 dark:text-slate-400">
                            {relativeTime(alert.original_created_at)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Pagination */}
              {alertTotalPages > 1 && (
                <div className="flex items-center justify-between border-t border-slate-200 px-4 py-3 dark:border-slate-700">
                  <span className="text-sm text-slate-500 dark:text-slate-400">
                    Page {alertPage} of {alertTotalPages} ({alertTotal} alerts)
                  </span>
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setAlertPage((p) => Math.max(1, p - 1))}
                      disabled={alertPage === 1}
                    >
                      <ChevronLeft className="mr-1 h-4 w-4" />
                      Previous
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setAlertPage((p) => Math.min(alertTotalPages, p + 1))}
                      disabled={alertPage === alertTotalPages}
                    >
                      Next
                      <ChevronRight className="ml-1 h-4 w-4" />
                    </Button>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── Comparison Tab ────────────────────────────────────────── */}
        <TabsContent value="comparison" className="space-y-4">
          {comparisonData && comparisonData.length > 0 ? (
            <>
              {/* Comparison Chart */}
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <ArrowUpDown className="h-4 w-4" />
                    Site Metric Comparison
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <ResponsiveContainer width="100%" height={350}>
                    <BarChart data={comparisonChartData} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                      <XAxis dataKey="name" tick={{ fontSize: 12 }} />
                      <YAxis tick={{ fontSize: 12 }} />
                      <Tooltip />
                      <Legend />
                      <Bar dataKey="Cameras" fill="#8b5cf6" radius={[4, 4, 0, 0]} />
                      <Bar dataKey="Alerts Today" fill="#f59e0b" radius={[4, 4, 0, 0]} />
                      <Bar dataKey="Total Alerts" fill="#ef4444" radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </CardContent>
              </Card>

              {/* Comparison Table */}
              <Card>
                <CardContent className="p-0">
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-slate-200 bg-slate-50/50 dark:border-slate-700 dark:bg-slate-800/50">
                          <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">Site</th>
                          <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">Status</th>
                          <th className="px-4 py-3 text-right font-medium text-slate-600 dark:text-slate-300">Cameras</th>
                          <th className="px-4 py-3 text-right font-medium text-slate-600 dark:text-slate-300">Alerts Today</th>
                          <th className="px-4 py-3 text-right font-medium text-slate-600 dark:text-slate-300">Total Synced Alerts</th>
                          <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">Last Heartbeat</th>
                        </tr>
                      </thead>
                      <tbody>
                        {comparisonData.map((m) => (
                          <tr
                            key={m.site_id}
                            className="border-b border-slate-100 dark:border-slate-800"
                          >
                            <td className="px-4 py-3 font-medium text-slate-700 dark:text-slate-300">
                              {m.site_name}
                              <span className="ml-2 text-xs text-slate-400">({m.site_code})</span>
                            </td>
                            <td className="px-4 py-3">
                              {m.is_online ? (
                                <Badge className="bg-green-100 text-xs text-green-700 dark:bg-green-900/30 dark:text-green-400">
                                  <Wifi className="mr-1 h-3 w-3" />
                                  Online
                                </Badge>
                              ) : (
                                <Badge className="bg-red-100 text-xs text-red-700 dark:bg-red-900/30 dark:text-red-400">
                                  <WifiOff className="mr-1 h-3 w-3" />
                                  Offline
                                </Badge>
                              )}
                            </td>
                            <td className="px-4 py-3 text-right font-semibold text-slate-700 dark:text-slate-300">
                              {m.camera_count}
                            </td>
                            <td className="px-4 py-3 text-right font-semibold text-slate-700 dark:text-slate-300">
                              {m.alert_count_today}
                            </td>
                            <td className="px-4 py-3 text-right font-semibold text-slate-700 dark:text-slate-300">
                              {m.total_federated_alerts}
                            </td>
                            <td className="whitespace-nowrap px-4 py-3 text-slate-600 dark:text-slate-400">
                              {relativeTime(m.last_heartbeat)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </CardContent>
              </Card>
            </>
          ) : (
            <Card>
              <CardContent className="flex h-64 flex-col items-center justify-center text-slate-400">
                <BarChart3 className="mb-3 h-10 w-10" />
                <p className="text-sm font-medium">No comparison data</p>
                <p className="mt-1 text-xs">Register sites to compare metrics</p>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* ── Search Tab ────────────────────────────────────────────── */}
        <TabsContent value="search" className="space-y-4">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-base">
                <Search className="h-4 w-4" />
                Cross-Site Search
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex flex-wrap items-end gap-3">
                <div className="flex-1 space-y-1">
                  <Label className="text-xs">Search Query</Label>
                  <Input
                    placeholder="Enter face name, vehicle plate, or keyword..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') handleSearch(); }}
                    className="text-sm"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Type</Label>
                  <Select value={searchType} onValueChange={setSearchType}>
                    <SelectTrigger className="h-9 w-[140px] text-sm">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">All Types</SelectItem>
                      <SelectItem value="faces">Faces</SelectItem>
                      <SelectItem value="vehicles">Vehicles</SelectItem>
                      <SelectItem value="alerts">Alerts</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <Button
                  size="sm"
                  className="h-9"
                  onClick={handleSearch}
                  disabled={searchMutation.isPending || !searchQuery.trim()}
                >
                  {searchMutation.isPending ? (
                    <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                  ) : (
                    <Search className="mr-1.5 h-4 w-4" />
                  )}
                  Search
                </Button>
              </div>

              {/* Search Results */}
              {searchResults && (
                <div className="space-y-3">
                  <div className="flex items-center justify-between text-sm text-slate-500 dark:text-slate-400">
                    <span>
                      {searchResults.total_results} result{searchResults.total_results !== 1 ? 's' : ''}{' '}
                      across {searchResults.sites_responded}/{searchResults.sites_searched} sites
                    </span>
                    <span>Query: &quot;{searchResults.query}&quot;</span>
                  </div>

                  {searchResults.results.length === 0 ? (
                    <div className="rounded-lg border border-slate-200 p-8 text-center text-slate-400 dark:border-slate-700">
                      <Search className="mx-auto mb-2 h-8 w-8" />
                      <p className="text-sm">No results found across federated sites</p>
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {searchResults.results.map((item, idx) => (
                        <div
                          key={`${item.site_id}-${item.result_id}-${idx}`}
                          className="rounded-lg border border-slate-200 p-3 dark:border-slate-700"
                        >
                          <div className="flex items-start justify-between">
                            <div>
                              <div className="flex items-center gap-2">
                                <Badge className="bg-slate-100 text-[10px] text-slate-600 dark:bg-slate-800 dark:text-slate-400">
                                  {item.site_name}
                                </Badge>
                                <Badge
                                  className={`text-[10px] ${
                                    item.result_type === 'face'
                                      ? 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400'
                                      : item.result_type === 'vehicle'
                                        ? 'bg-cyan-100 text-cyan-700 dark:bg-cyan-900/30 dark:text-cyan-400'
                                        : 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400'
                                  }`}
                                >
                                  {item.result_type}
                                </Badge>
                              </div>
                              <p className="mt-1 text-sm font-medium text-slate-800 dark:text-slate-200">
                                {item.title}
                              </p>
                              {item.description && (
                                <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                                  {item.description.slice(0, 150)}
                                </p>
                              )}
                            </div>
                            {item.confidence !== null && item.confidence !== undefined && (
                              <span className="text-xs font-semibold text-slate-600 dark:text-slate-400">
                                {(item.confidence * 100).toFixed(1)}%
                              </span>
                            )}
                          </div>
                          {item.matched_at && (
                            <p className="mt-1 text-[10px] text-slate-400">
                              Matched: {formatDate(item.matched_at)}
                            </p>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* ── Add Site Dialog ──────────────────────────────────────────── */}
      <Dialog open={showAddDialog} onOpenChange={setShowAddDialog}>
        <DialogContent className="max-h-[85vh] max-w-lg overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Plus className="h-5 w-5" />
              Add Federated Site
            </DialogTitle>
            <DialogDescription>
              Register a remote IBVAP border outpost instance to federate with this central command hub
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            {addSiteMutation.isError && (
              <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">
                Failed to register site. Check the site code is unique and try again.
              </div>
            )}

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label className="text-xs font-medium">Site Name *</Label>
                <Input
                  value={addForm.name}
                  onChange={(e) => setAddForm((f) => ({ ...f, name: e.target.value }))}
                  placeholder="Downtown HQ"
                  className="text-sm"
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs font-medium">Site Code *</Label>
                <Input
                  value={addForm.code}
                  onChange={(e) => setAddForm((f) => ({ ...f, code: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '') }))}
                  placeholder="downtown-hq"
                  className="text-sm"
                />
              </div>
            </div>

            <div className="space-y-1">
              <Label className="text-xs font-medium">API URL *</Label>
              <Input
                value={addForm.api_url}
                onChange={(e) => setAddForm((f) => ({ ...f, api_url: e.target.value }))}
                placeholder="https://site2.example.com/api/v1"
                className="text-sm"
              />
            </div>

            <div className="space-y-1">
              <Label className="text-xs font-medium">API Key *</Label>
              <Input
                type="password"
                value={addForm.api_key}
                onChange={(e) => setAddForm((f) => ({ ...f, api_key: e.target.value }))}
                placeholder="Remote site API key"
                className="text-sm"
              />
            </div>

            {/* Test connection */}
            <div className="flex items-center gap-3">
              <Button
                variant="outline"
                size="sm"
                onClick={handleTestConnection}
                disabled={testConnectionMutation.isPending || !addForm.api_url || !addForm.api_key}
              >
                {testConnectionMutation.isPending ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Link2 className="mr-1.5 h-3.5 w-3.5" />
                )}
                Test Connection
              </Button>
              {testConnectionMutation.isSuccess && testConnectionMutation.data && (
                <span className={`text-xs font-medium ${testConnectionMutation.data.reachable ? 'text-green-600' : 'text-red-600'}`}>
                  {testConnectionMutation.data.reachable ? (
                    <>
                      <CheckCircle2 className="mr-1 inline h-3.5 w-3.5" />
                      Connected ({testConnectionMutation.data.latency_ms}ms)
                    </>
                  ) : (
                    <>
                      <XCircle className="mr-1 inline h-3.5 w-3.5" />
                      {testConnectionMutation.data.error || 'Connection failed'}
                    </>
                  )}
                </span>
              )}
            </div>

            <div className="space-y-1">
              <Label className="text-xs font-medium">Address</Label>
              <Input
                value={addForm.address}
                onChange={(e) => setAddForm((f) => ({ ...f, address: e.target.value }))}
                placeholder="123 Main Street"
                className="text-sm"
              />
            </div>

            <div className="grid grid-cols-3 gap-3">
              <div className="space-y-1">
                <Label className="text-xs font-medium">City</Label>
                <Input
                  value={addForm.city}
                  onChange={(e) => setAddForm((f) => ({ ...f, city: e.target.value }))}
                  placeholder="City"
                  className="text-sm"
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs font-medium">State</Label>
                <Input
                  value={addForm.state}
                  onChange={(e) => setAddForm((f) => ({ ...f, state: e.target.value }))}
                  placeholder="State"
                  className="text-sm"
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs font-medium">Country</Label>
                <Input
                  value={addForm.country}
                  onChange={(e) => setAddForm((f) => ({ ...f, country: e.target.value }))}
                  placeholder="Country"
                  className="text-sm"
                />
              </div>
            </div>

            <div className="grid grid-cols-3 gap-3">
              <div className="space-y-1">
                <Label className="text-xs font-medium">Latitude</Label>
                <Input
                  value={addForm.latitude}
                  onChange={(e) => setAddForm((f) => ({ ...f, latitude: e.target.value }))}
                  placeholder="40.7128"
                  className="text-sm"
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs font-medium">Longitude</Label>
                <Input
                  value={addForm.longitude}
                  onChange={(e) => setAddForm((f) => ({ ...f, longitude: e.target.value }))}
                  placeholder="-74.0060"
                  className="text-sm"
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs font-medium">Timezone</Label>
                <Input
                  value={addForm.timezone}
                  onChange={(e) => setAddForm((f) => ({ ...f, timezone: e.target.value }))}
                  placeholder="UTC"
                  className="text-sm"
                />
              </div>
            </div>

            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="is_primary"
                checked={addForm.is_primary}
                onChange={(e) => setAddForm((f) => ({ ...f, is_primary: e.target.checked }))}
                className="rounded border-slate-300"
              />
              <Label htmlFor="is_primary" className="text-xs">
                Mark as primary (hub) site
              </Label>
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => { setShowAddDialog(false); resetAddForm(); }}>
              Cancel
            </Button>
            <Button
              onClick={handleAddSite}
              disabled={addSiteMutation.isPending || !addForm.name || !addForm.code || !addForm.api_url || !addForm.api_key}
            >
              {addSiteMutation.isPending ? (
                <>
                  <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                  Registering...
                </>
              ) : (
                <>
                  <Plus className="mr-1.5 h-4 w-4" />
                  Register Site
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Site Detail Dialog ───────────────────────────────────────── */}
      <Dialog open={showSiteDetail} onOpenChange={setShowSiteDetail}>
        <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Server className="h-5 w-5" />
              Site Details
            </DialogTitle>
          </DialogHeader>

          {selectedSite && (
            <div className="space-y-4">
              {/* Status and name */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div
                    className={`h-3 w-3 rounded-full ${
                      selectedSite.is_online
                        ? 'bg-green-500 shadow-sm shadow-green-300'
                        : 'bg-red-500 shadow-sm shadow-red-300'
                    }`}
                  />
                  <h3 className="text-lg font-semibold text-slate-800 dark:text-slate-200">
                    {selectedSite.name}
                  </h3>
                  {selectedSite.is_primary && (
                    <Badge className="bg-blue-100 text-xs text-blue-700 dark:bg-blue-900/30 dark:text-blue-400">
                      Primary
                    </Badge>
                  )}
                </div>
                <Badge className={selectedSite.is_online
                  ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                  : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
                }>
                  {selectedSite.is_online ? 'Online' : 'Offline'}
                </Badge>
              </div>

              {/* Info grid */}
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <span className="text-slate-500 dark:text-slate-400">Code:</span>
                  <span className="ml-2 font-mono font-medium text-slate-700 dark:text-slate-300">{selectedSite.code}</span>
                </div>
                <div>
                  <span className="text-slate-500 dark:text-slate-400">Timezone:</span>
                  <span className="ml-2 font-medium text-slate-700 dark:text-slate-300">{selectedSite.timezone}</span>
                </div>
                <div>
                  <span className="text-slate-500 dark:text-slate-400">Cameras:</span>
                  <span className="ml-2 font-semibold text-slate-700 dark:text-slate-300">{selectedSite.camera_count}</span>
                </div>
                <div>
                  <span className="text-slate-500 dark:text-slate-400">Alerts Today:</span>
                  <span className="ml-2 font-semibold text-slate-700 dark:text-slate-300">{selectedSite.alert_count_today}</span>
                </div>
                <div>
                  <span className="text-slate-500 dark:text-slate-400">API URL:</span>
                  <span className="ml-2 break-all text-xs text-slate-600 dark:text-slate-400">{selectedSite.api_url}</span>
                </div>
                <div>
                  <span className="text-slate-500 dark:text-slate-400">Last Heartbeat:</span>
                  <span className="ml-2 font-medium text-slate-700 dark:text-slate-300">{relativeTime(selectedSite.last_heartbeat)}</span>
                </div>
                {(selectedSite.city || selectedSite.country) && (
                  <div className="col-span-2">
                    <span className="text-slate-500 dark:text-slate-400">Location:</span>
                    <span className="ml-2 text-slate-700 dark:text-slate-300">
                      {[selectedSite.address, selectedSite.city, selectedSite.state, selectedSite.country].filter(Boolean).join(', ')}
                    </span>
                  </div>
                )}
              </div>

              {/* Sync actions */}
              <div>
                <h4 className="mb-2 text-sm font-semibold text-slate-700 dark:text-slate-300">
                  Manual Sync
                </h4>
                <div className="flex flex-wrap gap-2">
                  {(['alerts', 'cameras', 'faces', 'vehicles'] as const).map((type) => (
                    <Button
                      key={type}
                      variant="outline"
                      size="sm"
                      onClick={() => syncMutation.mutate({ siteId: selectedSite.id, syncType: type })}
                      disabled={syncMutation.isPending}
                    >
                      {syncMutation.isPending ? (
                        <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
                      )}
                      Sync {type.charAt(0).toUpperCase() + type.slice(1)}
                    </Button>
                  ))}
                </div>
              </div>

              {/* Sync history */}
              <div>
                <h4 className="mb-2 text-sm font-semibold text-slate-700 dark:text-slate-300">
                  Sync History
                </h4>
                {syncLoading ? (
                  <div className="flex h-24 items-center justify-center">
                    <Loader2 className="h-5 w-5 animate-spin text-blue-600" />
                  </div>
                ) : syncHistoryData && syncHistoryData.data.length > 0 ? (
                  <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="border-b border-slate-200 bg-slate-50/50 dark:border-slate-700 dark:bg-slate-800/50">
                          <th className="px-3 py-2 text-left font-medium">Type</th>
                          <th className="px-3 py-2 text-left font-medium">Status</th>
                          <th className="px-3 py-2 text-right font-medium">Records</th>
                          <th className="px-3 py-2 text-left font-medium">Last Synced</th>
                          <th className="px-3 py-2 text-left font-medium">Error</th>
                        </tr>
                      </thead>
                      <tbody>
                        {syncHistoryData.data.map((record) => (
                          <tr key={record.id} className="border-b border-slate-100 dark:border-slate-800">
                            <td className="px-3 py-2 font-medium capitalize">{record.sync_type}</td>
                            <td className="px-3 py-2">
                              <Badge className={`text-[10px] ${syncStatusColors[record.status] || syncStatusColors.syncing}`}>
                                {record.status === 'synced' && <CheckCircle2 className="mr-0.5 h-2.5 w-2.5" />}
                                {record.status === 'syncing' && <Loader2 className="mr-0.5 h-2.5 w-2.5 animate-spin" />}
                                {record.status === 'failed' && <XCircle className="mr-0.5 h-2.5 w-2.5" />}
                                {record.status}
                              </Badge>
                            </td>
                            <td className="px-3 py-2 text-right">{record.records_synced}</td>
                            <td className="whitespace-nowrap px-3 py-2">{relativeTime(record.last_synced_at)}</td>
                            <td className="max-w-[150px] truncate px-3 py-2 text-red-500">
                              {record.error_message || '--'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="text-xs text-slate-400">No sync history yet</p>
                )}
              </div>

              {/* Actions */}
              <div className="flex justify-between border-t border-slate-200 pt-3 dark:border-slate-700">
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => {
                    if (confirm(`Remove site "${selectedSite.name}" from the federation? This will delete all synced data.`)) {
                      deleteSiteMutation.mutate(selectedSite.id);
                    }
                  }}
                  disabled={deleteSiteMutation.isPending}
                >
                  {deleteSiteMutation.isPending ? (
                    <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                  ) : (
                    <Trash2 className="mr-1.5 h-4 w-4" />
                  )}
                  Remove Site
                </Button>
                <Button variant="outline" onClick={() => setShowSiteDetail(false)}>
                  Close
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
