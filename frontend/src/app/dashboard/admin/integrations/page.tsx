'use client';

import { useState, useEffect, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Webhook,
  Plus,
  Search,
  Loader2,
  MoreHorizontal,
  Pencil,
  Trash2,
  TestTube2,
  Power,
  PowerOff,
  AlertCircle,
  CheckCircle2,
  X,
  Copy,
  RefreshCw,
  ExternalLink,
  ChevronDown,
  ChevronRight,
  Settings,
  Hash,
  Link2,
  Shield,
  Key,
  Globe,
  Zap,
  MessageSquare,
  ArrowRight,
  Clock,
  Eye,
  Info,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Separator } from '@/components/ui/separator';
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { apiClient } from '@/lib/api-client';
import { cn, formatDate } from '@/lib/utils';

// ── Types ───────────────────────────────────────────────────────────────

interface WebhookEndpoint {
  id: string;
  org_id: string;
  name: string;
  url: string;
  secret: string;
  events: string[];
  headers: Record<string, string> | null;
  is_active: boolean;
  retry_policy: { max_retries: number; backoff_seconds: number } | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

interface WebhookDelivery {
  id: string;
  webhook_id: string;
  event_type: string;
  payload: Record<string, unknown>;
  status: 'pending' | 'success' | 'failed' | 'retrying';
  status_code: number | null;
  response_body: string | null;
  attempt_number: number;
  next_retry_at: string | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
}

interface IntegrationConfig {
  id: string;
  org_id: string;
  integration_type: string;
  name: string;
  config: Record<string, string>;
  is_active: boolean;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

interface EventTypeInfo {
  event_type: string;
  description: string;
  example_payload: Record<string, unknown>;
  subscriber_count: number;
}

interface PaginatedResponse<T> {
  status: string;
  data: T[];
  meta: { page: number; page_size: number; total: number; total_pages: number };
}

// ── Constants ───────────────────────────────────────────────────────────

const EVENT_TYPES = [
  'alert.created',
  'alert.resolved',
  'alert.escalated',
  'camera.online',
  'camera.offline',
  'face.recognized',
  'face.unknown',
  'vehicle.entry',
  'vehicle.exit',
  'anomaly.detected',
  'system.health',
];

const EVENT_TYPE_LABELS: Record<string, string> = {
  'alert.created': 'Alert Created',
  'alert.resolved': 'Alert Resolved',
  'alert.escalated': 'Alert Escalated',
  'camera.online': 'Camera Online',
  'camera.offline': 'Camera Offline',
  'face.recognized': 'Face Recognized',
  'face.unknown': 'Unknown Face',
  'vehicle.entry': 'Vehicle Entry',
  'vehicle.exit': 'Vehicle Exit',
  'anomaly.detected': 'Anomaly Detected',
  'system.health': 'System Health',
};

const EVENT_TYPE_COLORS: Record<string, string> = {
  'alert.created': 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  'alert.resolved': 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  'alert.escalated': 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400',
  'camera.online': 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400',
  'camera.offline': 'bg-gray-100 text-gray-700 dark:bg-gray-900/30 dark:text-gray-400',
  'face.recognized': 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
  'face.unknown': 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
  'vehicle.entry': 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-400',
  'vehicle.exit': 'bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-400',
  'anomaly.detected': 'bg-pink-100 text-pink-700 dark:bg-pink-900/30 dark:text-pink-400',
  'system.health': 'bg-cyan-100 text-cyan-700 dark:bg-cyan-900/30 dark:text-cyan-400',
};

const DELIVERY_STATUS_COLORS: Record<string, string> = {
  pending: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
  success: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  failed: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  retrying: 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400',
};

const INTEGRATION_TYPES = [
  {
    value: 'slack',
    label: 'Slack',
    icon: MessageSquare,
    color: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400',
    description: 'Send alerts and events to Slack channels via incoming webhooks.',
  },
  {
    value: 'teams',
    label: 'Microsoft Teams',
    icon: Globe,
    color: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
    description: 'Send adaptive cards to Teams channels via incoming webhooks.',
  },
  {
    value: 'pagerduty',
    label: 'PagerDuty',
    icon: Zap,
    color: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
    description: 'Create and resolve incidents in PagerDuty from alerts.',
  },
  {
    value: 'jira',
    label: 'Jira',
    icon: Shield,
    color: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-400',
    description: 'Create Jira issues from VisionAI alerts for tracking.',
  },
];

const getIntegrationTypeConfig = (type: string) =>
  INTEGRATION_TYPES.find((t) => t.value === type) || INTEGRATION_TYPES[0];

// ── Toast helper ────────────────────────────────────────────────────────

function useToast() {
  const [toast, setToast] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(null), 4000);
      return () => clearTimeout(timer);
    }
  }, [toast]);

  return { toast, showToast: setToast };
}

// ── Main Component ──────────────────────────────────────────────────────

export default function IntegrationsPage() {
  const queryClient = useQueryClient();
  const { toast, showToast } = useToast();

  // -- Tab state
  const [activeTab, setActiveTab] = useState('webhooks');

  // -- Webhook state
  const [webhookSearch, setWebhookSearch] = useState('');
  const [addWebhookOpen, setAddWebhookOpen] = useState(false);
  const [editWebhookOpen, setEditWebhookOpen] = useState(false);
  const [editingWebhook, setEditingWebhook] = useState<WebhookEndpoint | null>(null);
  const [deliveryLogWebhook, setDeliveryLogWebhook] = useState<WebhookEndpoint | null>(null);
  const [deliveryLogOpen, setDeliveryLogOpen] = useState(false);

  // Webhook form
  const [whName, setWhName] = useState('');
  const [whUrl, setWhUrl] = useState('');
  const [whEvents, setWhEvents] = useState<string[]>([]);
  const [whHeaders, setWhHeaders] = useState<{ key: string; value: string }[]>([{ key: '', value: '' }]);
  const [whMaxRetries, setWhMaxRetries] = useState(5);
  const [whBackoff, setWhBackoff] = useState(30);

  // -- Integration state
  const [addIntegrationOpen, setAddIntegrationOpen] = useState(false);
  const [editIntegrationOpen, setEditIntegrationOpen] = useState(false);
  const [editingIntegration, setEditingIntegration] = useState<IntegrationConfig | null>(null);
  const [integrationType, setIntegrationType] = useState('slack');
  const [integrationName, setIntegrationName] = useState('');
  const [integrationConfig, setIntegrationConfig] = useState<Record<string, string>>({});

  // -- Event type state
  const [expandedEventType, setExpandedEventType] = useState<string | null>(null);

  // ── Queries ───────────────────────────────────────────────────────────

  const { data: webhooksData, isLoading: webhooksLoading } = useQuery<PaginatedResponse<WebhookEndpoint>>({
    queryKey: ['webhooks'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/integrations/webhooks?page_size=100');
      return res.data;
    },
  });
  const webhooks = webhooksData?.data || [];

  const { data: integrationsData, isLoading: integrationsLoading } = useQuery<PaginatedResponse<IntegrationConfig>>({
    queryKey: ['integration-configs'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/integrations/configs?page_size=100');
      return res.data;
    },
  });
  const integrations = integrationsData?.data || [];

  const { data: eventTypesData, isLoading: eventTypesLoading } = useQuery<{ status: string; data: EventTypeInfo[] }>({
    queryKey: ['event-types'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/integrations/event-types');
      return res.data;
    },
  });
  const eventTypes = eventTypesData?.data || [];

  const { data: deliveriesData, isLoading: deliveriesLoading } = useQuery<PaginatedResponse<WebhookDelivery>>({
    queryKey: ['webhook-deliveries', deliveryLogWebhook?.id],
    queryFn: async () => {
      if (!deliveryLogWebhook) return { status: 'success', data: [], meta: { page: 1, page_size: 20, total: 0, total_pages: 0 } };
      const res = await apiClient.get(`/api/v1/integrations/webhooks/${deliveryLogWebhook.id}/deliveries?page_size=50`);
      return res.data;
    },
    enabled: !!deliveryLogWebhook,
  });
  const deliveries = deliveriesData?.data || [];

  // ── Webhook Mutations ─────────────────────────────────────────────────

  const addWebhookMutation = useMutation({
    mutationFn: async (data: Record<string, unknown>) => {
      const res = await apiClient.post('/api/v1/integrations/webhooks', data);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['webhooks'] });
      queryClient.invalidateQueries({ queryKey: ['event-types'] });
      setAddWebhookOpen(false);
      resetWebhookForm();
      showToast({ type: 'success', message: 'Webhook created successfully.' });
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Failed to create webhook.' });
    },
  });

  const editWebhookMutation = useMutation({
    mutationFn: async ({ id, data }: { id: string; data: Record<string, unknown> }) => {
      const res = await apiClient.put(`/api/v1/integrations/webhooks/${id}`, data);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['webhooks'] });
      queryClient.invalidateQueries({ queryKey: ['event-types'] });
      setEditWebhookOpen(false);
      setEditingWebhook(null);
      resetWebhookForm();
      showToast({ type: 'success', message: 'Webhook updated successfully.' });
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Failed to update webhook.' });
    },
  });

  const deleteWebhookMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/api/v1/integrations/webhooks/${id}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['webhooks'] });
      queryClient.invalidateQueries({ queryKey: ['event-types'] });
      showToast({ type: 'success', message: 'Webhook deleted.' });
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Failed to delete webhook.' });
    },
  });

  const toggleWebhookMutation = useMutation({
    mutationFn: async ({ id, is_active }: { id: string; is_active: boolean }) => {
      await apiClient.put(`/api/v1/integrations/webhooks/${id}`, { is_active });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['webhooks'] });
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Failed to toggle webhook.' });
    },
  });

  const testWebhookMutation = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiClient.post(`/api/v1/integrations/webhooks/${id}/test`, {
        event_type: 'alert.created',
      });
      return res.data;
    },
    onSuccess: (data: any) => {
      const status = data?.data?.status;
      if (status === 'success') {
        showToast({ type: 'success', message: 'Test webhook delivered successfully.' });
      } else {
        showToast({ type: 'error', message: `Test delivery failed: ${data?.data?.error_message || 'Unknown error'}` });
      }
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Failed to send test webhook.' });
    },
  });

  const retryDeliveryMutation = useMutation({
    mutationFn: async ({ webhookId, deliveryId }: { webhookId: string; deliveryId: string }) => {
      const res = await apiClient.post(
        `/api/v1/integrations/webhooks/${webhookId}/deliveries/${deliveryId}/retry`
      );
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['webhook-deliveries'] });
      showToast({ type: 'success', message: 'Delivery retry initiated.' });
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Failed to retry delivery.' });
    },
  });

  // ── Integration Mutations ─────────────────────────────────────────────

  const addIntegrationMutation = useMutation({
    mutationFn: async (data: Record<string, unknown>) => {
      const res = await apiClient.post('/api/v1/integrations/configs', data);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['integration-configs'] });
      setAddIntegrationOpen(false);
      resetIntegrationForm();
      showToast({ type: 'success', message: 'Integration created successfully.' });
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Failed to create integration.' });
    },
  });

  const editIntegrationMutation = useMutation({
    mutationFn: async ({ id, data }: { id: string; data: Record<string, unknown> }) => {
      const res = await apiClient.put(`/api/v1/integrations/configs/${id}`, data);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['integration-configs'] });
      setEditIntegrationOpen(false);
      setEditingIntegration(null);
      resetIntegrationForm();
      showToast({ type: 'success', message: 'Integration updated successfully.' });
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Failed to update integration.' });
    },
  });

  const deleteIntegrationMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/api/v1/integrations/configs/${id}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['integration-configs'] });
      showToast({ type: 'success', message: 'Integration deleted.' });
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Failed to delete integration.' });
    },
  });

  const toggleIntegrationMutation = useMutation({
    mutationFn: async ({ id, is_active }: { id: string; is_active: boolean }) => {
      await apiClient.put(`/api/v1/integrations/configs/${id}`, { is_active });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['integration-configs'] });
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Failed to toggle integration.' });
    },
  });

  const testIntegrationMutation = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiClient.post(`/api/v1/integrations/configs/${id}/test`);
      return res.data;
    },
    onSuccess: (data: any) => {
      if (data?.data?.success) {
        showToast({ type: 'success', message: 'Integration test passed.' });
      } else {
        showToast({ type: 'error', message: `Test failed: ${data?.data?.error || 'Unknown error'}` });
      }
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Integration test failed.' });
    },
  });

  // ── Form Helpers ──────────────────────────────────────────────────────

  const resetWebhookForm = useCallback(() => {
    setWhName('');
    setWhUrl('');
    setWhEvents([]);
    setWhHeaders([{ key: '', value: '' }]);
    setWhMaxRetries(5);
    setWhBackoff(30);
  }, []);

  const resetIntegrationForm = useCallback(() => {
    setIntegrationType('slack');
    setIntegrationName('');
    setIntegrationConfig({});
  }, []);

  const openEditWebhook = (webhook: WebhookEndpoint) => {
    setEditingWebhook(webhook);
    setWhName(webhook.name);
    setWhUrl(webhook.url);
    setWhEvents(webhook.events || []);
    const headers = webhook.headers
      ? Object.entries(webhook.headers).map(([key, value]) => ({ key, value }))
      : [];
    if (headers.length === 0) headers.push({ key: '', value: '' });
    setWhHeaders(headers);
    setWhMaxRetries(webhook.retry_policy?.max_retries ?? 5);
    setWhBackoff(webhook.retry_policy?.backoff_seconds ?? 30);
    setEditWebhookOpen(true);
  };

  const openEditIntegration = (integration: IntegrationConfig) => {
    setEditingIntegration(integration);
    setIntegrationType(integration.integration_type);
    setIntegrationName(integration.name);
    setIntegrationConfig(integration.config || {});
    setEditIntegrationOpen(true);
  };

  const handleWebhookSubmit = (isEdit: boolean) => {
    if (!whName.trim() || !whUrl.trim() || whEvents.length === 0) {
      showToast({ type: 'error', message: 'Name, URL, and at least one event are required.' });
      return;
    }

    const headersObj: Record<string, string> = {};
    whHeaders.forEach(({ key, value }) => {
      if (key.trim()) headersObj[key.trim()] = value;
    });

    const data: Record<string, unknown> = {
      name: whName.trim(),
      url: whUrl.trim(),
      events: whEvents,
      headers: Object.keys(headersObj).length > 0 ? headersObj : null,
      retry_policy: { max_retries: whMaxRetries, backoff_seconds: whBackoff },
    };

    if (isEdit && editingWebhook) {
      editWebhookMutation.mutate({ id: editingWebhook.id, data });
    } else {
      addWebhookMutation.mutate(data);
    }
  };

  const handleIntegrationSubmit = (isEdit: boolean) => {
    if (!integrationName.trim()) {
      showToast({ type: 'error', message: 'Integration name is required.' });
      return;
    }

    const data: Record<string, unknown> = {
      integration_type: integrationType,
      name: integrationName.trim(),
      config: integrationConfig,
    };

    if (isEdit && editingIntegration) {
      editIntegrationMutation.mutate({ id: editingIntegration.id, data: { name: data.name as string, config: data.config } });
    } else {
      addIntegrationMutation.mutate(data);
    }
  };

  const toggleEventType = (eventType: string) => {
    setWhEvents((prev) =>
      prev.includes(eventType) ? prev.filter((e) => e !== eventType) : [...prev, eventType]
    );
  };

  const addHeaderRow = () => {
    setWhHeaders((prev) => [...prev, { key: '', value: '' }]);
  };

  const removeHeaderRow = (index: number) => {
    setWhHeaders((prev) => prev.filter((_, i) => i !== index));
  };

  const updateHeader = (index: number, field: 'key' | 'value', value: string) => {
    setWhHeaders((prev) => prev.map((h, i) => (i === index ? { ...h, [field]: value } : h)));
  };

  const updateIntegrationConfigField = (field: string, value: string) => {
    setIntegrationConfig((prev) => ({ ...prev, [field]: value }));
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text).then(() => {
      showToast({ type: 'success', message: 'Copied to clipboard.' });
    });
  };

  const truncateUrl = (url: string, max: number = 50) => {
    if (url.length <= max) return url;
    return url.slice(0, max) + '...';
  };

  // ── Filtered webhooks ─────────────────────────────────────────────────

  const filteredWebhooks = webhooks.filter(
    (w) =>
      w.name.toLowerCase().includes(webhookSearch.toLowerCase()) ||
      w.url.toLowerCase().includes(webhookSearch.toLowerCase())
  );

  // ── Integration config form renderer ──────────────────────────────────

  const renderIntegrationConfigForm = () => {
    switch (integrationType) {
      case 'slack':
        return (
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label className="text-xs">Webhook URL</Label>
              <Input
                placeholder="https://hooks.slack.com/services/T00/B00/xxxx"
                value={integrationConfig.webhook_url || ''}
                onChange={(e) => updateIntegrationConfigField('webhook_url', e.target.value)}
              />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label className="text-xs">Channel</Label>
                <Input
                  placeholder="#alerts"
                  value={integrationConfig.channel || ''}
                  onChange={(e) => updateIntegrationConfigField('channel', e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">Bot Username</Label>
                <Input
                  placeholder="VisionAI Bot"
                  value={integrationConfig.username || ''}
                  onChange={(e) => updateIntegrationConfigField('username', e.target.value)}
                />
              </div>
            </div>
          </div>
        );
      case 'teams':
        return (
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label className="text-xs">Incoming Webhook URL</Label>
              <Input
                placeholder="https://outlook.office.com/webhook/..."
                value={integrationConfig.webhook_url || ''}
                onChange={(e) => updateIntegrationConfigField('webhook_url', e.target.value)}
              />
            </div>
          </div>
        );
      case 'pagerduty':
        return (
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label className="text-xs">Integration / Routing Key</Label>
              <Input
                placeholder="PagerDuty Events API v2 routing key"
                value={integrationConfig.routing_key || ''}
                onChange={(e) => updateIntegrationConfigField('routing_key', e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Service ID (optional)</Label>
              <Input
                placeholder="PXXXXXX"
                value={integrationConfig.service_id || ''}
                onChange={(e) => updateIntegrationConfigField('service_id', e.target.value)}
              />
            </div>
          </div>
        );
      case 'jira':
        return (
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label className="text-xs">Jira Base URL</Label>
              <Input
                placeholder="https://yourcompany.atlassian.net"
                value={integrationConfig.base_url || ''}
                onChange={(e) => updateIntegrationConfigField('base_url', e.target.value)}
              />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label className="text-xs">Email</Label>
                <Input
                  placeholder="user@company.com"
                  value={integrationConfig.email || ''}
                  onChange={(e) => updateIntegrationConfigField('email', e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">API Token</Label>
                <Input
                  type="password"
                  placeholder="Jira API token"
                  value={integrationConfig.api_token || ''}
                  onChange={(e) => updateIntegrationConfigField('api_token', e.target.value)}
                />
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label className="text-xs">Project Key</Label>
                <Input
                  placeholder="PROJ"
                  value={integrationConfig.project_key || ''}
                  onChange={(e) => updateIntegrationConfigField('project_key', e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">Issue Type</Label>
                <Input
                  placeholder="Bug"
                  value={integrationConfig.issue_type || ''}
                  onChange={(e) => updateIntegrationConfigField('issue_type', e.target.value)}
                />
              </div>
            </div>
          </div>
        );
      default:
        return (
          <div className="space-y-1.5">
            <Label className="text-xs">Configuration (JSON)</Label>
            <Textarea
              placeholder='{"key": "value"}'
              value={JSON.stringify(integrationConfig, null, 2)}
              onChange={(e) => {
                try {
                  setIntegrationConfig(JSON.parse(e.target.value));
                } catch {
                  // Allow typing invalid JSON temporarily
                }
              }}
              rows={6}
            />
          </div>
        );
    }
  };

  // ── Render ────────────────────────────────────────────────────────────

  return (
    <div className="space-y-6 p-6">
      {/* Toast */}
      {toast && (
        <div
          className={cn(
            'fixed top-4 right-4 z-[100] flex items-center gap-2 rounded-lg px-4 py-3 shadow-lg transition-all',
            toast.type === 'success'
              ? 'bg-green-50 text-green-800 border border-green-200 dark:bg-green-900/50 dark:text-green-200 dark:border-green-800'
              : 'bg-red-50 text-red-800 border border-red-200 dark:bg-red-900/50 dark:text-red-200 dark:border-red-800'
          )}
        >
          {toast.type === 'success' ? (
            <CheckCircle2 className="h-4 w-4 flex-shrink-0" />
          ) : (
            <AlertCircle className="h-4 w-4 flex-shrink-0" />
          )}
          <span className="text-sm font-medium">{toast.message}</span>
          <button onClick={() => showToast(null)} className="ml-2">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {/* Page Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Integrations</h1>
        <p className="text-muted-foreground mt-1">
          Manage webhooks, third-party integrations, and event subscriptions.
        </p>
      </div>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="grid w-full max-w-md grid-cols-3">
          <TabsTrigger value="webhooks" className="gap-1.5">
            <Webhook className="h-4 w-4" />
            Webhooks
          </TabsTrigger>
          <TabsTrigger value="integrations" className="gap-1.5">
            <Link2 className="h-4 w-4" />
            Integrations
          </TabsTrigger>
          <TabsTrigger value="events" className="gap-1.5">
            <Zap className="h-4 w-4" />
            Event Types
          </TabsTrigger>
        </TabsList>

        {/* ── Webhooks Tab ─────────────────────────────────────────────── */}
        <TabsContent value="webhooks" className="mt-6 space-y-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="relative max-w-sm flex-1">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder="Search webhooks..."
                value={webhookSearch}
                onChange={(e) => setWebhookSearch(e.target.value)}
                className="pl-9"
              />
            </div>
            <Button onClick={() => { resetWebhookForm(); setAddWebhookOpen(true); }}>
              <Plus className="mr-1.5 h-4 w-4" />
              Add Webhook
            </Button>
          </div>

          {webhooksLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : filteredWebhooks.length === 0 ? (
            <Card>
              <CardContent className="flex flex-col items-center justify-center py-12 text-center">
                <Webhook className="h-12 w-12 text-muted-foreground/50 mb-3" />
                <h3 className="text-lg font-medium">No webhooks configured</h3>
                <p className="text-muted-foreground text-sm mt-1 max-w-sm">
                  Create a webhook to receive real-time event notifications via HTTP POST.
                </p>
              </CardContent>
            </Card>
          ) : (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>URL</TableHead>
                    <TableHead>Events</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredWebhooks.map((webhook) => (
                    <TableRow key={webhook.id}>
                      <TableCell className="font-medium">{webhook.name}</TableCell>
                      <TableCell>
                        <span className="text-xs text-muted-foreground font-mono">
                          {truncateUrl(webhook.url, 40)}
                        </span>
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-wrap gap-1">
                          {webhook.events.slice(0, 3).map((event) => (
                            <Badge
                              key={event}
                              variant="secondary"
                              className={cn('text-[10px] px-1.5 py-0', EVENT_TYPE_COLORS[event])}
                            >
                              {EVENT_TYPE_LABELS[event] || event}
                            </Badge>
                          ))}
                          {webhook.events.length > 3 && (
                            <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                              +{webhook.events.length - 3} more
                            </Badge>
                          )}
                        </div>
                      </TableCell>
                      <TableCell>
                        <Switch
                          checked={webhook.is_active}
                          onCheckedChange={(checked) =>
                            toggleWebhookMutation.mutate({ id: webhook.id, is_active: checked })
                          }
                        />
                      </TableCell>
                      <TableCell className="text-right">
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="icon" className="h-8 w-8">
                              <MoreHorizontal className="h-4 w-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem onClick={() => openEditWebhook(webhook)}>
                              <Pencil className="mr-2 h-3.5 w-3.5" /> Edit
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              onClick={() => testWebhookMutation.mutate(webhook.id)}
                              disabled={testWebhookMutation.isPending}
                            >
                              <TestTube2 className="mr-2 h-3.5 w-3.5" /> Test
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              onClick={() => {
                                setDeliveryLogWebhook(webhook);
                                setDeliveryLogOpen(true);
                              }}
                            >
                              <Eye className="mr-2 h-3.5 w-3.5" /> Delivery Log
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => copyToClipboard(webhook.secret)}>
                              <Key className="mr-2 h-3.5 w-3.5" /> Copy Secret
                            </DropdownMenuItem>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem
                              className="text-red-600 dark:text-red-400"
                              onClick={() => deleteWebhookMutation.mutate(webhook.id)}
                            >
                              <Trash2 className="mr-2 h-3.5 w-3.5" /> Delete
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </TabsContent>

        {/* ── Integrations Tab ──────────────────────────────────────────── */}
        <TabsContent value="integrations" className="mt-6 space-y-4">
          <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground">
              Connect VisionAI with your existing tools and services.
            </p>
            <Button onClick={() => { resetIntegrationForm(); setAddIntegrationOpen(true); }}>
              <Plus className="mr-1.5 h-4 w-4" />
              Add Integration
            </Button>
          </div>

          {integrationsLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : integrations.length === 0 ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {INTEGRATION_TYPES.map((type) => {
                const Icon = type.icon;
                return (
                  <Card key={type.value} className="hover:shadow-md transition-shadow cursor-pointer" onClick={() => {
                    resetIntegrationForm();
                    setIntegrationType(type.value);
                    setAddIntegrationOpen(true);
                  }}>
                    <CardContent className="flex flex-col items-center justify-center py-8 text-center">
                      <div className={cn('rounded-full p-3 mb-3', type.color)}>
                        <Icon className="h-6 w-6" />
                      </div>
                      <h3 className="font-medium">{type.label}</h3>
                      <p className="text-xs text-muted-foreground mt-1 max-w-[200px]">
                        {type.description}
                      </p>
                      <Button variant="outline" size="sm" className="mt-4">
                        <Plus className="mr-1 h-3 w-3" /> Connect
                      </Button>
                    </CardContent>
                  </Card>
                );
              })}
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {integrations.map((integration) => {
                const typeConfig = getIntegrationTypeConfig(integration.integration_type);
                const Icon = typeConfig.icon;
                return (
                  <Card key={integration.id}>
                    <CardHeader className="pb-3">
                      <div className="flex items-start justify-between">
                        <div className="flex items-center gap-3">
                          <div className={cn('rounded-lg p-2', typeConfig.color)}>
                            <Icon className="h-5 w-5" />
                          </div>
                          <div>
                            <CardTitle className="text-base">{integration.name}</CardTitle>
                            <CardDescription className="text-xs capitalize">
                              {typeConfig.label}
                            </CardDescription>
                          </div>
                        </div>
                        <Switch
                          checked={integration.is_active}
                          onCheckedChange={(checked) =>
                            toggleIntegrationMutation.mutate({ id: integration.id, is_active: checked })
                          }
                        />
                      </div>
                    </CardHeader>
                    <CardContent className="pt-0">
                      <div className="flex items-center gap-2 text-xs text-muted-foreground mb-3">
                        <Clock className="h-3 w-3" />
                        Created {formatDate(integration.created_at)}
                      </div>
                      <div className="flex gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => openEditIntegration(integration)}
                        >
                          <Settings className="mr-1 h-3 w-3" /> Configure
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => testIntegrationMutation.mutate(integration.id)}
                          disabled={testIntegrationMutation.isPending}
                        >
                          {testIntegrationMutation.isPending ? (
                            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                          ) : (
                            <TestTube2 className="mr-1 h-3 w-3" />
                          )}
                          Test
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-red-600 dark:text-red-400 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-900/20"
                          onClick={() => deleteIntegrationMutation.mutate(integration.id)}
                        >
                          <Trash2 className="h-3 w-3" />
                        </Button>
                      </div>
                    </CardContent>
                  </Card>
                );
              })}
              {/* Add new card */}
              <Card className="border-dashed hover:shadow-md transition-shadow cursor-pointer" onClick={() => {
                resetIntegrationForm();
                setAddIntegrationOpen(true);
              }}>
                <CardContent className="flex flex-col items-center justify-center py-12 text-center">
                  <Plus className="h-8 w-8 text-muted-foreground/50 mb-2" />
                  <p className="text-sm text-muted-foreground">Add Integration</p>
                </CardContent>
              </Card>
            </div>
          )}
        </TabsContent>

        {/* ── Event Types Tab ───────────────────────────────────────────── */}
        <TabsContent value="events" className="mt-6 space-y-4">
          <p className="text-sm text-muted-foreground">
            All available event types that can trigger webhooks and integrations.
          </p>

          {eventTypesLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-8"></TableHead>
                    <TableHead>Event Type</TableHead>
                    <TableHead>Description</TableHead>
                    <TableHead className="text-center">Subscribers</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {eventTypes.map((et) => (
                    <>
                      <TableRow
                        key={et.event_type}
                        className="cursor-pointer hover:bg-muted/50"
                        onClick={() =>
                          setExpandedEventType(
                            expandedEventType === et.event_type ? null : et.event_type
                          )
                        }
                      >
                        <TableCell className="w-8">
                          {expandedEventType === et.event_type ? (
                            <ChevronDown className="h-4 w-4 text-muted-foreground" />
                          ) : (
                            <ChevronRight className="h-4 w-4 text-muted-foreground" />
                          )}
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant="secondary"
                            className={cn('text-xs font-mono', EVENT_TYPE_COLORS[et.event_type])}
                          >
                            {et.event_type}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground">
                          {et.description}
                        </TableCell>
                        <TableCell className="text-center">
                          <Badge variant="outline" className="text-xs">
                            {et.subscriber_count} webhook{et.subscriber_count !== 1 ? 's' : ''}
                          </Badge>
                        </TableCell>
                      </TableRow>
                      {expandedEventType === et.event_type && (
                        <TableRow key={`${et.event_type}-payload`}>
                          <TableCell colSpan={4} className="bg-muted/30">
                            <div className="p-3">
                              <p className="text-xs font-medium text-muted-foreground mb-2">
                                Example Payload:
                              </p>
                              <pre className="text-xs bg-background rounded-md p-3 overflow-x-auto border">
                                {JSON.stringify(et.example_payload, null, 2)}
                              </pre>
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
        </TabsContent>
      </Tabs>

      {/* ── Add / Edit Webhook Dialog ──────────────────────────────────── */}
      <Dialog
        open={addWebhookOpen || editWebhookOpen}
        onOpenChange={(open) => {
          if (!open) {
            setAddWebhookOpen(false);
            setEditWebhookOpen(false);
            setEditingWebhook(null);
          }
        }}
      >
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>
              {editWebhookOpen ? 'Edit Webhook' : 'Add Webhook Endpoint'}
            </DialogTitle>
            <DialogDescription>
              {editWebhookOpen
                ? 'Update webhook configuration and event subscriptions.'
                : 'Register a new HTTP endpoint to receive real-time event notifications.'}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-5 py-2">
            {/* Name & URL */}
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label className="text-xs font-medium">Name</Label>
                <Input
                  placeholder="e.g. Production Alert Webhook"
                  value={whName}
                  onChange={(e) => setWhName(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs font-medium">URL</Label>
                <Input
                  placeholder="https://hooks.example.com/visionai"
                  value={whUrl}
                  onChange={(e) => setWhUrl(e.target.value)}
                />
              </div>
            </div>

            {/* Secret (display only for edit) */}
            {editWebhookOpen && editingWebhook && (
              <div className="space-y-1.5">
                <Label className="text-xs font-medium">Signing Secret</Label>
                <div className="flex gap-2">
                  <Input value={editingWebhook.secret} readOnly className="font-mono text-xs bg-muted" />
                  <Button
                    variant="outline"
                    size="icon"
                    onClick={() => copyToClipboard(editingWebhook.secret)}
                  >
                    <Copy className="h-4 w-4" />
                  </Button>
                </div>
                <p className="text-[10px] text-muted-foreground">
                  Use this secret to verify webhook signatures (X-VisionAI-Signature header).
                </p>
              </div>
            )}

            {/* Event Types */}
            <div className="space-y-2">
              <Label className="text-xs font-medium">Event Subscriptions</Label>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                {EVENT_TYPES.map((event) => {
                  const isSelected = whEvents.includes(event);
                  return (
                    <button
                      key={event}
                      type="button"
                      onClick={() => toggleEventType(event)}
                      className={cn(
                        'flex items-center gap-2 rounded-md border px-3 py-2 text-xs transition-colors text-left',
                        isSelected
                          ? 'border-primary bg-primary/5 text-primary'
                          : 'border-border hover:bg-muted/50 text-muted-foreground'
                      )}
                    >
                      <div
                        className={cn(
                          'h-3 w-3 rounded-sm border flex-shrink-0',
                          isSelected ? 'bg-primary border-primary' : 'border-muted-foreground/40'
                        )}
                      >
                        {isSelected && (
                          <CheckCircle2 className="h-3 w-3 text-primary-foreground" />
                        )}
                      </div>
                      {EVENT_TYPE_LABELS[event] || event}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Custom Headers */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label className="text-xs font-medium">Custom Headers (optional)</Label>
                <Button variant="ghost" size="sm" onClick={addHeaderRow}>
                  <Plus className="mr-1 h-3 w-3" /> Add
                </Button>
              </div>
              {whHeaders.map((header, index) => (
                <div key={index} className="flex gap-2 items-center">
                  <Input
                    placeholder="Header name"
                    value={header.key}
                    onChange={(e) => updateHeader(index, 'key', e.target.value)}
                    className="flex-1 text-xs"
                  />
                  <Input
                    placeholder="Value"
                    value={header.value}
                    onChange={(e) => updateHeader(index, 'value', e.target.value)}
                    className="flex-1 text-xs"
                  />
                  {whHeaders.length > 1 && (
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8 flex-shrink-0"
                      onClick={() => removeHeaderRow(index)}
                    >
                      <X className="h-3 w-3" />
                    </Button>
                  )}
                </div>
              ))}
            </div>

            {/* Retry Policy */}
            <div className="space-y-2">
              <Label className="text-xs font-medium">Retry Policy</Label>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label className="text-[10px] text-muted-foreground">Max Retries</Label>
                  <Input
                    type="number"
                    min={0}
                    max={20}
                    value={whMaxRetries}
                    onChange={(e) => setWhMaxRetries(Number(e.target.value))}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-[10px] text-muted-foreground">
                    Backoff Interval (seconds)
                  </Label>
                  <Input
                    type="number"
                    min={5}
                    max={3600}
                    value={whBackoff}
                    onChange={(e) => setWhBackoff(Number(e.target.value))}
                  />
                </div>
              </div>
              <p className="text-[10px] text-muted-foreground">
                Uses exponential backoff: interval doubles with each retry attempt.
              </p>
            </div>
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setAddWebhookOpen(false);
                setEditWebhookOpen(false);
                setEditingWebhook(null);
              }}
            >
              Cancel
            </Button>
            <Button
              onClick={() => handleWebhookSubmit(editWebhookOpen)}
              disabled={addWebhookMutation.isPending || editWebhookMutation.isPending}
            >
              {(addWebhookMutation.isPending || editWebhookMutation.isPending) && (
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
              )}
              {editWebhookOpen ? 'Update Webhook' : 'Create Webhook'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── Delivery Log Dialog ────────────────────────────────────────── */}
      <Dialog
        open={deliveryLogOpen}
        onOpenChange={(open) => {
          if (!open) {
            setDeliveryLogOpen(false);
            setDeliveryLogWebhook(null);
          }
        }}
      >
        <DialogContent className="max-w-4xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>
              Delivery Log: {deliveryLogWebhook?.name}
            </DialogTitle>
            <DialogDescription>
              Recent webhook delivery attempts and their statuses.
            </DialogDescription>
          </DialogHeader>

          {deliveriesLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : deliveries.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-8 text-center">
              <Info className="h-8 w-8 text-muted-foreground/50 mb-2" />
              <p className="text-sm text-muted-foreground">No deliveries recorded yet.</p>
            </div>
          ) : (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Event</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>HTTP Code</TableHead>
                    <TableHead>Attempt</TableHead>
                    <TableHead>Time</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {deliveries.map((delivery) => (
                    <TableRow key={delivery.id}>
                      <TableCell>
                        <Badge
                          variant="secondary"
                          className={cn('text-[10px]', EVENT_TYPE_COLORS[delivery.event_type])}
                        >
                          {delivery.event_type}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant="secondary"
                          className={cn('text-[10px]', DELIVERY_STATUS_COLORS[delivery.status])}
                        >
                          {delivery.status}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {delivery.status_code ?? '-'}
                      </TableCell>
                      <TableCell className="text-xs">{delivery.attempt_number}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {formatDate(delivery.created_at)}
                      </TableCell>
                      <TableCell className="text-right">
                        {(delivery.status === 'failed' || delivery.status === 'retrying') &&
                          deliveryLogWebhook && (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() =>
                                retryDeliveryMutation.mutate({
                                  webhookId: deliveryLogWebhook.id,
                                  deliveryId: delivery.id,
                                })
                              }
                              disabled={retryDeliveryMutation.isPending}
                            >
                              <RefreshCw className="mr-1 h-3 w-3" /> Retry
                            </Button>
                          )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* ── Add / Edit Integration Dialog ──────────────────────────────── */}
      <Dialog
        open={addIntegrationOpen || editIntegrationOpen}
        onOpenChange={(open) => {
          if (!open) {
            setAddIntegrationOpen(false);
            setEditIntegrationOpen(false);
            setEditingIntegration(null);
          }
        }}
      >
        <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>
              {editIntegrationOpen ? 'Edit Integration' : 'Add Integration'}
            </DialogTitle>
            <DialogDescription>
              {editIntegrationOpen
                ? 'Update the integration configuration.'
                : 'Connect a third-party service to receive VisionAI events.'}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-2">
            {/* Type selector (only for add) */}
            {!editIntegrationOpen && (
              <div className="space-y-1.5">
                <Label className="text-xs font-medium">Integration Type</Label>
                <Select value={integrationType} onValueChange={(v) => { setIntegrationType(v); setIntegrationConfig({}); }}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {INTEGRATION_TYPES.map((type) => (
                      <SelectItem key={type.value} value={type.value}>
                        <div className="flex items-center gap-2">
                          <type.icon className="h-4 w-4" />
                          {type.label}
                        </div>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}

            <div className="space-y-1.5">
              <Label className="text-xs font-medium">Name</Label>
              <Input
                placeholder="e.g. Production Slack Channel"
                value={integrationName}
                onChange={(e) => setIntegrationName(e.target.value)}
              />
            </div>

            <Separator />

            <div>
              <Label className="text-xs font-medium mb-3 block">Configuration</Label>
              {renderIntegrationConfigForm()}
            </div>
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setAddIntegrationOpen(false);
                setEditIntegrationOpen(false);
                setEditingIntegration(null);
              }}
            >
              Cancel
            </Button>
            <Button
              onClick={() => handleIntegrationSubmit(editIntegrationOpen)}
              disabled={addIntegrationMutation.isPending || editIntegrationMutation.isPending}
            >
              {(addIntegrationMutation.isPending || editIntegrationMutation.isPending) && (
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
              )}
              {editIntegrationOpen ? 'Update Integration' : 'Create Integration'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
