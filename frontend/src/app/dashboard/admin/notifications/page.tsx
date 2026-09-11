'use client';

import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Bell,
  Plus,
  Search,
  Loader2,
  Mail,
  MessageSquare,
  Phone,
  Send,
  Webhook,
  MoreHorizontal,
  Pencil,
  Trash2,
  TestTube2,
  Power,
  PowerOff,
  AlertCircle,
  CheckCircle2,
  X,
  Settings,
  Hash,
  Link2,
  Shield,
  Key,
  Globe,
  Users,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Separator } from '@/components/ui/separator';
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

// ---------- Types ----------

type ChannelType = 'email' | 'sms' | 'whatsapp' | 'telegram' | 'webhook';

interface NotificationChannel {
  id: string;
  name: string;
  type: ChannelType;
  enabled: boolean;
  config: Record<string, string>;
  recipients: string[];
  created_at: string;
  updated_at: string;
}

interface AlertTypePreference {
  alert_type: string;
  channel_ids: string[];
}

interface EmailConfig {
  smtp_server: string;
  smtp_port: string;
  smtp_username: string;
  smtp_password: string;
  from_address: string;
}

interface SmsConfig {
  twilio_sid: string;
  twilio_auth_token: string;
  from_number: string;
}

interface WhatsAppConfig {
  twilio_sid: string;
  twilio_auth_token: string;
  from_number: string;
}

interface TelegramConfig {
  bot_token: string;
  chat_id: string;
}

interface WebhookConfig {
  url: string;
  secret: string;
  headers: string;
}

// ---------- Constants ----------

const CHANNEL_TYPES: { value: ChannelType; label: string; icon: React.ElementType; color: string }[] = [
  { value: 'email', label: 'Email', icon: Mail, color: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400' },
  { value: 'sms', label: 'SMS', icon: Phone, color: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' },
  { value: 'whatsapp', label: 'WhatsApp', icon: MessageSquare, color: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400' },
  { value: 'telegram', label: 'Telegram', icon: Send, color: 'bg-cyan-100 text-cyan-700 dark:bg-cyan-900/30 dark:text-cyan-400' },
  { value: 'webhook', label: 'Webhook', icon: Webhook, color: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400' },
];

const ALERT_TYPES = [
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
  'system_error',
  'camera_offline',
];

const getChannelTypeConfig = (type: ChannelType) =>
  CHANNEL_TYPES.find((ct) => ct.value === type) || CHANNEL_TYPES[0];

// ---------- Toast helper ----------

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

// ---------- Config Form Defaults ----------

function getDefaultConfig(type: ChannelType): Record<string, string> {
  switch (type) {
    case 'email':
      return { smtp_server: '', smtp_port: '587', smtp_username: '', smtp_password: '', from_address: '' };
    case 'sms':
      return { twilio_sid: '', twilio_auth_token: '', from_number: '' };
    case 'whatsapp':
      return { twilio_sid: '', twilio_auth_token: '', from_number: '' };
    case 'telegram':
      return { bot_token: '', chat_id: '' };
    case 'webhook':
      return { url: '', secret: '', headers: '{}' };
    default:
      return {};
  }
}

// ---------- Component ----------

export default function NotificationsPage() {
  const queryClient = useQueryClient();
  const { toast, showToast } = useToast();

  const [search, setSearch] = useState('');
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [editingChannel, setEditingChannel] = useState<NotificationChannel | null>(null);

  // Channel form state
  const [channelName, setChannelName] = useState('');
  const [channelType, setChannelType] = useState<ChannelType>('email');
  const [channelConfig, setChannelConfig] = useState<Record<string, string>>(getDefaultConfig('email'));
  const [channelRecipients, setChannelRecipients] = useState('');

  // ----- Queries -----

  const { data: channelsData, isLoading: channelsLoading } = useQuery<NotificationChannel[]>({
    queryKey: ['notification-channels'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/admin/notification-channels');
      return (res.data.items || res.data) as NotificationChannel[];
    },
  });

  const channels = channelsData || [];
  const filteredChannels = channels.filter(
    (ch) =>
      ch.name.toLowerCase().includes(search.toLowerCase()) ||
      ch.type.toLowerCase().includes(search.toLowerCase())
  );

  const { data: preferencesData, isLoading: preferencesLoading } = useQuery<AlertTypePreference[]>({
    queryKey: ['notification-preferences'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/admin/notification-preferences');
      return (res.data.items || res.data) as AlertTypePreference[];
    },
  });

  const preferences = preferencesData || [];

  // ----- Mutations -----

  const addChannelMutation = useMutation({
    mutationFn: async (data: { name: string; type: ChannelType; config: Record<string, string>; recipients: string[] }) => {
      const res = await apiClient.post('/api/v1/admin/notification-channels', data);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notification-channels'] });
      setAddDialogOpen(false);
      resetForm();
      showToast({ type: 'success', message: 'Channel created successfully' });
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Failed to create channel' });
    },
  });

  const editChannelMutation = useMutation({
    mutationFn: async ({ id, data }: { id: string; data: Partial<NotificationChannel> }) => {
      const res = await apiClient.put(`/api/v1/admin/notification-channels/${id}`, data);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notification-channels'] });
      setEditDialogOpen(false);
      setEditingChannel(null);
      resetForm();
      showToast({ type: 'success', message: 'Channel updated successfully' });
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Failed to update channel' });
    },
  });

  const deleteChannelMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/api/v1/admin/notification-channels/${id}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notification-channels'] });
      showToast({ type: 'success', message: 'Channel deleted' });
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Failed to delete channel' });
    },
  });

  const toggleChannelMutation = useMutation({
    mutationFn: async ({ id, enabled }: { id: string; enabled: boolean }) => {
      await apiClient.patch(`/api/v1/admin/notification-channels/${id}`, { enabled });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notification-channels'] });
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Failed to toggle channel' });
    },
  });

  const testChannelMutation = useMutation({
    mutationFn: async (id: string) => {
      const res = await apiClient.post(`/api/v1/admin/notification-channels/${id}/test`);
      return res.data;
    },
    onSuccess: () => {
      showToast({ type: 'success', message: 'Test notification sent successfully' });
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Test failed. Check channel configuration.' });
    },
  });

  const savePreferencesMutation = useMutation({
    mutationFn: async (prefs: AlertTypePreference[]) => {
      await apiClient.put('/api/v1/admin/notification-preferences', { preferences: prefs });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notification-preferences'] });
      showToast({ type: 'success', message: 'Preferences saved' });
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Failed to save preferences' });
    },
  });

  // ----- Form helpers -----

  const resetForm = () => {
    setChannelName('');
    setChannelType('email');
    setChannelConfig(getDefaultConfig('email'));
    setChannelRecipients('');
  };

  const openEditDialog = (channel: NotificationChannel) => {
    setEditingChannel(channel);
    setChannelName(channel.name);
    setChannelType(channel.type);
    setChannelConfig({ ...getDefaultConfig(channel.type), ...channel.config });
    setChannelRecipients(channel.recipients.join(', '));
    setEditDialogOpen(true);
  };

  const handleAddSubmit = () => {
    if (!channelName.trim()) {
      showToast({ type: 'error', message: 'Channel name is required' });
      return;
    }
    const recipients = channelRecipients
      .split(',')
      .map((r) => r.trim())
      .filter(Boolean);

    addChannelMutation.mutate({
      name: channelName.trim(),
      type: channelType,
      config: channelConfig,
      recipients,
    });
  };

  const handleEditSubmit = () => {
    if (!editingChannel || !channelName.trim()) return;
    const recipients = channelRecipients
      .split(',')
      .map((r) => r.trim())
      .filter(Boolean);

    editChannelMutation.mutate({
      id: editingChannel.id,
      data: {
        name: channelName.trim(),
        type: channelType,
        config: channelConfig,
        recipients,
      },
    });
  };

  const updateConfigField = (field: string, value: string) => {
    setChannelConfig((prev) => ({ ...prev, [field]: value }));
  };

  // Manage local preferences state
  const [localPreferences, setLocalPreferences] = useState<AlertTypePreference[]>([]);

  useEffect(() => {
    if (preferences.length > 0) {
      setLocalPreferences(preferences);
    } else {
      // Initialize with defaults
      setLocalPreferences(
        ALERT_TYPES.map((type) => ({
          alert_type: type,
          channel_ids: [],
        }))
      );
    }
  }, [preferences]);

  const togglePreference = (alertType: string, channelId: string) => {
    setLocalPreferences((prev) =>
      prev.map((pref) => {
        if (pref.alert_type !== alertType) return pref;
        const ids = pref.channel_ids.includes(channelId)
          ? pref.channel_ids.filter((id) => id !== channelId)
          : [...pref.channel_ids, channelId];
        return { ...pref, channel_ids: ids };
      })
    );
  };

  // ----- Config form renderer -----

  const renderConfigForm = () => {
    switch (channelType) {
      case 'email':
        return (
          <div className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label className="text-xs">SMTP Server</Label>
                <Input
                  placeholder="smtp.gmail.com"
                  value={channelConfig.smtp_server || ''}
                  onChange={(e) => updateConfigField('smtp_server', e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">Port</Label>
                <Input
                  placeholder="587"
                  value={channelConfig.smtp_port || ''}
                  onChange={(e) => updateConfigField('smtp_port', e.target.value)}
                />
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label className="text-xs">Username</Label>
                <Input
                  placeholder="user@gmail.com"
                  value={channelConfig.smtp_username || ''}
                  onChange={(e) => updateConfigField('smtp_username', e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">Password</Label>
                <Input
                  type="password"
                  placeholder="App password"
                  value={channelConfig.smtp_password || ''}
                  onChange={(e) => updateConfigField('smtp_password', e.target.value)}
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">From Address</Label>
              <Input
                placeholder="alerts@mycompany.com"
                value={channelConfig.from_address || ''}
                onChange={(e) => updateConfigField('from_address', e.target.value)}
              />
            </div>
          </div>
        );

      case 'sms':
        return (
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label className="text-xs">Twilio Account SID</Label>
              <Input
                placeholder="ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                value={channelConfig.twilio_sid || ''}
                onChange={(e) => updateConfigField('twilio_sid', e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Auth Token</Label>
              <Input
                type="password"
                placeholder="Auth token"
                value={channelConfig.twilio_auth_token || ''}
                onChange={(e) => updateConfigField('twilio_auth_token', e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">From Number</Label>
              <Input
                placeholder="+1234567890"
                value={channelConfig.from_number || ''}
                onChange={(e) => updateConfigField('from_number', e.target.value)}
              />
            </div>
          </div>
        );

      case 'whatsapp':
        return (
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label className="text-xs">Twilio Account SID</Label>
              <Input
                placeholder="ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                value={channelConfig.twilio_sid || ''}
                onChange={(e) => updateConfigField('twilio_sid', e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Auth Token</Label>
              <Input
                type="password"
                placeholder="Auth token"
                value={channelConfig.twilio_auth_token || ''}
                onChange={(e) => updateConfigField('twilio_auth_token', e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">From Number (WhatsApp)</Label>
              <Input
                placeholder="+1234567890"
                value={channelConfig.from_number || ''}
                onChange={(e) => updateConfigField('from_number', e.target.value)}
              />
            </div>
          </div>
        );

      case 'telegram':
        return (
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label className="text-xs">Bot Token</Label>
              <Input
                placeholder="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
                value={channelConfig.bot_token || ''}
                onChange={(e) => updateConfigField('bot_token', e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Chat ID</Label>
              <Input
                placeholder="-1001234567890"
                value={channelConfig.chat_id || ''}
                onChange={(e) => updateConfigField('chat_id', e.target.value)}
              />
            </div>
          </div>
        );

      case 'webhook':
        return (
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label className="text-xs">Webhook URL</Label>
              <Input
                placeholder="https://example.com/webhook"
                value={channelConfig.url || ''}
                onChange={(e) => updateConfigField('url', e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Secret (optional)</Label>
              <Input
                type="password"
                placeholder="Signing secret"
                value={channelConfig.secret || ''}
                onChange={(e) => updateConfigField('secret', e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Custom Headers (JSON)</Label>
              <Input
                placeholder='{"Authorization": "Bearer token"}'
                value={channelConfig.headers || '{}'}
                onChange={(e) => updateConfigField('headers', e.target.value)}
              />
            </div>
          </div>
        );

      default:
        return null;
    }
  };

  // ---------- Render ----------

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Notification Channels</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Configure how and where alert notifications are delivered
          </p>
        </div>
        <Button onClick={() => { resetForm(); setAddDialogOpen(true); }}>
          <Plus className="mr-1 h-4 w-4" />
          Add Channel
        </Button>
      </div>

      {/* Toast */}
      {toast && (
        <div
          className={cn(
            'flex items-center gap-2 rounded-lg border px-4 py-3 text-sm transition-all',
            toast.type === 'success'
              ? 'border-green-200 bg-green-50 text-green-700 dark:border-green-800 dark:bg-green-900/30 dark:text-green-400'
              : 'border-red-200 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400'
          )}
        >
          {toast.type === 'success' ? (
            <CheckCircle2 className="h-4 w-4 shrink-0" />
          ) : (
            <AlertCircle className="h-4 w-4 shrink-0" />
          )}
          {toast.message}
        </div>
      )}

      <Tabs defaultValue="channels" className="space-y-4">
        <TabsList>
          <TabsTrigger value="channels" className="gap-1">
            <Bell className="h-3.5 w-3.5" />
            Channels
          </TabsTrigger>
          <TabsTrigger value="preferences" className="gap-1">
            <Settings className="h-3.5 w-3.5" />
            Alert Routing
          </TabsTrigger>
        </TabsList>

        {/* ===== Channels Tab ===== */}
        <TabsContent value="channels">
          {/* Search */}
          <div className="mb-4 flex items-center gap-3">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              <Input
                placeholder="Search channels..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="pl-10"
              />
            </div>
          </div>

          <Card>
            <CardContent className="p-0">
              {channelsLoading ? (
                <div className="flex h-64 items-center justify-center">
                  <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
                </div>
              ) : filteredChannels.length === 0 ? (
                <div className="flex h-64 flex-col items-center justify-center text-slate-400">
                  <Bell className="mb-3 h-10 w-10" />
                  <p className="text-sm font-medium">
                    {search ? 'No channels match your search' : 'No notification channels configured'}
                  </p>
                  {!search && (
                    <Button variant="link" className="mt-1" onClick={() => { resetForm(); setAddDialogOpen(true); }}>
                      Add your first channel
                    </Button>
                  )}
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow className="bg-slate-50/50 dark:bg-slate-800/50">
                        <TableHead>Channel</TableHead>
                        <TableHead>Type</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>Recipients</TableHead>
                        <TableHead>Updated</TableHead>
                        <TableHead className="text-right">Actions</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {filteredChannels.map((channel) => {
                        const typeConfig = getChannelTypeConfig(channel.type);
                        const TypeIcon = typeConfig.icon;

                        return (
                          <TableRow key={channel.id}>
                            <TableCell>
                              <div className="flex items-center gap-2">
                                <div className={cn('flex h-8 w-8 items-center justify-center rounded-lg', typeConfig.color)}>
                                  <TypeIcon className="h-4 w-4" />
                                </div>
                                <span className="font-medium text-slate-900 dark:text-white">
                                  {channel.name}
                                </span>
                              </div>
                            </TableCell>
                            <TableCell>
                              <Badge className={cn('text-xs', typeConfig.color)}>
                                {typeConfig.label}
                              </Badge>
                            </TableCell>
                            <TableCell>
                              <div className="flex items-center gap-2">
                                <Switch
                                  checked={channel.enabled}
                                  onCheckedChange={(enabled) => toggleChannelMutation.mutate({ id: channel.id, enabled })}
                                />
                                <span className={cn('text-xs', channel.enabled ? 'text-green-600' : 'text-slate-400')}>
                                  {channel.enabled ? 'Enabled' : 'Disabled'}
                                </span>
                              </div>
                            </TableCell>
                            <TableCell>
                              <div className="flex items-center gap-1">
                                <Users className="h-3 w-3 text-slate-400" />
                                <span className="text-sm text-slate-600 dark:text-slate-400">
                                  {channel.recipients.length} recipient{channel.recipients.length !== 1 ? 's' : ''}
                                </span>
                              </div>
                            </TableCell>
                            <TableCell className="text-sm text-slate-500 dark:text-slate-400">
                              {formatDate(channel.updated_at, 'MMM d, yyyy')}
                            </TableCell>
                            <TableCell className="text-right">
                              <div className="flex items-center justify-end gap-1">
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  onClick={() => testChannelMutation.mutate(channel.id)}
                                  disabled={testChannelMutation.isPending}
                                  title="Test channel"
                                >
                                  {testChannelMutation.isPending ? (
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                  ) : (
                                    <TestTube2 className="h-4 w-4" />
                                  )}
                                </Button>
                                <DropdownMenu>
                                  <DropdownMenuTrigger asChild>
                                    <Button variant="ghost" size="icon" className="h-8 w-8">
                                      <MoreHorizontal className="h-4 w-4" />
                                    </Button>
                                  </DropdownMenuTrigger>
                                  <DropdownMenuContent align="end">
                                    <DropdownMenuItem onClick={() => openEditDialog(channel)}>
                                      <Pencil className="mr-2 h-4 w-4" />
                                      Edit
                                    </DropdownMenuItem>
                                    <DropdownMenuItem
                                      onClick={() => deleteChannelMutation.mutate(channel.id)}
                                      className="text-red-600 dark:text-red-400"
                                    >
                                      <Trash2 className="mr-2 h-4 w-4" />
                                      Delete
                                    </DropdownMenuItem>
                                  </DropdownMenuContent>
                                </DropdownMenu>
                              </div>
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ===== Preferences Tab ===== */}
        <TabsContent value="preferences">
          <Card>
            <CardHeader>
              <CardTitle>Alert Routing</CardTitle>
              <CardDescription>
                Configure which alert types are sent to which notification channels
              </CardDescription>
            </CardHeader>
            <CardContent>
              {preferencesLoading || channelsLoading ? (
                <div className="flex h-40 items-center justify-center">
                  <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
                </div>
              ) : channels.length === 0 ? (
                <div className="flex h-40 items-center justify-center text-sm text-slate-400">
                  <div className="text-center">
                    <Bell className="mx-auto mb-2 h-8 w-8" />
                    Create notification channels first to configure routing
                  </div>
                </div>
              ) : (
                <div className="space-y-4">
                  <div className="overflow-x-auto">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead className="min-w-[180px]">Alert Type</TableHead>
                          {channels.map((ch) => {
                            const typeConfig = getChannelTypeConfig(ch.type);
                            const TypeIcon = typeConfig.icon;
                            return (
                              <TableHead key={ch.id} className="text-center min-w-[100px]">
                                <div className="flex flex-col items-center gap-1">
                                  <TypeIcon className="h-3.5 w-3.5 text-slate-500" />
                                  <span className="text-xs">{ch.name}</span>
                                </div>
                              </TableHead>
                            );
                          })}
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {ALERT_TYPES.map((alertType) => {
                          const pref = localPreferences.find((p) => p.alert_type === alertType);
                          return (
                            <TableRow key={alertType}>
                              <TableCell className="font-medium text-sm">
                                {alertType
                                  .replace(/_/g, ' ')
                                  .replace(/\b\w/g, (l) => l.toUpperCase())}
                              </TableCell>
                              {channels.map((ch) => (
                                <TableCell key={ch.id} className="text-center">
                                  <input
                                    type="checkbox"
                                    checked={pref?.channel_ids.includes(ch.id) || false}
                                    onChange={() => togglePreference(alertType, ch.id)}
                                    className="h-4 w-4 rounded border-slate-300 accent-primary"
                                  />
                                </TableCell>
                              ))}
                            </TableRow>
                          );
                        })}
                      </TableBody>
                    </Table>
                  </div>

                  <div className="flex justify-end">
                    <Button
                      onClick={() => savePreferencesMutation.mutate(localPreferences)}
                      disabled={savePreferencesMutation.isPending}
                    >
                      {savePreferencesMutation.isPending ? (
                        <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                      ) : (
                        <CheckCircle2 className="mr-1 h-4 w-4" />
                      )}
                      Save Preferences
                    </Button>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* Add Channel Dialog */}
      <Dialog open={addDialogOpen} onOpenChange={(open) => { setAddDialogOpen(open); if (!open) resetForm(); }}>
        <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Add Notification Channel</DialogTitle>
            <DialogDescription>
              Configure a new channel for sending alert notifications
            </DialogDescription>
          </DialogHeader>

          {addChannelMutation.isError && (
            <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">
              <AlertCircle className="h-4 w-4 shrink-0" />
              {(addChannelMutation.error as any)?.message || 'Failed to create channel'}
            </div>
          )}

          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Channel Name</Label>
              <Input
                placeholder="e.g., Security Team Email"
                value={channelName}
                onChange={(e) => setChannelName(e.target.value)}
              />
            </div>

            <div className="space-y-2">
              <Label>Channel Type</Label>
              <Select
                value={channelType}
                onValueChange={(v: ChannelType) => {
                  setChannelType(v);
                  setChannelConfig(getDefaultConfig(v));
                }}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CHANNEL_TYPES.map((ct) => (
                    <SelectItem key={ct.value} value={ct.value}>
                      <div className="flex items-center gap-2">
                        <ct.icon className="h-4 w-4" />
                        {ct.label}
                      </div>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <Separator />

            <div>
              <h4 className="mb-3 text-sm font-medium text-slate-900 dark:text-white">
                {getChannelTypeConfig(channelType).label} Configuration
              </h4>
              {renderConfigForm()}
            </div>

            <Separator />

            <div className="space-y-2">
              <Label>Recipients</Label>
              <Input
                placeholder={
                  channelType === 'email'
                    ? 'admin@company.com, security@company.com'
                    : channelType === 'sms' || channelType === 'whatsapp'
                      ? '+1234567890, +0987654321'
                      : 'Recipient identifiers, comma-separated'
                }
                value={channelRecipients}
                onChange={(e) => setChannelRecipients(e.target.value)}
              />
              <p className="text-xs text-slate-500">Comma-separated list of recipients</p>
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => { setAddDialogOpen(false); resetForm(); }}>
              Cancel
            </Button>
            <Button onClick={handleAddSubmit} disabled={addChannelMutation.isPending}>
              {addChannelMutation.isPending ? (
                <>
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                  Creating...
                </>
              ) : (
                <>
                  <Plus className="mr-1 h-4 w-4" />
                  Create Channel
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Edit Channel Dialog */}
      <Dialog open={editDialogOpen} onOpenChange={(open) => { setEditDialogOpen(open); if (!open) { setEditingChannel(null); resetForm(); } }}>
        <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Edit Notification Channel</DialogTitle>
            <DialogDescription>
              Update the channel configuration and recipients
            </DialogDescription>
          </DialogHeader>

          {editChannelMutation.isError && (
            <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">
              <AlertCircle className="h-4 w-4 shrink-0" />
              {(editChannelMutation.error as any)?.message || 'Failed to update channel'}
            </div>
          )}

          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Channel Name</Label>
              <Input
                value={channelName}
                onChange={(e) => setChannelName(e.target.value)}
              />
            </div>

            <div className="space-y-2">
              <Label>Channel Type</Label>
              <Select
                value={channelType}
                onValueChange={(v: ChannelType) => {
                  setChannelType(v);
                  setChannelConfig(getDefaultConfig(v));
                }}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CHANNEL_TYPES.map((ct) => (
                    <SelectItem key={ct.value} value={ct.value}>
                      <div className="flex items-center gap-2">
                        <ct.icon className="h-4 w-4" />
                        {ct.label}
                      </div>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <Separator />

            <div>
              <h4 className="mb-3 text-sm font-medium text-slate-900 dark:text-white">
                {getChannelTypeConfig(channelType).label} Configuration
              </h4>
              {renderConfigForm()}
            </div>

            <Separator />

            <div className="space-y-2">
              <Label>Recipients</Label>
              <Input
                placeholder="Comma-separated recipients"
                value={channelRecipients}
                onChange={(e) => setChannelRecipients(e.target.value)}
              />
              <p className="text-xs text-slate-500">Comma-separated list of recipients</p>
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => { setEditDialogOpen(false); setEditingChannel(null); resetForm(); }}>
              Cancel
            </Button>
            <Button onClick={handleEditSubmit} disabled={editChannelMutation.isPending}>
              {editChannelMutation.isPending ? (
                <>
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                  Saving...
                </>
              ) : (
                <>
                  <CheckCircle2 className="mr-1 h-4 w-4" />
                  Save Changes
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
