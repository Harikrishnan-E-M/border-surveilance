'use client';

import { useState, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  FileText,
  ShieldAlert,
  CalendarDays,
  CalendarRange,
  Settings2,
  Loader2,
  Download,
  Plus,
  Clock,
  Eye,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Filter,
  ChevronLeft,
  ChevronRight,
  LayoutTemplate,
  Sparkles,
  FileWarning,
  X,
  Pencil,
  Save,
  Zap,
  TrendingUp,
  BarChart3,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { apiClient } from '@/lib/api-client';
import { formatBytes, formatDate } from '@/lib/utils';

// ── Types ─────────────────────────────────────────────────────────────────

interface IncidentReport {
  id: string;
  org_id: string;
  title: string;
  report_type: string;
  alert_id: string | null;
  status: 'generating' | 'completed' | 'failed';
  file_path: string | null;
  file_size: number | null;
  page_count: number | null;
  generated_by: string | null;
  generated_by_name: string | null;
  summary_text: string | null;
  metadata_json: Record<string, unknown> | null;
  error_message: string | null;
  download_url: string | null;
  template_id: string | null;
  created_at: string;
  updated_at: string;
}

interface ReportTemplate {
  id: string;
  org_id: string;
  name: string;
  template_type: string;
  header_html: string | null;
  footer_html: string | null;
  css_styles: string | null;
  logo_path: string | null;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

interface AlertOption {
  id: string;
  title: string;
  severity: string;
  created_at: string;
}

// ── Constants ─────────────────────────────────────────────────────────────

const reportTypeConfig: Record<
  string,
  { label: string; color: string; icon: React.ElementType }
> = {
  incident: {
    label: 'Incident',
    color: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
    icon: ShieldAlert,
  },
  daily_summary: {
    label: 'Daily Summary',
    color: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
    icon: CalendarDays,
  },
  weekly_report: {
    label: 'Weekly Report',
    color: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400',
    icon: CalendarRange,
  },
  monthly_report: {
    label: 'Monthly Report',
    color: 'bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-400',
    icon: BarChart3,
  },
  custom: {
    label: 'Custom',
    color: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-400',
    icon: Settings2,
  },
};

const statusConfig: Record<
  string,
  { label: string; color: string; icon: React.ElementType }
> = {
  generating: {
    label: 'Generating',
    color: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
    icon: Loader2,
  },
  completed: {
    label: 'Completed',
    color: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
    icon: CheckCircle2,
  },
  failed: {
    label: 'Failed',
    color: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
    icon: XCircle,
  },
};

// ── Page Component ────────────────────────────────────────────────────────

export default function IncidentReportsPage() {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState('reports');

  // Report list state
  const [page, setPage] = useState(1);
  const pageSize = 15;
  const [filterType, setFilterType] = useState<string>('all');
  const [filterStatus, setFilterStatus] = useState<string>('all');
  const [filterStartDate, setFilterStartDate] = useState('');
  const [filterEndDate, setFilterEndDate] = useState('');

  // Generate dialog state
  const [showGenerateDialog, setShowGenerateDialog] = useState(false);
  const [generateType, setGenerateType] = useState<string>('incident');
  const [generateAlertId, setGenerateAlertId] = useState('');
  const [generateDateFrom, setGenerateDateFrom] = useState('');
  const [generateDateTo, setGenerateDateTo] = useState('');
  const [generateTitle, setGenerateTitle] = useState('');

  // Preview dialog state
  const [showPreviewDialog, setShowPreviewDialog] = useState(false);
  const [previewReport, setPreviewReport] = useState<IncidentReport | null>(null);

  // Template management state
  const [showTemplateDialog, setShowTemplateDialog] = useState(false);
  const [editingTemplate, setEditingTemplate] = useState<ReportTemplate | null>(null);
  const [templateForm, setTemplateForm] = useState({
    name: '',
    template_type: 'incident',
    header_html: '',
    footer_html: '',
    css_styles: '',
    is_default: false,
  });

  // ── Queries ─────────────────────────────────────────────────────────

  const buildFilterParams = useCallback(() => {
    const params: Record<string, string | number> = {
      page,
      page_size: pageSize,
    };
    if (filterType && filterType !== 'all') params.type = filterType;
    if (filterStatus && filterStatus !== 'all') params.status = filterStatus;
    if (filterStartDate) params.start_date = new Date(filterStartDate).toISOString();
    if (filterEndDate) params.end_date = new Date(filterEndDate).toISOString();
    return params;
  }, [page, filterType, filterStatus, filterStartDate, filterEndDate]);

  const {
    data: reportsData,
    isLoading: reportsLoading,
    refetch: refetchReports,
  } = useQuery({
    queryKey: ['incident-reports', page, filterType, filterStatus, filterStartDate, filterEndDate],
    queryFn: async () => {
      const res = await apiClient.get('/incident-reports', {
        params: buildFilterParams(),
      });
      return res.data as {
        data: IncidentReport[];
        meta: { page: number; page_size: number; total: number; total_pages: number };
      };
    },
    refetchInterval: 10000, // Poll for status updates
  });

  const reports = reportsData?.data || [];
  const totalReports = reportsData?.meta?.total || 0;
  const totalPages = reportsData?.meta?.total_pages || 0;

  const { data: recentAlerts } = useQuery<AlertOption[]>({
    queryKey: ['alerts', 'recent-for-reports'],
    queryFn: async () => {
      const res = await apiClient.get('/alerts/recent', { params: { limit: 50 } });
      const alertsData = res.data?.data || res.data || [];
      return alertsData.map((a: Record<string, string>) => ({
        id: a.id,
        title: a.title,
        severity: a.severity,
        created_at: a.created_at,
      }));
    },
  });

  const { data: templates, isLoading: templatesLoading } = useQuery<ReportTemplate[]>({
    queryKey: ['incident-report-templates'],
    queryFn: async () => {
      const res = await apiClient.get('/incident-reports/templates/list');
      return res.data?.data || [];
    },
  });

  // ── Mutations ───────────────────────────────────────────────────────

  const generateFromAlert = useMutation({
    mutationFn: async (payload: {
      alert_id: string;
      template_id?: string;
      include_evidence?: boolean;
      title?: string;
    }) => {
      const res = await apiClient.post('/incident-reports/generate', payload);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['incident-reports'] });
      setShowGenerateDialog(false);
      resetGenerateForm();
    },
  });

  const generateDailySummary = useMutation({
    mutationFn: async (payload: { report_date: string; template_id?: string }) => {
      const res = await apiClient.post('/incident-reports/daily-summary', payload);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['incident-reports'] });
      setShowGenerateDialog(false);
      resetGenerateForm();
    },
  });

  const generateWeeklyReport = useMutation({
    mutationFn: async (payload: { week_start: string; template_id?: string }) => {
      const res = await apiClient.post('/incident-reports/weekly', payload);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['incident-reports'] });
      setShowGenerateDialog(false);
      resetGenerateForm();
    },
  });

  const createTemplate = useMutation({
    mutationFn: async (payload: typeof templateForm) => {
      const res = await apiClient.post('/incident-reports/templates', payload);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['incident-report-templates'] });
      setShowTemplateDialog(false);
      resetTemplateForm();
    },
  });

  const updateTemplate = useMutation({
    mutationFn: async ({
      id,
      payload,
    }: {
      id: string;
      payload: Partial<typeof templateForm>;
    }) => {
      const res = await apiClient.put(`/incident-reports/templates/${id}`, payload);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['incident-report-templates'] });
      setShowTemplateDialog(false);
      resetTemplateForm();
    },
  });

  // ── Handlers ────────────────────────────────────────────────────────

  const resetGenerateForm = () => {
    setGenerateType('incident');
    setGenerateAlertId('');
    setGenerateDateFrom('');
    setGenerateDateTo('');
    setGenerateTitle('');
  };

  const resetTemplateForm = () => {
    setEditingTemplate(null);
    setTemplateForm({
      name: '',
      template_type: 'incident',
      header_html: '',
      footer_html: '',
      css_styles: '',
      is_default: false,
    });
  };

  const handleGenerate = () => {
    if (generateType === 'incident') {
      if (!generateAlertId) return;
      generateFromAlert.mutate({
        alert_id: generateAlertId,
        title: generateTitle || undefined,
      });
    } else if (generateType === 'daily_summary') {
      if (!generateDateFrom) return;
      generateDailySummary.mutate({
        report_date: generateDateFrom,
      });
    } else if (generateType === 'weekly_report') {
      if (!generateDateFrom) return;
      generateWeeklyReport.mutate({
        week_start: generateDateFrom,
      });
    }
  };

  const handleQuickDailySummary = () => {
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    const dateStr = yesterday.toISOString().split('T')[0];
    generateDailySummary.mutate({ report_date: dateStr });
  };

  const handleQuickWeeklyReport = () => {
    const today = new Date();
    const dayOfWeek = today.getDay();
    const lastMonday = new Date(today);
    lastMonday.setDate(today.getDate() - dayOfWeek - 6);
    const dateStr = lastMonday.toISOString().split('T')[0];
    generateWeeklyReport.mutate({ week_start: dateStr });
  };

  const handleDownload = async (report: IncidentReport) => {
    if (report.status !== 'completed') return;
    try {
      const res = await apiClient.get(`/incident-reports/${report.id}/download`, {
        responseType: 'blob',
      });
      const blob = new Blob([res.data], { type: 'application/pdf' });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const safeName = report.title.replace(/[^a-zA-Z0-9\s-]/g, '').slice(0, 80);
      a.download = `${safeName}.pdf`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (error) {
      console.error('Report download failed:', error);
    }
  };

  const handlePreview = async (report: IncidentReport) => {
    try {
      const res = await apiClient.get(`/incident-reports/${report.id}`);
      const reportData = res.data?.data || res.data;
      setPreviewReport(reportData);
      setShowPreviewDialog(true);
    } catch {
      setPreviewReport(report);
      setShowPreviewDialog(true);
    }
  };

  const openEditTemplate = (template: ReportTemplate) => {
    setEditingTemplate(template);
    setTemplateForm({
      name: template.name,
      template_type: template.template_type,
      header_html: template.header_html || '',
      footer_html: template.footer_html || '',
      css_styles: template.css_styles || '',
      is_default: template.is_default,
    });
    setShowTemplateDialog(true);
  };

  const handleSaveTemplate = () => {
    if (editingTemplate) {
      updateTemplate.mutate({ id: editingTemplate.id, payload: templateForm });
    } else {
      createTemplate.mutate(templateForm);
    }
  };

  const isGenerating =
    generateFromAlert.isPending ||
    generateDailySummary.isPending ||
    generateWeeklyReport.isPending;

  // ── Badge renderers ─────────────────────────────────────────────────

  const renderTypeBadge = (type: string) => {
    const cfg = reportTypeConfig[type] || reportTypeConfig.custom;
    const Icon = cfg.icon;
    return (
      <Badge className={`gap-1 text-xs ${cfg.color}`}>
        <Icon className="h-3 w-3" />
        {cfg.label}
      </Badge>
    );
  };

  const renderStatusBadge = (status: string) => {
    const cfg = statusConfig[status] || statusConfig.generating;
    const Icon = cfg.icon;
    return (
      <Badge className={`gap-1 text-xs ${cfg.color}`}>
        <Icon
          className={`h-3 w-3 ${status === 'generating' ? 'animate-spin' : ''}`}
        />
        {cfg.label}
      </Badge>
    );
  };

  // ── Render ──────────────────────────────────────────────────────────

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">
            Incident Reports
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Automated incident report generation and management
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleQuickDailySummary}
            disabled={generateDailySummary.isPending}
          >
            {generateDailySummary.isPending ? (
              <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
            ) : (
              <Zap className="mr-1.5 h-4 w-4" />
            )}
            Today&apos;s Summary
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={handleQuickWeeklyReport}
            disabled={generateWeeklyReport.isPending}
          >
            {generateWeeklyReport.isPending ? (
              <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
            ) : (
              <TrendingUp className="mr-1.5 h-4 w-4" />
            )}
            Weekly Report
          </Button>
          <Button size="sm" onClick={() => setShowGenerateDialog(true)}>
            <Plus className="mr-1.5 h-4 w-4" />
            Generate Report
          </Button>
        </div>
      </div>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="reports">
            <FileText className="mr-1 h-4 w-4" />
            Reports
          </TabsTrigger>
          <TabsTrigger value="templates">
            <LayoutTemplate className="mr-1 h-4 w-4" />
            Templates
          </TabsTrigger>
        </TabsList>

        {/* Reports Tab */}
        <TabsContent value="reports">
          {/* Filters */}
          <Card className="mb-4">
            <CardContent className="flex flex-wrap items-end gap-3 p-4">
              <div className="space-y-1">
                <Label className="text-xs">Type</Label>
                <Select value={filterType} onValueChange={(v) => { setFilterType(v); setPage(1); }}>
                  <SelectTrigger className="h-9 w-[150px] text-sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Types</SelectItem>
                    <SelectItem value="incident">Incident</SelectItem>
                    <SelectItem value="daily_summary">Daily Summary</SelectItem>
                    <SelectItem value="weekly_report">Weekly Report</SelectItem>
                    <SelectItem value="monthly_report">Monthly Report</SelectItem>
                    <SelectItem value="custom">Custom</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label className="text-xs">Status</Label>
                <Select value={filterStatus} onValueChange={(v) => { setFilterStatus(v); setPage(1); }}>
                  <SelectTrigger className="h-9 w-[150px] text-sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Statuses</SelectItem>
                    <SelectItem value="generating">Generating</SelectItem>
                    <SelectItem value="completed">Completed</SelectItem>
                    <SelectItem value="failed">Failed</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label className="text-xs">From</Label>
                <Input
                  type="date"
                  value={filterStartDate}
                  onChange={(e) => { setFilterStartDate(e.target.value); setPage(1); }}
                  className="h-9 w-[150px] text-sm"
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">To</Label>
                <Input
                  type="date"
                  value={filterEndDate}
                  onChange={(e) => { setFilterEndDate(e.target.value); setPage(1); }}
                  className="h-9 w-[150px] text-sm"
                />
              </div>
              {(filterType !== 'all' || filterStatus !== 'all' || filterStartDate || filterEndDate) && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-9"
                  onClick={() => {
                    setFilterType('all');
                    setFilterStatus('all');
                    setFilterStartDate('');
                    setFilterEndDate('');
                    setPage(1);
                  }}
                >
                  <X className="mr-1 h-3.5 w-3.5" />
                  Clear
                </Button>
              )}
            </CardContent>
          </Card>

          {/* Reports Table */}
          <Card>
            <CardContent className="p-0">
              {reportsLoading ? (
                <div className="flex h-64 items-center justify-center">
                  <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
                </div>
              ) : reports.length === 0 ? (
                <div className="flex h-64 flex-col items-center justify-center text-slate-400">
                  <FileWarning className="mb-3 h-10 w-10" />
                  <p className="text-sm font-medium">No incident reports found</p>
                  <p className="mt-1 text-xs">
                    Generate your first report using the button above
                  </p>
                  <Button
                    variant="link"
                    className="mt-2"
                    onClick={() => setShowGenerateDialog(true)}
                  >
                    Generate a report
                  </Button>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-200 bg-slate-50/50 dark:border-slate-700 dark:bg-slate-800/50">
                        <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                          Title
                        </th>
                        <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                          Type
                        </th>
                        <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                          Status
                        </th>
                        <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                          Date
                        </th>
                        <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                          Generated By
                        </th>
                        <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                          Pages
                        </th>
                        <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                          Size
                        </th>
                        <th className="px-4 py-3 text-right font-medium text-slate-600 dark:text-slate-300">
                          Actions
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {reports.map((report) => (
                        <tr
                          key={report.id}
                          className="cursor-pointer border-b border-slate-100 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50"
                          onClick={() => handlePreview(report)}
                        >
                          <td className="max-w-[250px] px-4 py-3">
                            <div className="flex items-center gap-2">
                              <FileText className="h-4 w-4 flex-shrink-0 text-slate-400" />
                              <span className="truncate font-medium text-slate-700 dark:text-slate-300">
                                {report.title}
                              </span>
                            </div>
                          </td>
                          <td className="px-4 py-3">{renderTypeBadge(report.report_type)}</td>
                          <td className="px-4 py-3">{renderStatusBadge(report.status)}</td>
                          <td className="whitespace-nowrap px-4 py-3 text-slate-600 dark:text-slate-400">
                            {formatDate(report.created_at, 'MMM d, yyyy HH:mm')}
                          </td>
                          <td className="px-4 py-3 text-slate-600 dark:text-slate-400">
                            {report.generated_by_name || '--'}
                          </td>
                          <td className="px-4 py-3 text-slate-600 dark:text-slate-400">
                            {report.page_count || '--'}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-slate-600 dark:text-slate-400">
                            {report.file_size ? formatBytes(report.file_size) : '--'}
                          </td>
                          <td className="px-4 py-3 text-right">
                            <div
                              className="flex items-center justify-end gap-1"
                              onClick={(e) => e.stopPropagation()}
                            >
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => handlePreview(report)}
                                title="Preview"
                              >
                                <Eye className="h-4 w-4" />
                              </Button>
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => handleDownload(report)}
                                disabled={report.status !== 'completed'}
                                title="Download PDF"
                              >
                                <Download className="h-4 w-4" />
                              </Button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="flex items-center justify-between border-t border-slate-200 px-4 py-3 dark:border-slate-700">
                  <span className="text-sm text-slate-500 dark:text-slate-400">
                    Page {page} of {totalPages} ({totalReports} reports)
                  </span>
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setPage((p) => Math.max(1, p - 1))}
                      disabled={page === 1}
                    >
                      <ChevronLeft className="mr-1 h-4 w-4" />
                      Previous
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                      disabled={page === totalPages}
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

        {/* Templates Tab */}
        <TabsContent value="templates">
          <div className="mb-4 flex items-center justify-between">
            <p className="text-sm text-slate-500 dark:text-slate-400">
              Manage custom templates for report branding and layout
            </p>
            <Button
              size="sm"
              onClick={() => {
                resetTemplateForm();
                setShowTemplateDialog(true);
              }}
            >
              <Plus className="mr-1.5 h-4 w-4" />
              New Template
            </Button>
          </div>

          {templatesLoading ? (
            <div className="flex h-64 items-center justify-center">
              <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
            </div>
          ) : !templates || templates.length === 0 ? (
            <Card>
              <CardContent className="flex h-64 flex-col items-center justify-center text-slate-400">
                <LayoutTemplate className="mb-3 h-10 w-10" />
                <p className="text-sm font-medium">No custom templates</p>
                <p className="mt-1 text-xs">
                  Create a template to customize report branding
                </p>
                <Button
                  variant="link"
                  className="mt-2"
                  onClick={() => {
                    resetTemplateForm();
                    setShowTemplateDialog(true);
                  }}
                >
                  Create template
                </Button>
              </CardContent>
            </Card>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {templates.map((template) => (
                <Card
                  key={template.id}
                  className="transition-shadow hover:shadow-md"
                >
                  <CardHeader className="pb-3">
                    <div className="flex items-start justify-between">
                      <div className="flex items-center gap-2">
                        <LayoutTemplate className="h-5 w-5 text-blue-500" />
                        <CardTitle className="text-base">{template.name}</CardTitle>
                      </div>
                      {template.is_default && (
                        <Badge className="bg-green-100 text-xs text-green-700 dark:bg-green-900/30 dark:text-green-400">
                          Default
                        </Badge>
                      )}
                    </div>
                  </CardHeader>
                  <CardContent>
                    <div className="mb-3 space-y-1 text-sm text-slate-500 dark:text-slate-400">
                      <p>
                        Type:{' '}
                        {renderTypeBadge(template.template_type)}
                      </p>
                      <p>Created: {formatDate(template.created_at, 'MMM d, yyyy')}</p>
                      <p>
                        Header: {template.header_html ? 'Custom' : 'Default'}
                        {' | '}
                        Footer: {template.footer_html ? 'Custom' : 'Default'}
                        {' | '}
                        CSS: {template.css_styles ? 'Custom' : 'Default'}
                      </p>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      className="w-full"
                      onClick={() => openEditTemplate(template)}
                    >
                      <Pencil className="mr-1.5 h-3.5 w-3.5" />
                      Edit Template
                    </Button>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>

      {/* ── Generate Report Dialog ──────────────────────────────────── */}
      <Dialog open={showGenerateDialog} onOpenChange={setShowGenerateDialog}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Sparkles className="h-5 w-5" />
              Generate Incident Report
            </DialogTitle>
            <DialogDescription>
              Choose report type and configure parameters
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            {(generateFromAlert.isError ||
              generateDailySummary.isError ||
              generateWeeklyReport.isError) && (
              <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">
                Failed to generate report. Please try again.
              </div>
            )}

            {/* Report type selection */}
            <div className="space-y-2">
              <Label className="text-xs font-medium">Report Type</Label>
              <Select
                value={generateType}
                onValueChange={(v) => setGenerateType(v)}
              >
                <SelectTrigger className="text-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="incident">
                    <div className="flex items-center gap-2">
                      <ShieldAlert className="h-3.5 w-3.5" />
                      From Alert (Incident)
                    </div>
                  </SelectItem>
                  <SelectItem value="daily_summary">
                    <div className="flex items-center gap-2">
                      <CalendarDays className="h-3.5 w-3.5" />
                      Daily Summary
                    </div>
                  </SelectItem>
                  <SelectItem value="weekly_report">
                    <div className="flex items-center gap-2">
                      <CalendarRange className="h-3.5 w-3.5" />
                      Weekly Report
                    </div>
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Incident: alert selector */}
            {generateType === 'incident' && (
              <>
                <div className="space-y-2">
                  <Label className="text-xs font-medium">Select Alert</Label>
                  <Select
                    value={generateAlertId}
                    onValueChange={setGenerateAlertId}
                  >
                    <SelectTrigger className="text-sm">
                      <SelectValue placeholder="Choose an alert..." />
                    </SelectTrigger>
                    <SelectContent>
                      {recentAlerts && recentAlerts.length > 0 ? (
                        recentAlerts.map((alert) => (
                          <SelectItem key={alert.id} value={alert.id}>
                            <div className="flex items-center gap-2">
                              <AlertTriangle className="h-3 w-3" />
                              <span className="max-w-[280px] truncate">
                                {alert.title}
                              </span>
                              <Badge
                                className={`ml-1 text-[10px] ${
                                  alert.severity === 'critical'
                                    ? 'bg-red-100 text-red-700'
                                    : alert.severity === 'high'
                                      ? 'bg-orange-100 text-orange-700'
                                      : 'bg-slate-100 text-slate-700'
                                }`}
                              >
                                {alert.severity}
                              </Badge>
                            </div>
                          </SelectItem>
                        ))
                      ) : (
                        <SelectItem value="none" disabled>
                          No recent alerts
                        </SelectItem>
                      )}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label className="text-xs font-medium">
                    Custom Title{' '}
                    <span className="text-slate-400">(optional)</span>
                  </Label>
                  <Input
                    placeholder="Auto-generated if empty"
                    value={generateTitle}
                    onChange={(e) => setGenerateTitle(e.target.value)}
                    className="text-sm"
                  />
                </div>
              </>
            )}

            {/* Daily summary: date picker */}
            {generateType === 'daily_summary' && (
              <div className="space-y-2">
                <Label className="text-xs font-medium">Report Date</Label>
                <Input
                  type="date"
                  value={generateDateFrom}
                  onChange={(e) => setGenerateDateFrom(e.target.value)}
                  className="text-sm"
                />
              </div>
            )}

            {/* Weekly report: week start */}
            {generateType === 'weekly_report' && (
              <div className="space-y-2">
                <Label className="text-xs font-medium">
                  Week Start (Monday)
                </Label>
                <Input
                  type="date"
                  value={generateDateFrom}
                  onChange={(e) => setGenerateDateFrom(e.target.value)}
                  className="text-sm"
                />
              </div>
            )}
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setShowGenerateDialog(false);
                resetGenerateForm();
              }}
            >
              Cancel
            </Button>
            <Button
              onClick={handleGenerate}
              disabled={
                isGenerating ||
                (generateType === 'incident' && !generateAlertId) ||
                (generateType !== 'incident' && !generateDateFrom)
              }
            >
              {isGenerating ? (
                <>
                  <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                  Generating...
                </>
              ) : (
                <>
                  <Sparkles className="mr-1.5 h-4 w-4" />
                  Generate Report
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Report Preview Dialog ──────────────────────────────────── */}
      <Dialog open={showPreviewDialog} onOpenChange={setShowPreviewDialog}>
        <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Eye className="h-5 w-5" />
              Report Details
            </DialogTitle>
          </DialogHeader>

          {previewReport && (
            <div className="space-y-4">
              {/* Status & type */}
              <div className="flex flex-wrap items-center gap-2">
                {renderTypeBadge(previewReport.report_type)}
                {renderStatusBadge(previewReport.status)}
              </div>

              {/* Title */}
              <h3 className="text-lg font-semibold text-slate-800 dark:text-slate-200">
                {previewReport.title}
              </h3>

              {/* Metadata grid */}
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <span className="text-slate-500 dark:text-slate-400">Created:</span>
                  <span className="ml-2 font-medium text-slate-700 dark:text-slate-300">
                    {formatDate(previewReport.created_at, 'MMM d, yyyy HH:mm')}
                  </span>
                </div>
                <div>
                  <span className="text-slate-500 dark:text-slate-400">Generated by:</span>
                  <span className="ml-2 font-medium text-slate-700 dark:text-slate-300">
                    {previewReport.generated_by_name || 'System'}
                  </span>
                </div>
                {previewReport.page_count && (
                  <div>
                    <span className="text-slate-500 dark:text-slate-400">Pages:</span>
                    <span className="ml-2 font-medium text-slate-700 dark:text-slate-300">
                      {previewReport.page_count}
                    </span>
                  </div>
                )}
                {previewReport.file_size && (
                  <div>
                    <span className="text-slate-500 dark:text-slate-400">File size:</span>
                    <span className="ml-2 font-medium text-slate-700 dark:text-slate-300">
                      {formatBytes(previewReport.file_size)}
                    </span>
                  </div>
                )}
              </div>

              {/* Summary */}
              {previewReport.summary_text && (
                <div>
                  <h4 className="mb-1 text-sm font-semibold text-slate-700 dark:text-slate-300">
                    Executive Summary
                  </h4>
                  <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm leading-relaxed text-slate-700 dark:border-blue-800 dark:bg-blue-900/20 dark:text-slate-300">
                    {previewReport.summary_text}
                  </div>
                </div>
              )}

              {/* Metadata */}
              {previewReport.metadata_json && (
                <div>
                  <h4 className="mb-1 text-sm font-semibold text-slate-700 dark:text-slate-300">
                    Report Metadata
                  </h4>
                  <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-800/50">
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      {previewReport.metadata_json.alert_count !== undefined && (
                        <div>
                          <span className="text-slate-500">Alert count:</span>{' '}
                          <span className="font-medium">
                            {String(previewReport.metadata_json.alert_count)}
                          </span>
                        </div>
                      )}
                      {previewReport.metadata_json.severity && (
                        <div>
                          <span className="text-slate-500">Severity:</span>{' '}
                          <span className="font-medium">
                            {String(previewReport.metadata_json.severity)}
                          </span>
                        </div>
                      )}
                      {previewReport.metadata_json.alert_type && (
                        <div>
                          <span className="text-slate-500">Alert type:</span>{' '}
                          <span className="font-medium">
                            {String(previewReport.metadata_json.alert_type)}
                          </span>
                        </div>
                      )}
                      {previewReport.metadata_json.timeline_events !== undefined && (
                        <div>
                          <span className="text-slate-500">Timeline events:</span>{' '}
                          <span className="font-medium">
                            {String(previewReport.metadata_json.timeline_events)}
                          </span>
                        </div>
                      )}
                      {previewReport.metadata_json.date && (
                        <div>
                          <span className="text-slate-500">Date:</span>{' '}
                          <span className="font-medium">
                            {String(previewReport.metadata_json.date)}
                          </span>
                        </div>
                      )}
                      {previewReport.metadata_json.week_start && (
                        <div>
                          <span className="text-slate-500">Week start:</span>{' '}
                          <span className="font-medium">
                            {String(previewReport.metadata_json.week_start)}
                          </span>
                        </div>
                      )}
                      {previewReport.metadata_json.wow_change !== undefined && (
                        <div>
                          <span className="text-slate-500">WoW change:</span>{' '}
                          <span className="font-medium">
                            {Number(previewReport.metadata_json.wow_change) > 0 ? '+' : ''}
                            {String(previewReport.metadata_json.wow_change)}%
                          </span>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}

              {/* Error message */}
              {previewReport.error_message && (
                <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/20 dark:text-red-400">
                  <strong>Error:</strong> {previewReport.error_message}
                </div>
              )}

              {/* PDF Preview iframe */}
              {previewReport.status === 'completed' && previewReport.download_url && (
                <div>
                  <h4 className="mb-1 text-sm font-semibold text-slate-700 dark:text-slate-300">
                    PDF Preview
                  </h4>
                  <div className="overflow-hidden rounded-lg border border-slate-200 dark:border-slate-700">
                    <iframe
                      src={previewReport.download_url}
                      className="h-[400px] w-full"
                      title="Report PDF Preview"
                    />
                  </div>
                </div>
              )}

              {/* Actions */}
              <div className="flex justify-end gap-2 border-t border-slate-200 pt-3 dark:border-slate-700">
                <Button variant="outline" onClick={() => setShowPreviewDialog(false)}>
                  Close
                </Button>
                {previewReport.status === 'completed' && (
                  <Button onClick={() => handleDownload(previewReport)}>
                    <Download className="mr-1.5 h-4 w-4" />
                    Download PDF
                  </Button>
                )}
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* ── Template Edit Dialog ───────────────────────────────────── */}
      <Dialog open={showTemplateDialog} onOpenChange={setShowTemplateDialog}>
        <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <LayoutTemplate className="h-5 w-5" />
              {editingTemplate ? 'Edit Template' : 'Create Template'}
            </DialogTitle>
            <DialogDescription>
              Customize the HTML header, footer, and CSS for generated reports
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            {(createTemplate.isError || updateTemplate.isError) && (
              <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">
                Failed to save template. Please check your input and try again.
              </div>
            )}

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label className="text-xs font-medium">Template Name</Label>
                <Input
                  value={templateForm.name}
                  onChange={(e) =>
                    setTemplateForm((f) => ({ ...f, name: e.target.value }))
                  }
                  placeholder="e.g., Corporate Security Template"
                  className="text-sm"
                />
              </div>
              <div className="space-y-2">
                <Label className="text-xs font-medium">Type</Label>
                <Select
                  value={templateForm.template_type}
                  onValueChange={(v) =>
                    setTemplateForm((f) => ({ ...f, template_type: v }))
                  }
                >
                  <SelectTrigger className="text-sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="incident">Incident</SelectItem>
                    <SelectItem value="daily_summary">Daily Summary</SelectItem>
                    <SelectItem value="weekly_report">Weekly Report</SelectItem>
                    <SelectItem value="custom">Custom</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="space-y-2">
              <Label className="text-xs font-medium">Header HTML</Label>
              <Textarea
                value={templateForm.header_html}
                onChange={(e) =>
                  setTemplateForm((f) => ({ ...f, header_html: e.target.value }))
                }
                placeholder='<div class="logo">Your Company</div><div class="logo-sub">Security Division</div>'
                rows={4}
                className="font-mono text-xs"
              />
            </div>

            <div className="space-y-2">
              <Label className="text-xs font-medium">Footer HTML</Label>
              <Textarea
                value={templateForm.footer_html}
                onChange={(e) =>
                  setTemplateForm((f) => ({ ...f, footer_html: e.target.value }))
                }
                placeholder='<p style="font-size: 8pt; text-align: center;">Your Company - Confidential</p>'
                rows={3}
                className="font-mono text-xs"
              />
            </div>

            <div className="space-y-2">
              <Label className="text-xs font-medium">Custom CSS</Label>
              <Textarea
                value={templateForm.css_styles}
                onChange={(e) =>
                  setTemplateForm((f) => ({ ...f, css_styles: e.target.value }))
                }
                placeholder=".cover .logo { color: #003366; } .summary-box { border-left-color: #003366; }"
                rows={5}
                className="font-mono text-xs"
              />
            </div>

            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="is_default"
                checked={templateForm.is_default}
                onChange={(e) =>
                  setTemplateForm((f) => ({ ...f, is_default: e.target.checked }))
                }
                className="rounded border-slate-300"
              />
              <Label htmlFor="is_default" className="text-xs">
                Set as default template for this report type
              </Label>
            </div>
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setShowTemplateDialog(false);
                resetTemplateForm();
              }}
            >
              Cancel
            </Button>
            <Button
              onClick={handleSaveTemplate}
              disabled={
                createTemplate.isPending ||
                updateTemplate.isPending ||
                !templateForm.name.trim()
              }
            >
              {createTemplate.isPending || updateTemplate.isPending ? (
                <>
                  <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                  Saving...
                </>
              ) : (
                <>
                  <Save className="mr-1.5 h-4 w-4" />
                  {editingTemplate ? 'Update Template' : 'Create Template'}
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
