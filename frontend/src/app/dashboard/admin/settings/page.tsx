'use client';

import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Building2,
  Shield,
  Database,
  Link2,
  Cpu,
  Loader2,
  Save,
  Upload,
  Plus,
  Trash2,
  Copy,
  Eye,
  EyeOff,
  CheckCircle2,
  AlertCircle,
  Key,
  Globe,
  Clock,
  Lock,
  HardDrive,
  Webhook,
  Gauge,
  Monitor,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Separator } from '@/components/ui/separator';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { apiClient } from '@/lib/api-client';
import { cn, formatDate } from '@/lib/utils';

// ---------- Types ----------

interface GeneralSettings {
  organization_name: string;
  logo_url: string;
  timezone: string;
  language: string;
}

interface SecuritySettings {
  min_password_length: number;
  require_uppercase: boolean;
  require_numbers: boolean;
  require_special_chars: boolean;
  session_timeout_minutes: number;
  two_factor_enabled: boolean;
  two_factor_method: string;
  max_login_attempts: number;
  lockout_duration_minutes: number;
}

interface StorageSettings {
  minio_endpoint: string;
  minio_access_key: string;
  minio_secret_key: string;
  minio_bucket: string;
  minio_use_ssl: boolean;
  retention_days: number;
  auto_cleanup_enabled: boolean;
}

interface ApiKeyItem {
  id: string;
  name: string;
  key_prefix: string;
  created_at: string;
  last_used: string | null;
  status: 'active' | 'revoked';
}

interface IntegrationSettings {
  api_keys: ApiKeyItem[];
  webhook_urls: { id: string; url: string; events: string[]; active: boolean }[];
}

interface PerformanceSettings {
  gpu_devices: { id: string; name: string; memory_total: number; memory_used: number; utilization: number; temperature: number }[];
  inference_batch_size: number;
  inference_confidence_threshold: number;
  max_concurrent_cameras: number;
  frame_skip_interval: number;
  enable_gpu_acceleration: boolean;
}

// ---------- Timezones and Languages ----------

const TIMEZONES = [
  'UTC', 'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
  'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'Asia/Tokyo', 'Asia/Shanghai',
  'Asia/Kolkata', 'Asia/Dubai', 'Australia/Sydney', 'Pacific/Auckland',
];

const LANGUAGES = [
  { value: 'en', label: 'English' },
  { value: 'es', label: 'Spanish' },
  { value: 'fr', label: 'French' },
  { value: 'de', label: 'German' },
  { value: 'zh', label: 'Chinese' },
  { value: 'ja', label: 'Japanese' },
  { value: 'ar', label: 'Arabic' },
  { value: 'hi', label: 'Hindi' },
];

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

// ---------- Component ----------

export default function SettingsPage() {
  const queryClient = useQueryClient();
  const { toast, showToast } = useToast();

  // ----- General Tab State -----
  const [generalForm, setGeneralForm] = useState<GeneralSettings>({
    organization_name: '',
    logo_url: '',
    timezone: 'UTC',
    language: 'en',
  });

  const { data: generalData, isLoading: generalLoading } = useQuery<GeneralSettings>({
    queryKey: ['settings', 'general'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/admin/settings/general');
      return res.data;
    },
  });

  useEffect(() => {
    if (generalData) setGeneralForm(generalData);
  }, [generalData]);

  const saveGeneralMutation = useMutation({
    mutationFn: async (data: GeneralSettings) => {
      await apiClient.put('/api/v1/admin/settings/general', data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings', 'general'] });
      showToast({ type: 'success', message: 'General settings saved successfully' });
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Failed to save general settings' });
    },
  });

  const logoUploadMutation = useMutation({
    mutationFn: async (file: File) => {
      const formData = new FormData();
      formData.append('logo', file);
      const res = await apiClient.post('/api/v1/admin/settings/logo', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      return res.data;
    },
    onSuccess: (data: any) => {
      setGeneralForm((prev) => ({ ...prev, logo_url: data.logo_url || data.url }));
      showToast({ type: 'success', message: 'Logo uploaded successfully' });
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Failed to upload logo' });
    },
  });

  // ----- Security Tab State -----
  const [securityForm, setSecurityForm] = useState<SecuritySettings>({
    min_password_length: 8,
    require_uppercase: true,
    require_numbers: true,
    require_special_chars: false,
    session_timeout_minutes: 30,
    two_factor_enabled: false,
    two_factor_method: 'totp',
    max_login_attempts: 5,
    lockout_duration_minutes: 15,
  });

  const { data: securityData, isLoading: securityLoading } = useQuery<SecuritySettings>({
    queryKey: ['settings', 'security'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/admin/settings/security');
      return res.data;
    },
  });

  useEffect(() => {
    if (securityData) setSecurityForm(securityData);
  }, [securityData]);

  const saveSecurityMutation = useMutation({
    mutationFn: async (data: SecuritySettings) => {
      await apiClient.put('/api/v1/admin/settings/security', data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings', 'security'] });
      showToast({ type: 'success', message: 'Security settings saved successfully' });
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Failed to save security settings' });
    },
  });

  // ----- Storage Tab State -----
  const [storageForm, setStorageForm] = useState<StorageSettings>({
    minio_endpoint: '',
    minio_access_key: '',
    minio_secret_key: '',
    minio_bucket: '',
    minio_use_ssl: true,
    retention_days: 30,
    auto_cleanup_enabled: true,
  });
  const [showMinioSecret, setShowMinioSecret] = useState(false);

  const { data: storageData, isLoading: storageLoading } = useQuery<StorageSettings>({
    queryKey: ['settings', 'storage'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/admin/settings/storage');
      return res.data;
    },
  });

  useEffect(() => {
    if (storageData) setStorageForm(storageData);
  }, [storageData]);

  const saveStorageMutation = useMutation({
    mutationFn: async (data: StorageSettings) => {
      await apiClient.put('/api/v1/admin/settings/storage', data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings', 'storage'] });
      showToast({ type: 'success', message: 'Storage settings saved successfully' });
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Failed to save storage settings' });
    },
  });

  // ----- Integration Tab State -----
  const [newApiKeyName, setNewApiKeyName] = useState('');
  const [newWebhookUrl, setNewWebhookUrl] = useState('');
  const [revealedKey, setRevealedKey] = useState<string | null>(null);

  const { data: integrationData, isLoading: integrationLoading } = useQuery<IntegrationSettings>({
    queryKey: ['settings', 'integration'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/admin/settings/integration');
      return res.data;
    },
  });

  const createApiKeyMutation = useMutation({
    mutationFn: async (name: string) => {
      const res = await apiClient.post('/api/v1/admin/api-keys', { name });
      return res.data;
    },
    onSuccess: (data: any) => {
      queryClient.invalidateQueries({ queryKey: ['settings', 'integration'] });
      setNewApiKeyName('');
      setRevealedKey(data.key || data.api_key);
      showToast({ type: 'success', message: 'API key created. Copy it now - it won\'t be shown again.' });
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Failed to create API key' });
    },
  });

  const revokeApiKeyMutation = useMutation({
    mutationFn: async (keyId: string) => {
      await apiClient.delete(`/api/v1/admin/api-keys/${keyId}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings', 'integration'] });
      showToast({ type: 'success', message: 'API key revoked' });
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Failed to revoke API key' });
    },
  });

  const addWebhookMutation = useMutation({
    mutationFn: async (url: string) => {
      const res = await apiClient.post('/api/v1/admin/webhooks', { url, events: ['*'], active: true });
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings', 'integration'] });
      setNewWebhookUrl('');
      showToast({ type: 'success', message: 'Webhook added' });
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Failed to add webhook' });
    },
  });

  const deleteWebhookMutation = useMutation({
    mutationFn: async (webhookId: string) => {
      await apiClient.delete(`/api/v1/admin/webhooks/${webhookId}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings', 'integration'] });
      showToast({ type: 'success', message: 'Webhook removed' });
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Failed to remove webhook' });
    },
  });

  // ----- Performance Tab State -----
  const [performanceForm, setPerformanceForm] = useState({
    inference_batch_size: 4,
    inference_confidence_threshold: 0.5,
    max_concurrent_cameras: 16,
    frame_skip_interval: 3,
    enable_gpu_acceleration: true,
  });

  const { data: performanceData, isLoading: performanceLoading } = useQuery<PerformanceSettings>({
    queryKey: ['settings', 'performance'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/admin/settings/performance');
      return res.data;
    },
    refetchInterval: 10000,
  });

  useEffect(() => {
    if (performanceData) {
      setPerformanceForm({
        inference_batch_size: performanceData.inference_batch_size,
        inference_confidence_threshold: performanceData.inference_confidence_threshold,
        max_concurrent_cameras: performanceData.max_concurrent_cameras,
        frame_skip_interval: performanceData.frame_skip_interval,
        enable_gpu_acceleration: performanceData.enable_gpu_acceleration,
      });
    }
  }, [performanceData]);

  const savePerformanceMutation = useMutation({
    mutationFn: async (data: typeof performanceForm) => {
      await apiClient.put('/api/v1/admin/settings/performance', data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings', 'performance'] });
      showToast({ type: 'success', message: 'Performance settings saved successfully' });
    },
    onError: (err: any) => {
      showToast({ type: 'error', message: err?.message || 'Failed to save performance settings' });
    },
  });

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    showToast({ type: 'success', message: 'Copied to clipboard' });
  };

  // ---------- Render ----------

  return (
    <div className="space-y-4">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-white">System Settings</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Configure system-wide settings and preferences
        </p>
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

      {/* Tabs */}
      <Tabs defaultValue="general" className="space-y-4">
        <TabsList className="grid w-full grid-cols-5">
          <TabsTrigger value="general" className="gap-1 text-xs sm:text-sm">
            <Building2 className="h-3.5 w-3.5 hidden sm:block" />
            General
          </TabsTrigger>
          <TabsTrigger value="security" className="gap-1 text-xs sm:text-sm">
            <Shield className="h-3.5 w-3.5 hidden sm:block" />
            Security
          </TabsTrigger>
          <TabsTrigger value="storage" className="gap-1 text-xs sm:text-sm">
            <Database className="h-3.5 w-3.5 hidden sm:block" />
            Storage
          </TabsTrigger>
          <TabsTrigger value="integration" className="gap-1 text-xs sm:text-sm">
            <Link2 className="h-3.5 w-3.5 hidden sm:block" />
            Integration
          </TabsTrigger>
          <TabsTrigger value="performance" className="gap-1 text-xs sm:text-sm">
            <Cpu className="h-3.5 w-3.5 hidden sm:block" />
            Performance
          </TabsTrigger>
        </TabsList>

        {/* ===== General Tab ===== */}
        <TabsContent value="general">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Building2 className="h-5 w-5" />
                General Settings
              </CardTitle>
              <CardDescription>Organization information and regional preferences</CardDescription>
            </CardHeader>
            <CardContent>
              {generalLoading ? (
                <div className="flex h-40 items-center justify-center">
                  <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
                </div>
              ) : (
                <div className="space-y-6">
                  <div className="space-y-2">
                    <Label htmlFor="org-name">Organization Name</Label>
                    <Input
                      id="org-name"
                      placeholder="My Organization"
                      value={generalForm.organization_name}
                      onChange={(e) => setGeneralForm((prev) => ({ ...prev, organization_name: e.target.value }))}
                    />
                  </div>

                  <div className="space-y-2">
                    <Label>Organization Logo</Label>
                    <div className="flex items-center gap-4">
                      {generalForm.logo_url ? (
                        <img
                          src={generalForm.logo_url}
                          alt="Organization logo"
                          className="h-16 w-16 rounded-lg border object-contain"
                        />
                      ) : (
                        <div className="flex h-16 w-16 items-center justify-center rounded-lg border border-dashed border-slate-300 bg-slate-50 dark:border-slate-600 dark:bg-slate-800">
                          <Building2 className="h-6 w-6 text-slate-400" />
                        </div>
                      )}
                      <div>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            const input = document.createElement('input');
                            input.type = 'file';
                            input.accept = 'image/*';
                            input.onchange = (e) => {
                              const file = (e.target as HTMLInputElement).files?.[0];
                              if (file) logoUploadMutation.mutate(file);
                            };
                            input.click();
                          }}
                          disabled={logoUploadMutation.isPending}
                        >
                          {logoUploadMutation.isPending ? (
                            <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                          ) : (
                            <Upload className="mr-1 h-4 w-4" />
                          )}
                          Upload Logo
                        </Button>
                        <p className="mt-1 text-xs text-slate-500">PNG, JPG up to 2MB</p>
                      </div>
                    </div>
                  </div>

                  <div className="grid gap-4 sm:grid-cols-2">
                    <div className="space-y-2">
                      <Label htmlFor="timezone">Timezone</Label>
                      <Select
                        value={generalForm.timezone}
                        onValueChange={(v) => setGeneralForm((prev) => ({ ...prev, timezone: v }))}
                      >
                        <SelectTrigger id="timezone">
                          <SelectValue placeholder="Select timezone" />
                        </SelectTrigger>
                        <SelectContent>
                          {TIMEZONES.map((tz) => (
                            <SelectItem key={tz} value={tz}>
                              {tz.replace(/_/g, ' ')}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>

                    <div className="space-y-2">
                      <Label htmlFor="language">Language</Label>
                      <Select
                        value={generalForm.language}
                        onValueChange={(v) => setGeneralForm((prev) => ({ ...prev, language: v }))}
                      >
                        <SelectTrigger id="language">
                          <SelectValue placeholder="Select language" />
                        </SelectTrigger>
                        <SelectContent>
                          {LANGUAGES.map((lang) => (
                            <SelectItem key={lang.value} value={lang.value}>
                              {lang.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>

                  <Separator />

                  <div className="flex justify-end">
                    <Button onClick={() => saveGeneralMutation.mutate(generalForm)} disabled={saveGeneralMutation.isPending}>
                      {saveGeneralMutation.isPending ? (
                        <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                      ) : (
                        <Save className="mr-1 h-4 w-4" />
                      )}
                      Save Changes
                    </Button>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ===== Security Tab ===== */}
        <TabsContent value="security">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Shield className="h-5 w-5" />
                Security Settings
              </CardTitle>
              <CardDescription>Password policies, session management, and two-factor authentication</CardDescription>
            </CardHeader>
            <CardContent>
              {securityLoading ? (
                <div className="flex h-40 items-center justify-center">
                  <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
                </div>
              ) : (
                <div className="space-y-6">
                  {/* Password Policy */}
                  <div>
                    <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-white">
                      <Lock className="h-4 w-4" />
                      Password Policy
                    </h3>
                    <div className="grid gap-4 sm:grid-cols-2">
                      <div className="space-y-2">
                        <Label htmlFor="min-pw-length">Minimum Password Length</Label>
                        <Input
                          id="min-pw-length"
                          type="number"
                          min={6}
                          max={128}
                          value={securityForm.min_password_length}
                          onChange={(e) => setSecurityForm((prev) => ({ ...prev, min_password_length: Number(e.target.value) }))}
                        />
                      </div>
                      <div className="space-y-3 pt-2">
                        <div className="flex items-center justify-between">
                          <Label htmlFor="req-upper">Require uppercase letters</Label>
                          <Switch
                            id="req-upper"
                            checked={securityForm.require_uppercase}
                            onCheckedChange={(v) => setSecurityForm((prev) => ({ ...prev, require_uppercase: v }))}
                          />
                        </div>
                        <div className="flex items-center justify-between">
                          <Label htmlFor="req-numbers">Require numbers</Label>
                          <Switch
                            id="req-numbers"
                            checked={securityForm.require_numbers}
                            onCheckedChange={(v) => setSecurityForm((prev) => ({ ...prev, require_numbers: v }))}
                          />
                        </div>
                        <div className="flex items-center justify-between">
                          <Label htmlFor="req-special">Require special characters</Label>
                          <Switch
                            id="req-special"
                            checked={securityForm.require_special_chars}
                            onCheckedChange={(v) => setSecurityForm((prev) => ({ ...prev, require_special_chars: v }))}
                          />
                        </div>
                      </div>
                    </div>
                  </div>

                  <Separator />

                  {/* Session Settings */}
                  <div>
                    <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-white">
                      <Clock className="h-4 w-4" />
                      Session Settings
                    </h3>
                    <div className="grid gap-4 sm:grid-cols-2">
                      <div className="space-y-2">
                        <Label htmlFor="session-timeout">Session Timeout (minutes)</Label>
                        <Input
                          id="session-timeout"
                          type="number"
                          min={5}
                          max={1440}
                          value={securityForm.session_timeout_minutes}
                          onChange={(e) => setSecurityForm((prev) => ({ ...prev, session_timeout_minutes: Number(e.target.value) }))}
                        />
                      </div>
                    </div>
                  </div>

                  <Separator />

                  {/* 2FA */}
                  <div>
                    <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-white">
                      <Key className="h-4 w-4" />
                      Two-Factor Authentication
                    </h3>
                    <div className="grid gap-4 sm:grid-cols-2">
                      <div className="flex items-center justify-between">
                        <div>
                          <Label>Enable 2FA</Label>
                          <p className="text-xs text-slate-500">Require two-factor authentication for all users</p>
                        </div>
                        <Switch
                          checked={securityForm.two_factor_enabled}
                          onCheckedChange={(v) => setSecurityForm((prev) => ({ ...prev, two_factor_enabled: v }))}
                        />
                      </div>
                      {securityForm.two_factor_enabled && (
                        <div className="space-y-2">
                          <Label htmlFor="2fa-method">2FA Method</Label>
                          <Select
                            value={securityForm.two_factor_method}
                            onValueChange={(v) => setSecurityForm((prev) => ({ ...prev, two_factor_method: v }))}
                          >
                            <SelectTrigger id="2fa-method">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="totp">TOTP (Authenticator App)</SelectItem>
                              <SelectItem value="sms">SMS</SelectItem>
                              <SelectItem value="email">Email</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                      )}
                    </div>
                  </div>

                  <Separator />

                  {/* Login Attempts */}
                  <div>
                    <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-white">
                      <AlertCircle className="h-4 w-4" />
                      Login Attempts
                    </h3>
                    <div className="grid gap-4 sm:grid-cols-2">
                      <div className="space-y-2">
                        <Label htmlFor="max-attempts">Max Login Attempts</Label>
                        <Input
                          id="max-attempts"
                          type="number"
                          min={1}
                          max={20}
                          value={securityForm.max_login_attempts}
                          onChange={(e) => setSecurityForm((prev) => ({ ...prev, max_login_attempts: Number(e.target.value) }))}
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="lockout-dur">Lockout Duration (minutes)</Label>
                        <Input
                          id="lockout-dur"
                          type="number"
                          min={1}
                          max={1440}
                          value={securityForm.lockout_duration_minutes}
                          onChange={(e) => setSecurityForm((prev) => ({ ...prev, lockout_duration_minutes: Number(e.target.value) }))}
                        />
                      </div>
                    </div>
                  </div>

                  <Separator />

                  <div className="flex justify-end">
                    <Button onClick={() => saveSecurityMutation.mutate(securityForm)} disabled={saveSecurityMutation.isPending}>
                      {saveSecurityMutation.isPending ? (
                        <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                      ) : (
                        <Save className="mr-1 h-4 w-4" />
                      )}
                      Save Changes
                    </Button>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ===== Storage Tab ===== */}
        <TabsContent value="storage">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Database className="h-5 w-5" />
                Storage Settings
              </CardTitle>
              <CardDescription>MinIO object storage connection and data retention policies</CardDescription>
            </CardHeader>
            <CardContent>
              {storageLoading ? (
                <div className="flex h-40 items-center justify-center">
                  <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
                </div>
              ) : (
                <div className="space-y-6">
                  {/* MinIO Connection */}
                  <div>
                    <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-white">
                      <HardDrive className="h-4 w-4" />
                      MinIO Connection
                    </h3>
                    <div className="grid gap-4 sm:grid-cols-2">
                      <div className="space-y-2">
                        <Label htmlFor="minio-endpoint">Endpoint</Label>
                        <Input
                          id="minio-endpoint"
                          placeholder="minio.example.com:9000"
                          value={storageForm.minio_endpoint}
                          onChange={(e) => setStorageForm((prev) => ({ ...prev, minio_endpoint: e.target.value }))}
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="minio-bucket">Bucket Name</Label>
                        <Input
                          id="minio-bucket"
                          placeholder="visionai-data"
                          value={storageForm.minio_bucket}
                          onChange={(e) => setStorageForm((prev) => ({ ...prev, minio_bucket: e.target.value }))}
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="minio-access">Access Key</Label>
                        <Input
                          id="minio-access"
                          placeholder="Access key"
                          value={storageForm.minio_access_key}
                          onChange={(e) => setStorageForm((prev) => ({ ...prev, minio_access_key: e.target.value }))}
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="minio-secret">Secret Key</Label>
                        <div className="relative">
                          <Input
                            id="minio-secret"
                            type={showMinioSecret ? 'text' : 'password'}
                            placeholder="Secret key"
                            value={storageForm.minio_secret_key}
                            onChange={(e) => setStorageForm((prev) => ({ ...prev, minio_secret_key: e.target.value }))}
                            className="pr-10"
                          />
                          <button
                            type="button"
                            onClick={() => setShowMinioSecret(!showMinioSecret)}
                            className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
                          >
                            {showMinioSecret ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                          </button>
                        </div>
                      </div>
                    </div>
                    <div className="mt-3 flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Switch
                          id="minio-ssl"
                          checked={storageForm.minio_use_ssl}
                          onCheckedChange={(v) => setStorageForm((prev) => ({ ...prev, minio_use_ssl: v }))}
                        />
                        <Label htmlFor="minio-ssl" className="text-sm">Use SSL/TLS</Label>
                      </div>
                    </div>
                  </div>

                  <Separator />

                  {/* Retention Policy */}
                  <div>
                    <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-white">
                      <Clock className="h-4 w-4" />
                      Retention Policy
                    </h3>
                    <div className="grid gap-4 sm:grid-cols-2">
                      <div className="space-y-2">
                        <Label htmlFor="retention-days">Retention Period (days)</Label>
                        <Input
                          id="retention-days"
                          type="number"
                          min={1}
                          max={3650}
                          value={storageForm.retention_days}
                          onChange={(e) => setStorageForm((prev) => ({ ...prev, retention_days: Number(e.target.value) }))}
                        />
                        <p className="text-xs text-slate-500">Data older than this will be eligible for cleanup</p>
                      </div>
                      <div className="flex items-center justify-between pt-4">
                        <div>
                          <Label>Auto-Cleanup</Label>
                          <p className="text-xs text-slate-500">Automatically remove expired data</p>
                        </div>
                        <Switch
                          checked={storageForm.auto_cleanup_enabled}
                          onCheckedChange={(v) => setStorageForm((prev) => ({ ...prev, auto_cleanup_enabled: v }))}
                        />
                      </div>
                    </div>
                  </div>

                  <Separator />

                  <div className="flex justify-end">
                    <Button onClick={() => saveStorageMutation.mutate(storageForm)} disabled={saveStorageMutation.isPending}>
                      {saveStorageMutation.isPending ? (
                        <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                      ) : (
                        <Save className="mr-1 h-4 w-4" />
                      )}
                      Save Changes
                    </Button>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ===== Integration Tab ===== */}
        <TabsContent value="integration">
          <div className="space-y-4">
            {/* API Keys */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Key className="h-5 w-5" />
                  API Keys
                </CardTitle>
                <CardDescription>Manage API keys for programmatic access</CardDescription>
              </CardHeader>
              <CardContent>
                {integrationLoading ? (
                  <div className="flex h-32 items-center justify-center">
                    <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
                  </div>
                ) : (
                  <div className="space-y-4">
                    {/* Revealed key banner */}
                    {revealedKey && (
                      <div className="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 dark:border-amber-800 dark:bg-amber-900/20">
                        <AlertCircle className="h-4 w-4 shrink-0 text-amber-600" />
                        <div className="flex-1">
                          <p className="text-sm font-medium text-amber-800 dark:text-amber-300">
                            New API Key (copy now, it won't be shown again):
                          </p>
                          <code className="mt-1 block break-all rounded bg-amber-100 px-2 py-1 font-mono text-xs text-amber-900 dark:bg-amber-900/40 dark:text-amber-200">
                            {revealedKey}
                          </code>
                        </div>
                        <Button variant="ghost" size="icon" className="shrink-0" onClick={() => copyToClipboard(revealedKey)}>
                          <Copy className="h-4 w-4" />
                        </Button>
                        <Button variant="ghost" size="icon" className="shrink-0" onClick={() => setRevealedKey(null)}>
                          <CheckCircle2 className="h-4 w-4 text-green-600" />
                        </Button>
                      </div>
                    )}

                    {/* Create new key */}
                    <div className="flex items-end gap-2">
                      <div className="flex-1 space-y-2">
                        <Label htmlFor="new-key-name">Key Name</Label>
                        <Input
                          id="new-key-name"
                          placeholder="e.g., Production API Key"
                          value={newApiKeyName}
                          onChange={(e) => setNewApiKeyName(e.target.value)}
                        />
                      </div>
                      <Button
                        onClick={() => {
                          if (newApiKeyName.trim()) createApiKeyMutation.mutate(newApiKeyName.trim());
                        }}
                        disabled={!newApiKeyName.trim() || createApiKeyMutation.isPending}
                      >
                        {createApiKeyMutation.isPending ? (
                          <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                        ) : (
                          <Plus className="mr-1 h-4 w-4" />
                        )}
                        Create Key
                      </Button>
                    </div>

                    {/* Keys list */}
                    <div className="space-y-2">
                      {integrationData?.api_keys && integrationData.api_keys.length > 0 ? (
                        integrationData.api_keys.map((key) => (
                          <div
                            key={key.id}
                            className="flex items-center justify-between rounded-lg border px-4 py-3"
                          >
                            <div className="flex items-center gap-3">
                              <Key className="h-4 w-4 text-slate-400" />
                              <div>
                                <p className="text-sm font-medium text-slate-900 dark:text-white">{key.name}</p>
                                <p className="text-xs text-slate-500">
                                  {key.key_prefix}... | Created: {formatDate(key.created_at, 'MMM d, yyyy')}
                                  {key.last_used && ` | Last used: ${formatDate(key.last_used, 'MMM d, yyyy')}`}
                                </p>
                              </div>
                            </div>
                            <div className="flex items-center gap-2">
                              <Badge className={key.status === 'active' ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'}>
                                {key.status}
                              </Badge>
                              {key.status === 'active' && (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="text-red-600 hover:text-red-700"
                                  onClick={() => revokeApiKeyMutation.mutate(key.id)}
                                  disabled={revokeApiKeyMutation.isPending}
                                >
                                  <Trash2 className="h-4 w-4" />
                                </Button>
                              )}
                            </div>
                          </div>
                        ))
                      ) : (
                        <div className="flex h-20 items-center justify-center text-sm text-slate-400">
                          No API keys created yet
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Webhooks */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Webhook className="h-5 w-5" />
                  Webhook URLs
                </CardTitle>
                <CardDescription>Configure webhook endpoints for event notifications</CardDescription>
              </CardHeader>
              <CardContent>
                {integrationLoading ? (
                  <div className="flex h-32 items-center justify-center">
                    <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
                  </div>
                ) : (
                  <div className="space-y-4">
                    <div className="flex items-end gap-2">
                      <div className="flex-1 space-y-2">
                        <Label htmlFor="new-webhook">Webhook URL</Label>
                        <Input
                          id="new-webhook"
                          placeholder="https://example.com/webhook"
                          value={newWebhookUrl}
                          onChange={(e) => setNewWebhookUrl(e.target.value)}
                        />
                      </div>
                      <Button
                        onClick={() => {
                          if (newWebhookUrl.trim()) addWebhookMutation.mutate(newWebhookUrl.trim());
                        }}
                        disabled={!newWebhookUrl.trim() || addWebhookMutation.isPending}
                      >
                        {addWebhookMutation.isPending ? (
                          <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                        ) : (
                          <Plus className="mr-1 h-4 w-4" />
                        )}
                        Add Webhook
                      </Button>
                    </div>

                    <div className="space-y-2">
                      {integrationData?.webhook_urls && integrationData.webhook_urls.length > 0 ? (
                        integrationData.webhook_urls.map((webhook) => (
                          <div
                            key={webhook.id}
                            className="flex items-center justify-between rounded-lg border px-4 py-3"
                          >
                            <div className="flex items-center gap-3 overflow-hidden">
                              <Globe className="h-4 w-4 shrink-0 text-slate-400" />
                              <div className="overflow-hidden">
                                <p className="truncate text-sm font-medium text-slate-900 dark:text-white">{webhook.url}</p>
                                <p className="text-xs text-slate-500">Events: {webhook.events.join(', ')}</p>
                              </div>
                            </div>
                            <div className="flex items-center gap-2">
                              <Badge className={webhook.active ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400'}>
                                {webhook.active ? 'Active' : 'Inactive'}
                              </Badge>
                              <Button
                                variant="ghost"
                                size="sm"
                                className="text-red-600 hover:text-red-700"
                                onClick={() => deleteWebhookMutation.mutate(webhook.id)}
                                disabled={deleteWebhookMutation.isPending}
                              >
                                <Trash2 className="h-4 w-4" />
                              </Button>
                            </div>
                          </div>
                        ))
                      ) : (
                        <div className="flex h-20 items-center justify-center text-sm text-slate-400">
                          No webhooks configured
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        {/* ===== Performance Tab ===== */}
        <TabsContent value="performance">
          <div className="space-y-4">
            {/* GPU Status */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Monitor className="h-5 w-5" />
                  GPU Status
                </CardTitle>
                <CardDescription>Real-time GPU device monitoring</CardDescription>
              </CardHeader>
              <CardContent>
                {performanceLoading ? (
                  <div className="flex h-32 items-center justify-center">
                    <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
                  </div>
                ) : performanceData?.gpu_devices && performanceData.gpu_devices.length > 0 ? (
                  <div className="grid gap-4 sm:grid-cols-2">
                    {performanceData.gpu_devices.map((gpu) => {
                      const memoryPercent = Math.round((gpu.memory_used / gpu.memory_total) * 100);
                      const memoryUsedGB = (gpu.memory_used / 1024).toFixed(1);
                      const memoryTotalGB = (gpu.memory_total / 1024).toFixed(1);

                      return (
                        <div key={gpu.id} className="rounded-lg border p-4">
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2">
                              <Cpu className="h-4 w-4 text-blue-600" />
                              <span className="text-sm font-medium text-slate-900 dark:text-white">{gpu.name}</span>
                            </div>
                            <Badge className={cn(
                              'text-xs',
                              gpu.temperature < 70
                                ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                                : gpu.temperature < 85
                                  ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400'
                                  : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
                            )}>
                              {gpu.temperature}C
                            </Badge>
                          </div>
                          <div className="mt-3 space-y-2">
                            <div className="flex justify-between text-xs text-slate-500">
                              <span>Utilization</span>
                              <span>{gpu.utilization}%</span>
                            </div>
                            <div className="h-2 rounded-full bg-slate-100 dark:bg-slate-700">
                              <div
                                className={cn(
                                  'h-full rounded-full transition-all',
                                  gpu.utilization < 70 ? 'bg-green-500' : gpu.utilization < 90 ? 'bg-amber-500' : 'bg-red-500'
                                )}
                                style={{ width: `${gpu.utilization}%` }}
                              />
                            </div>
                            <div className="flex justify-between text-xs text-slate-500">
                              <span>Memory</span>
                              <span>{memoryUsedGB} / {memoryTotalGB} GB ({memoryPercent}%)</span>
                            </div>
                            <div className="h-2 rounded-full bg-slate-100 dark:bg-slate-700">
                              <div
                                className={cn(
                                  'h-full rounded-full transition-all',
                                  memoryPercent < 70 ? 'bg-blue-500' : memoryPercent < 90 ? 'bg-amber-500' : 'bg-red-500'
                                )}
                                style={{ width: `${memoryPercent}%` }}
                              />
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="flex h-32 items-center justify-center text-sm text-slate-400">
                    <div className="text-center">
                      <Cpu className="mx-auto mb-2 h-8 w-8" />
                      No GPU devices detected
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Inference Settings */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Gauge className="h-5 w-5" />
                  Inference Settings
                </CardTitle>
                <CardDescription>Configure AI inference and processing parameters</CardDescription>
              </CardHeader>
              <CardContent>
                {performanceLoading ? (
                  <div className="flex h-40 items-center justify-center">
                    <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
                  </div>
                ) : (
                  <div className="space-y-6">
                    <div className="flex items-center justify-between">
                      <div>
                        <Label>GPU Acceleration</Label>
                        <p className="text-xs text-slate-500">Use GPU for inference when available</p>
                      </div>
                      <Switch
                        checked={performanceForm.enable_gpu_acceleration}
                        onCheckedChange={(v) => setPerformanceForm((prev) => ({ ...prev, enable_gpu_acceleration: v }))}
                      />
                    </div>

                    <div className="grid gap-4 sm:grid-cols-2">
                      <div className="space-y-2">
                        <Label htmlFor="batch-size">Inference Batch Size</Label>
                        <Input
                          id="batch-size"
                          type="number"
                          min={1}
                          max={64}
                          value={performanceForm.inference_batch_size}
                          onChange={(e) => setPerformanceForm((prev) => ({ ...prev, inference_batch_size: Number(e.target.value) }))}
                        />
                        <p className="text-xs text-slate-500">Higher values use more memory but improve throughput</p>
                      </div>

                      <div className="space-y-2">
                        <Label htmlFor="confidence">Confidence Threshold</Label>
                        <Input
                          id="confidence"
                          type="number"
                          min={0.1}
                          max={1}
                          step={0.05}
                          value={performanceForm.inference_confidence_threshold}
                          onChange={(e) => setPerformanceForm((prev) => ({ ...prev, inference_confidence_threshold: Number(e.target.value) }))}
                        />
                        <p className="text-xs text-slate-500">Minimum confidence score (0.1 - 1.0)</p>
                      </div>

                      <div className="space-y-2">
                        <Label htmlFor="max-cameras">Max Concurrent Cameras</Label>
                        <Input
                          id="max-cameras"
                          type="number"
                          min={1}
                          max={256}
                          value={performanceForm.max_concurrent_cameras}
                          onChange={(e) => setPerformanceForm((prev) => ({ ...prev, max_concurrent_cameras: Number(e.target.value) }))}
                        />
                        <p className="text-xs text-slate-500">Limit of simultaneously processed camera streams</p>
                      </div>

                      <div className="space-y-2">
                        <Label htmlFor="frame-skip">Frame Skip Interval</Label>
                        <Input
                          id="frame-skip"
                          type="number"
                          min={1}
                          max={30}
                          value={performanceForm.frame_skip_interval}
                          onChange={(e) => setPerformanceForm((prev) => ({ ...prev, frame_skip_interval: Number(e.target.value) }))}
                        />
                        <p className="text-xs text-slate-500">Process every Nth frame (1 = every frame)</p>
                      </div>
                    </div>

                    <Separator />

                    <div className="flex justify-end">
                      <Button onClick={() => savePerformanceMutation.mutate(performanceForm)} disabled={savePerformanceMutation.isPending}>
                        {savePerformanceMutation.isPending ? (
                          <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                        ) : (
                          <Save className="mr-1 h-4 w-4" />
                        )}
                        Save Changes
                      </Button>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
