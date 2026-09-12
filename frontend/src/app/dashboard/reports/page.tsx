'use client';

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  FileText,
  BarChart3,
  CalendarDays,
  ClipboardCheck,
  ShieldAlert,
  UserCheck,
  Car,
  HardHat,
  Settings2,
  Loader2,
  Download,
  Plus,
  Clock,
  Calendar,
  Mail,
  ChevronLeft,
  ChevronRight,
  Sparkles,
  FileSpreadsheet,
  File,
  Repeat,
  Trash2,
} from 'lucide-react';
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
import { getAccessToken } from '@/lib/auth';
import { formatBytes, formatDate } from '@/lib/utils';

interface ReportTemplate {
  id: string;
  name: string;
  description: string;
  icon: string;
  category: string;
}

interface GeneratedReport {
  id: string;
  name: string;
  type: string;
  template_id: string;
  generated_at: string;
  format: 'pdf' | 'excel' | 'csv';
  size_bytes: number;
  status: 'generating' | 'completed' | 'failed';
  download_url: string | null;
  parameters: Record<string, string>;
}

interface ScheduledReport {
  id: string;
  template_id: string;
  template_name: string;
  frequency: 'daily' | 'weekly' | 'monthly';
  format: 'pdf' | 'excel' | 'csv';
  recipients: string[];
  next_run: string;
  enabled: boolean;
  created_at: string;
}

interface CameraOption {
  id: string;
  name: string;
}

const reportTemplates: ReportTemplate[] = [
  {
    id: 'daily-summary',
    name: 'Daily Summary',
    description: 'Overview of all activities, alerts, and detections for a single day.',
    icon: 'CalendarDays',
    category: 'summary',
  },
  {
    id: 'weekly-analytics',
    name: 'Weekly Analytics',
    description: 'Detailed analytics with trends and patterns over the past week.',
    icon: 'BarChart3',
    category: 'analytics',
  },
  {
    id: 'monthly-compliance',
    name: 'Monthly Compliance',
    description: 'Compliance metrics and audit trail for regulatory requirements.',
    icon: 'ClipboardCheck',
    category: 'compliance',
  },
  {
    id: 'incident-report',
    name: 'Incident Report',
    description: 'Detailed incident documentation with timeline and evidence.',
    icon: 'ShieldAlert',
    category: 'security',
  },

  {
    id: 'vehicle-log',
    name: 'Vehicle Log',
    description: 'Complete vehicle entry/exit log with plate numbers and durations.',
    icon: 'Car',
    category: 'vehicles',
  },
  {
    id: 'ppe-compliance',
    name: 'PPE Compliance',
    description: 'Personal protective equipment compliance rates and violations.',
    icon: 'HardHat',
    category: 'safety',
  },
  {
    id: 'custom-report',
    name: 'Custom Report',
    description: 'Build a custom report with your own parameters and data sources.',
    icon: 'Settings2',
    category: 'custom',
  },
];

const templateIcons: Record<string, React.ElementType> = {
  CalendarDays,
  BarChart3,
  ClipboardCheck,
  ShieldAlert,
  UserCheck,
  Car,
  HardHat,
  Settings2,
};

const templateColors: Record<string, string> = {
  summary: 'bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400',
  analytics: 'bg-purple-100 text-purple-600 dark:bg-purple-900/30 dark:text-purple-400',
  compliance: 'bg-green-100 text-green-600 dark:bg-green-900/30 dark:text-green-400',
  security: 'bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-400',
  attendance: 'bg-amber-100 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400',
  vehicles: 'bg-cyan-100 text-cyan-600 dark:bg-cyan-900/30 dark:text-cyan-400',
  safety: 'bg-orange-100 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400',
  custom: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400',
};


export default function ReportsPage() {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState('templates');

  // Generate dialog state
  const [showGenerateDialog, setShowGenerateDialog] = useState(false);
  const [selectedTemplate, setSelectedTemplate] = useState<ReportTemplate | null>(null);
  const [generateForm, setGenerateForm] = useState({
    date_from: '',
    date_to: '',
    camera_ids: [] as string[],
    format: 'pdf' as 'pdf' | 'excel' | 'csv',
    recipients: '',
  });

  // Schedule dialog state
  const [showScheduleDialog, setShowScheduleDialog] = useState(false);
  const [scheduleTemplate, setScheduleTemplate] = useState<ReportTemplate | null>(null);
  const [scheduleForm, setScheduleForm] = useState({
    frequency: 'weekly' as 'daily' | 'weekly' | 'monthly',
    format: 'pdf' as 'pdf' | 'excel' | 'csv',
    recipients: '',
  });

  // Reports table pagination
  const [reportPage, setReportPage] = useState(1);
  const reportPageSize = 10;

  const { data: cameras } = useQuery<CameraOption[]>({
    queryKey: ['cameras', 'options'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/cameras', { params: { limit: 200 } });
      return (res.data.items || res.data).map((c: Record<string, string>) => ({
        id: c.id,
        name: c.name,
      }));
    },
  });

  const { data: reportsData, isLoading: reportsLoading } = useQuery({
    queryKey: ['reports', 'generated', reportPage],
    queryFn: async () => {
      const res: any = await apiClient.get('/api/v1/reports', {
        params: { page: reportPage, page_size: reportPageSize },
      });
      if (res && Array.isArray(res.items)) {
        return res as { items: GeneratedReport[]; total: number };
      }
      if (res && res.data && Array.isArray(res.data.items)) {
        return res.data as { items: GeneratedReport[]; total: number };
      }
      return {
        items: (Array.isArray(res) ? res : (res?.data || [])) as GeneratedReport[],
        total: (res?.total || 0),
      };
    },
    refetchInterval: 3000,
  });

  const { data: scheduledData, isLoading: scheduledLoading } = useQuery({
    queryKey: ['reports', 'scheduled'],
    queryFn: async () => {
      const res: any = await apiClient.get('/api/v1/reports/schedules');
      if (res && Array.isArray(res.items)) {
        return res as { items: ScheduledReport[]; total: number };
      }
      if (res && res.data && Array.isArray(res.data.items)) {
        return res.data as { items: ScheduledReport[]; total: number };
      }
      return {
        items: (Array.isArray(res) ? res : (res?.data || [])) as ScheduledReport[],
        total: (res?.total || 0),
      };
    },
  });

  const reports = reportsData?.items || [];
  const totalReports = reportsData?.total || 0;
  const totalReportPages = Math.ceil(totalReports / reportPageSize);
  const scheduledReports = scheduledData?.items || [];

  const generateMutation = useMutation({
    mutationFn: async (payload: {
      report_type: string;
      start_date: string;
      end_date: string;
      camera_ids?: string[];
      format: string;
    }) => {
      const res = await apiClient.post('/api/v1/reports/generate', payload);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['reports', 'generated'] });
      setShowGenerateDialog(false);
      resetGenerateForm();
      setActiveTab('generated');
    },
  });

  const scheduleMutation = useMutation({
    mutationFn: async (payload: {
      template_id: string;
      frequency: string;
      format: string;
      recipients: string[];
    }) => {
      const res = await apiClient.post('/api/v1/reports/schedules', payload);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['reports', 'scheduled'] });
      setShowScheduleDialog(false);
      resetScheduleForm();
    },
  });

  const deleteScheduleMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/api/v1/reports/schedules/${id}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['reports', 'scheduled'] });
    },
  });

  const resetGenerateForm = () => {
    setGenerateForm({
      date_from: '',
      date_to: '',
      camera_ids: [],
      format: 'pdf',
      recipients: '',
    });
    setSelectedTemplate(null);
  };

  const resetScheduleForm = () => {
    setScheduleForm({
      frequency: 'weekly',
      format: 'pdf',
      recipients: '',
    });
    setScheduleTemplate(null);
  };

  const openGenerateDialog = (template: ReportTemplate) => {
    generateMutation.reset();
    setSelectedTemplate(template);
    setShowGenerateDialog(true);
  };

  const openScheduleDialog = (template: ReportTemplate) => {
    scheduleMutation.reset();
    setScheduleTemplate(template);
    setShowScheduleDialog(true);
  };

  const handleGenerate = () => {
    if (!selectedTemplate) return;

    const toCleanDate = (val: string) => {
      const today = new Date().toISOString().split('T')[0];
      if (!val) return today;
      try {
        const parsed = new Date(val);
        if (!isNaN(parsed.getTime())) {
          return parsed.toISOString().split('T')[0];
        }
      } catch (e) {}
      return val.split('T')[0].split(' ')[0] || today;
    };

    const reportType = selectedTemplate.id.replace(/-/g, '_');
    const startDate = toCleanDate(generateForm.date_from);
    const endDate = toCleanDate(generateForm.date_to);

    generateMutation.mutate({
      report_type: reportType,
      start_date: startDate,
      end_date: endDate,
      camera_ids: generateForm.camera_ids.length > 0 ? generateForm.camera_ids : undefined,
      format: generateForm.format,
    });
  };

  const handleSchedule = () => {
    if (!scheduleTemplate) return;

    const recipients = scheduleForm.recipients
      .split(',')
      .map((r) => r.trim())
      .filter(Boolean);

    scheduleMutation.mutate({
      template_id: scheduleTemplate.id,
      frequency: scheduleForm.frequency,
      format: scheduleForm.format,
      recipients,
    });
  };

  const handleDownloadReport = async (report: GeneratedReport) => {
    try {
      const token = getAccessToken();
      const tokenParam = token ? `?token=${token}` : '';
      const endpoint = report.download_url
        ? `${report.download_url}${report.download_url.includes('?') ? '&' : '?'}${tokenParam.replace('?', '')}`
        : `/api/v1/reports/${report.id}/download${tokenParam}`;

      const res: any = await apiClient.get(endpoint, {
        responseType: 'blob',
      });

      const extension = report.format === 'excel' ? 'xlsx' : report.format;
      const rawData = res instanceof Blob ? res : (res?.data instanceof Blob ? res.data : res?.data || res);
      const blob = rawData instanceof Blob ? rawData : new Blob([rawData], { type: report.format === 'pdf' ? 'application/pdf' : 'application/octet-stream' });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${report.name.replace(/\s+/g, '_')}.${extension}`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (error) {
      console.error('Report download via blob failed, falling back to direct window open:', error);
      const token = getAccessToken();
      const tokenParam = token ? `?token=${token}` : '';
      const fallbackUrl = `/api/v1/reports/${report.id}/download${tokenParam}`;
      window.open(fallbackUrl, '_blank');
    }
  };

  const toggleCameraSelection = (camId: string) => {
    setGenerateForm((prev) => ({
      ...prev,
      camera_ids: prev.camera_ids.includes(camId)
        ? prev.camera_ids.filter((id) => id !== camId)
        : [...prev.camera_ids, camId],
    }));
  };

  const formatBadge = (fmt: string) => {
    const colors: Record<string, string> = {
      pdf: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
      excel: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
      csv: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
    };
    return (
      <Badge className={`text-xs ${colors[fmt] || ''}`}>
        {fmt.toUpperCase()}
      </Badge>
    );
  };

  const statusBadge = (status: string) => {
    const colors: Record<string, string> = {
      generating: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
      completed: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
      failed: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
    };
    return (
      <Badge className={`text-xs ${colors[status] || ''}`}>
        {status === 'generating' && (
          <Loader2 className="mr-1 h-3 w-3 animate-spin" />
        )}
        {status.charAt(0).toUpperCase() + status.slice(1)}
      </Badge>
    );
  };

  const frequencyBadge = (frequency: string) => {
    const colors: Record<string, string> = {
      daily: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
      weekly: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400',
      monthly: 'bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-400',
    };
    return (
      <Badge className={`text-xs ${colors[frequency] || ''}`}>
        {frequency.charAt(0).toUpperCase() + frequency.slice(1)}
      </Badge>
    );
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Reports</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Generate, schedule, and manage reports
          </p>
        </div>
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="templates">
            <Sparkles className="mr-1 h-4 w-4" />
            Templates
          </TabsTrigger>
          <TabsTrigger value="generated">
            <FileText className="mr-1 h-4 w-4" />
            Generated Reports
          </TabsTrigger>
          <TabsTrigger value="scheduled">
            <Repeat className="mr-1 h-4 w-4" />
            Scheduled
          </TabsTrigger>
        </TabsList>

        {/* Templates Tab */}
        <TabsContent value="templates">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {reportTemplates.map((template) => {
              const Icon = templateIcons[template.icon] || FileText;
              const colorClass = templateColors[template.category] || templateColors.custom;

              return (
                <Card
                  key={template.id}
                  className="flex flex-col transition-shadow hover:shadow-md"
                >
                  <CardHeader className="pb-3">
                    <div className="flex items-start gap-3">
                      <div className={`rounded-lg p-2.5 ${colorClass}`}>
                        <Icon className="h-5 w-5" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <CardTitle className="text-base">{template.name}</CardTitle>
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent className="flex flex-1 flex-col justify-between gap-4">
                    <p className="text-sm text-slate-500 dark:text-slate-400">
                      {template.description}
                    </p>
                    <div className="flex gap-2">
                      <Button
                        size="sm"
                        className="flex-1"
                        onClick={() => openGenerateDialog(template)}
                      >
                        <Plus className="mr-1 h-3.5 w-3.5" />
                        Generate
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => openScheduleDialog(template)}
                      >
                        <Clock className="mr-1 h-3.5 w-3.5" />
                        Schedule
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </TabsContent>

        {/* Generated Reports Tab */}
        <TabsContent value="generated">
          <Card>
            <CardContent className="p-0">
              {reportsLoading ? (
                <div className="flex h-64 items-center justify-center">
                  <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
                </div>
              ) : reports.length === 0 ? (
                <div className="flex h-64 flex-col items-center justify-center text-slate-400">
                  <FileText className="mb-3 h-10 w-10" />
                  <p className="text-sm font-medium">No reports generated yet</p>
                  <p className="mt-1 text-xs">
                    Select a template to generate your first report
                  </p>
                  <Button
                    variant="link"
                    className="mt-2"
                    onClick={() => setActiveTab('templates')}
                  >
                    Browse templates
                  </Button>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-200 bg-slate-50/50 dark:border-slate-700 dark:bg-slate-800/50">
                        <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                          Name
                        </th>
                        <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                          Type
                        </th>
                        <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                          Generated At
                        </th>
                        <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                          Format
                        </th>
                        <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                          Size
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
                      {reports.map((report) => (
                        <tr
                          key={report.id}
                          className="border-b border-slate-100 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50"
                        >
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-2">
                              <FileText className="h-4 w-4 text-slate-400" />
                              <span className="font-medium text-slate-700 dark:text-slate-300">
                                {report.name}
                              </span>
                            </div>
                          </td>
                          <td className="px-4 py-3 text-slate-600 dark:text-slate-400">
                            {report.type.replace(/-/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase())}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-slate-600 dark:text-slate-400">
                            {formatDate(report.generated_at, 'MMM d, yyyy HH:mm')}
                          </td>
                          <td className="px-4 py-3">{formatBadge(report.format)}</td>
                          <td className="whitespace-nowrap px-4 py-3 text-slate-600 dark:text-slate-400">
                            {formatBytes(report.size_bytes)}
                          </td>
                          <td className="px-4 py-3">{statusBadge(report.status)}</td>
                          <td className="px-4 py-3 text-right">
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => handleDownloadReport(report)}
                              disabled={report.status !== 'completed'}
                              title="Download"
                            >
                              <Download className="h-4 w-4" />
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Pagination */}
              {totalReportPages > 1 && (
                <div className="flex items-center justify-between border-t border-slate-200 px-4 py-3 dark:border-slate-700">
                  <span className="text-sm text-slate-500 dark:text-slate-400">
                    Page {reportPage} of {totalReportPages} ({totalReports} reports)
                  </span>
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setReportPage((p) => Math.max(1, p - 1))}
                      disabled={reportPage === 1}
                    >
                      <ChevronLeft className="mr-1 h-4 w-4" />
                      Previous
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setReportPage((p) => Math.min(totalReportPages, p + 1))}
                      disabled={reportPage === totalReportPages}
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

        {/* Scheduled Reports Tab */}
        <TabsContent value="scheduled">
          <Card>
            <CardContent className="p-0">
              {scheduledLoading ? (
                <div className="flex h-64 items-center justify-center">
                  <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
                </div>
              ) : scheduledReports.length === 0 ? (
                <div className="flex h-64 flex-col items-center justify-center text-slate-400">
                  <Repeat className="mb-3 h-10 w-10" />
                  <p className="text-sm font-medium">No scheduled reports</p>
                  <p className="mt-1 text-xs">
                    Schedule a report from the templates tab
                  </p>
                  <Button
                    variant="link"
                    className="mt-2"
                    onClick={() => setActiveTab('templates')}
                  >
                    Browse templates
                  </Button>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-200 bg-slate-50/50 dark:border-slate-700 dark:bg-slate-800/50">
                        <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                          Report
                        </th>
                        <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                          Frequency
                        </th>
                        <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                          Format
                        </th>
                        <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                          Recipients
                        </th>
                        <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                          Next Run
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
                      {scheduledReports.map((schedule) => (
                        <tr
                          key={schedule.id}
                          className="border-b border-slate-100 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50"
                        >
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-2">
                              <Repeat className="h-4 w-4 text-slate-400" />
                              <span className="font-medium text-slate-700 dark:text-slate-300">
                                {schedule.template_name}
                              </span>
                            </div>
                          </td>
                          <td className="px-4 py-3">{frequencyBadge(schedule.frequency)}</td>
                          <td className="px-4 py-3">{formatBadge(schedule.format)}</td>
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-1 text-slate-600 dark:text-slate-400">
                              <Mail className="h-3 w-3" />
                              <span className="max-w-[150px] truncate text-xs">
                                {schedule.recipients.length > 0
                                  ? schedule.recipients.join(', ')
                                  : 'None'}
                              </span>
                            </div>
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-slate-600 dark:text-slate-400">
                            {formatDate(schedule.next_run, 'MMM d, yyyy HH:mm')}
                          </td>
                          <td className="px-4 py-3">
                            <Badge
                              className={`text-xs ${
                                schedule.enabled
                                  ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                                  : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400'
                              }`}
                            >
                              {schedule.enabled ? 'Active' : 'Paused'}
                            </Badge>
                          </td>
                          <td className="px-4 py-3 text-right">
                            <Button
                              variant="ghost"
                              size="sm"
                              className="text-red-500 hover:text-red-700"
                              onClick={() => {
                                if (confirm(`Delete schedule for "${schedule.template_name}"?`)) {
                                  deleteScheduleMutation.mutate(schedule.id);
                                }
                              }}
                              title="Delete schedule"
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* Generate Report Dialog */}
      <Dialog
        open={showGenerateDialog}
        onOpenChange={(open) => {
          if (!open) {
            setShowGenerateDialog(false);
            resetGenerateForm();
          }
        }}
      >
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Plus className="h-5 w-5" />
              Generate Report
            </DialogTitle>
            <DialogDescription>
              {selectedTemplate
                ? `Configure and generate a ${selectedTemplate.name} report`
                : 'Configure your report parameters'}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            {generateMutation.isError && (
              <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">
                {((generateMutation.error as unknown as Record<string, string>)?.message) || 'Failed to generate report'}
              </div>
            )}

            {/* Template info */}
            {selectedTemplate && (
              <div className="flex items-center gap-3 rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-800/50">
                {(() => {
                  const Icon = templateIcons[selectedTemplate.icon] || FileText;
                  return (
                    <div className={`rounded-lg p-2 ${templateColors[selectedTemplate.category] || ''}`}>
                      <Icon className="h-4 w-4" />
                    </div>
                  );
                })()}
                <div>
                  <p className="text-sm font-medium text-slate-700 dark:text-slate-300">
                    {selectedTemplate.name}
                  </p>
                  <p className="text-xs text-slate-500 dark:text-slate-400">
                    {selectedTemplate.description}
                  </p>
                </div>
              </div>
            )}

            {/* Date range */}
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label className="text-xs font-medium">Start Date</Label>
                <Input
                  type="datetime-local"
                  value={generateForm.date_from}
                  onChange={(e) =>
                    setGenerateForm((f) => ({ ...f, date_from: e.target.value }))
                  }
                  className="text-sm"
                />
              </div>
              <div className="space-y-2">
                <Label className="text-xs font-medium">End Date</Label>
                <Input
                  type="datetime-local"
                  value={generateForm.date_to}
                  onChange={(e) =>
                    setGenerateForm((f) => ({ ...f, date_to: e.target.value }))
                  }
                  className="text-sm"
                />
              </div>
            </div>

            {/* Camera selection */}
            <div className="space-y-2">
              <Label className="text-xs font-medium">
                Cameras ({generateForm.camera_ids.length === 0 ? 'All' : generateForm.camera_ids.length + ' selected'})
              </Label>
              <div className="max-h-32 overflow-y-auto rounded-md border border-slate-200 p-2 dark:border-slate-700">
                {cameras && cameras.length > 0 ? (
                  <div className="space-y-1">
                    {cameras.map((cam) => (
                      <label
                        key={cam.id}
                        className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-sm hover:bg-slate-100 dark:hover:bg-slate-800"
                      >
                        <input
                          type="checkbox"
                          checked={generateForm.camera_ids.includes(cam.id)}
                          onChange={() => toggleCameraSelection(cam.id)}
                          className="rounded border-slate-300"
                        />
                        <span className="text-slate-700 dark:text-slate-300">{cam.name}</span>
                      </label>
                    ))}
                  </div>
                ) : (
                  <p className="py-2 text-center text-xs text-slate-400">
                    No cameras available
                  </p>
                )}
              </div>
            </div>

            {/* Format */}
            <div className="space-y-2">
              <Label className="text-xs font-medium">Output Format</Label>
              <Select
                value={generateForm.format}
                onValueChange={(val) =>
                  setGenerateForm((f) => ({ ...f, format: val as 'pdf' | 'excel' | 'csv' }))
                }
              >
                <SelectTrigger className="text-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="pdf">
                    <div className="flex items-center gap-2">
                      <File className="h-3.5 w-3.5" />
                      PDF
                    </div>
                  </SelectItem>
                  <SelectItem value="excel">
                    <div className="flex items-center gap-2">
                      <FileSpreadsheet className="h-3.5 w-3.5" />
                      Excel (.xlsx)
                    </div>
                  </SelectItem>
                  <SelectItem value="csv">
                    <div className="flex items-center gap-2">
                      <FileText className="h-3.5 w-3.5" />
                      CSV
                    </div>
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Recipients */}
            <div className="space-y-2">
              <Label className="text-xs font-medium">
                Email Recipients <span className="text-slate-400">(optional, comma-separated)</span>
              </Label>
              <div className="relative">
                <Mail className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                <Input
                  placeholder="user@example.com, admin@example.com"
                  value={generateForm.recipients}
                  onChange={(e) =>
                    setGenerateForm((f) => ({ ...f, recipients: e.target.value }))
                  }
                  className="pl-10 text-sm"
                />
              </div>
            </div>
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
              disabled={generateMutation.isPending || !generateForm.date_from || !generateForm.date_to}
            >
              {generateMutation.isPending ? (
                <>
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                  Generating...
                </>
              ) : (
                <>
                  <Plus className="mr-1 h-4 w-4" />
                  Generate Report
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Schedule Report Dialog */}
      <Dialog
        open={showScheduleDialog}
        onOpenChange={(open) => {
          if (!open) {
            setShowScheduleDialog(false);
            resetScheduleForm();
          }
        }}
      >
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Repeat className="h-5 w-5" />
              Schedule Report
            </DialogTitle>
            <DialogDescription>
              {scheduleTemplate
                ? `Schedule automatic generation of ${scheduleTemplate.name}`
                : 'Configure your report schedule'}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            {scheduleMutation.isError && (
              <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">
                {((scheduleMutation.error as unknown as Record<string, string>)?.message) || 'Failed to schedule report'}
              </div>
            )}

            {/* Template info */}
            {scheduleTemplate && (
              <div className="flex items-center gap-3 rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-800/50">
                {(() => {
                  const Icon = templateIcons[scheduleTemplate.icon] || FileText;
                  return (
                    <div className={`rounded-lg p-2 ${templateColors[scheduleTemplate.category] || ''}`}>
                      <Icon className="h-4 w-4" />
                    </div>
                  );
                })()}
                <div>
                  <p className="text-sm font-medium text-slate-700 dark:text-slate-300">
                    {scheduleTemplate.name}
                  </p>
                  <p className="text-xs text-slate-500 dark:text-slate-400">
                    {scheduleTemplate.description}
                  </p>
                </div>
              </div>
            )}

            {/* Frequency */}
            <div className="space-y-2">
              <Label className="text-xs font-medium">Frequency</Label>
              <Select
                value={scheduleForm.frequency}
                onValueChange={(val) =>
                  setScheduleForm((f) => ({
                    ...f,
                    frequency: val as 'daily' | 'weekly' | 'monthly',
                  }))
                }
              >
                <SelectTrigger className="text-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="daily">
                    <div className="flex items-center gap-2">
                      <Calendar className="h-3.5 w-3.5" />
                      Daily
                    </div>
                  </SelectItem>
                  <SelectItem value="weekly">
                    <div className="flex items-center gap-2">
                      <CalendarDays className="h-3.5 w-3.5" />
                      Weekly
                    </div>
                  </SelectItem>
                  <SelectItem value="monthly">
                    <div className="flex items-center gap-2">
                      <Calendar className="h-3.5 w-3.5" />
                      Monthly
                    </div>
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Format */}
            <div className="space-y-2">
              <Label className="text-xs font-medium">Output Format</Label>
              <Select
                value={scheduleForm.format}
                onValueChange={(val) =>
                  setScheduleForm((f) => ({ ...f, format: val as 'pdf' | 'excel' | 'csv' }))
                }
              >
                <SelectTrigger className="text-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="pdf">
                    <div className="flex items-center gap-2">
                      <File className="h-3.5 w-3.5" />
                      PDF
                    </div>
                  </SelectItem>
                  <SelectItem value="excel">
                    <div className="flex items-center gap-2">
                      <FileSpreadsheet className="h-3.5 w-3.5" />
                      Excel (.xlsx)
                    </div>
                  </SelectItem>
                  <SelectItem value="csv">
                    <div className="flex items-center gap-2">
                      <FileText className="h-3.5 w-3.5" />
                      CSV
                    </div>
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Recipients */}
            <div className="space-y-2">
              <Label className="text-xs font-medium">
                Email Recipients <span className="text-slate-400">(comma-separated)</span>
              </Label>
              <div className="relative">
                <Mail className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                <Input
                  placeholder="user@example.com, admin@example.com"
                  value={scheduleForm.recipients}
                  onChange={(e) =>
                    setScheduleForm((f) => ({ ...f, recipients: e.target.value }))
                  }
                  className="pl-10 text-sm"
                />
              </div>
            </div>
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setShowScheduleDialog(false);
                resetScheduleForm();
              }}
            >
              Cancel
            </Button>
            <Button
              onClick={handleSchedule}
              disabled={scheduleMutation.isPending || !scheduleForm.recipients.trim()}
            >
              {scheduleMutation.isPending ? (
                <>
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                  Scheduling...
                </>
              ) : (
                <>
                  <Repeat className="mr-1 h-4 w-4" />
                  Create Schedule
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
