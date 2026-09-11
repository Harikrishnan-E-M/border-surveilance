'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, ShieldCheck } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { apiClient } from '@/lib/api-client';

const RULE_TYPES = [
  { value: 'intrusion_detection', label: 'Intrusion Detection', category: 'Security' },
  { value: 'loitering', label: 'Loitering Detection', category: 'Security' },
  { value: 'crowd_formation', label: 'Crowd Formation', category: 'Security' },
  { value: 'object_left_behind', label: 'Object Left Behind', category: 'Security' },
  { value: 'object_removed', label: 'Object Removed', category: 'Security' },
  { value: 'wrong_direction', label: 'Wrong Direction', category: 'Security' },
  { value: 'line_crossing', label: 'Line Crossing', category: 'Counting' },
  { value: 'face_recognized', label: 'Face Recognized', category: 'Face Recognition' },
  { value: 'face_unknown', label: 'Unknown Face', category: 'Face Recognition' },
  { value: 'face_blacklisted', label: 'Blacklisted Face', category: 'Face Recognition' },
  { value: 'anpr_blacklisted', label: 'Blacklisted Vehicle', category: 'Vehicle' },
  { value: 'anpr_unknown', label: 'Unknown Vehicle', category: 'Vehicle' },
  { value: 'ppe_violation', label: 'PPE Violation', category: 'Safety' },
  { value: 'fire_smoke', label: 'Fire / Smoke', category: 'Safety' },
  { value: 'fall_detection', label: 'Fall Detection', category: 'Safety' },
  { value: 'violence_detection', label: 'Violence Detection', category: 'Safety' },
  { value: 'occupancy_threshold', label: 'Occupancy Threshold', category: 'Occupancy' },
  { value: 'camera_tamper', label: 'Camera Tamper', category: 'System' },
  { value: 'no_entry_zone', label: 'No Entry Zone', category: 'Security' },
  { value: 'tailgating', label: 'Tailgating', category: 'Security' },
  { value: 'speed_violation', label: 'Speed Violation', category: 'Vehicle' },
  { value: 'illegal_parking', label: 'Illegal Parking', category: 'Vehicle' },
] as const;

const SEVERITIES = [
  { value: 'critical', label: 'Critical', color: 'bg-red-100 text-red-700' },
  { value: 'high', label: 'High', color: 'bg-orange-100 text-orange-700' },
  { value: 'medium', label: 'Medium', color: 'bg-amber-100 text-amber-700' },
  { value: 'low', label: 'Low', color: 'bg-blue-100 text-blue-700' },
  { value: 'info', label: 'Info', color: 'bg-slate-100 text-slate-700' },
] as const;

interface Zone {
  id: string;
  name: string;
  type: string;
}

interface CreateRuleDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  cameraId: string;
}

export function CreateRuleDialog({
  open,
  onOpenChange,
  cameraId,
}: CreateRuleDialogProps) {
  const queryClient = useQueryClient();

  const [ruleType, setRuleType] = useState('');
  const [severity, setSeverity] = useState('medium');
  const [zoneId, setZoneId] = useState<string>('none');
  const [cooldown, setCooldown] = useState(300);
  const [scheduleCron, setScheduleCron] = useState('');

  // Type-specific parameters
  const [confidenceThreshold, setConfidenceThreshold] = useState(0.7);
  const [loiteringDuration, setLoiteringDuration] = useState(30);
  const [crowdThreshold, setCrowdThreshold] = useState(10);
  const [occupancyLimit, setOccupancyLimit] = useState(50);
  const [speedLimit, setSpeedLimit] = useState(30);

  // Fetch zones for this camera
  const { data: zones } = useQuery<Zone[]>({
    queryKey: ['cameras', cameraId, 'zones'],
    queryFn: async () => {
      const res = await apiClient.get(`/api/v1/cameras/${cameraId}/zones`);
      return res.data;
    },
    enabled: open,
  });

  const createRuleMutation = useMutation({
    mutationFn: async (data: Record<string, unknown>) => {
      const res = await apiClient.post('/api/v1/rules', data);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['cameras', cameraId, 'rules'] });
      resetForm();
      onOpenChange(false);
    },
  });

  const resetForm = () => {
    setRuleType('');
    setSeverity('medium');
    setZoneId('none');
    setCooldown(300);
    setScheduleCron('');
    setConfidenceThreshold(0.7);
    setLoiteringDuration(30);
    setCrowdThreshold(10);
    setOccupancyLimit(50);
    setSpeedLimit(30);
  };

  const buildParameters = (): Record<string, unknown> => {
    const params: Record<string, unknown> = {
      confidence_threshold: confidenceThreshold,
    };

    switch (ruleType) {
      case 'loitering':
        params.duration_seconds = loiteringDuration;
        break;
      case 'crowd_formation':
        params.max_people = crowdThreshold;
        break;
      case 'occupancy_threshold':
        params.max_occupancy = occupancyLimit;
        break;
      case 'speed_violation':
        params.speed_limit_kmh = speedLimit;
        break;
      case 'ppe_violation':
        params.required_ppe = ['helmet', 'vest'];
        break;
    }

    return params;
  };

  const handleSubmit = () => {
    if (!ruleType) return;

    const payload: Record<string, unknown> = {
      camera_id: cameraId,
      rule_type: ruleType,
      severity,
      parameters: buildParameters(),
      cooldown_seconds: cooldown,
    };

    if (zoneId && zoneId !== 'none') {
      payload.zone_id = zoneId;
    }
    if (scheduleCron.trim()) {
      payload.schedule_cron = scheduleCron.trim();
    }

    createRuleMutation.mutate(payload);
  };

  // Group rule types by category
  const categories = RULE_TYPES.reduce(
    (acc, rt) => {
      if (!acc[rt.category]) acc[rt.category] = [];
      acc[rt.category].push(rt);
      return acc;
    },
    {} as Record<string, typeof RULE_TYPES[number][]>
  );

  const selectedRule = RULE_TYPES.find((r) => r.value === ruleType);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5" />
            Create Detection Rule
          </DialogTitle>
          <DialogDescription>
            Configure a detection rule for this camera. Rules trigger alerts when
            specific events are detected.
          </DialogDescription>
        </DialogHeader>

        {createRuleMutation.isError && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">
            Failed to create rule. Please check your configuration.
          </div>
        )}

        <div className="space-y-4">
          {/* Detection Type */}
          <div className="space-y-2">
            <Label>Detection Type *</Label>
            <Select value={ruleType} onValueChange={setRuleType}>
              <SelectTrigger>
                <SelectValue placeholder="Select detection type..." />
              </SelectTrigger>
              <SelectContent className="max-h-80">
                {Object.entries(categories).map(([category, types]) => (
                  <div key={category}>
                    <div className="px-2 py-1.5 text-xs font-semibold text-muted-foreground">
                      {category}
                    </div>
                    {types.map((t) => (
                      <SelectItem key={t.value} value={t.value}>
                        {t.label}
                      </SelectItem>
                    ))}
                  </div>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Severity */}
          <div className="space-y-2">
            <Label>Severity</Label>
            <Select value={severity} onValueChange={setSeverity}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SEVERITIES.map((s) => (
                  <SelectItem key={s.value} value={s.value}>
                    <span className={`inline-block rounded px-1.5 py-0.5 text-xs font-medium ${s.color}`}>
                      {s.label}
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Zone */}
          <div className="space-y-2">
            <Label>Zone (optional)</Label>
            <Select value={zoneId} onValueChange={setZoneId}>
              <SelectTrigger>
                <SelectValue placeholder="All camera area" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">Entire camera view</SelectItem>
                {zones?.map((zone) => (
                  <SelectItem key={zone.id} value={zone.id}>
                    {zone.name} ({zone.type})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Type-specific parameters */}
          {ruleType && (
            <div className="space-y-3 rounded-lg border border-slate-200 p-3 dark:border-slate-700">
              <p className="text-xs font-medium text-muted-foreground">
                Parameters for {selectedRule?.label}
              </p>

              {/* Confidence threshold — common to all */}
              <div className="space-y-1">
                <Label className="text-xs">Confidence Threshold</Label>
                <div className="flex items-center gap-2">
                  <input
                    type="range"
                    min="0.1"
                    max="1.0"
                    step="0.05"
                    value={confidenceThreshold}
                    onChange={(e) => setConfidenceThreshold(parseFloat(e.target.value))}
                    className="flex-1"
                  />
                  <span className="w-12 text-right text-sm font-medium">
                    {(confidenceThreshold * 100).toFixed(0)}%
                  </span>
                </div>
              </div>

              {/* Loitering duration */}
              {ruleType === 'loitering' && (
                <div className="space-y-1">
                  <Label className="text-xs">Duration Threshold (seconds)</Label>
                  <Input
                    type="number"
                    min={5}
                    max={600}
                    value={loiteringDuration}
                    onChange={(e) => setLoiteringDuration(parseInt(e.target.value) || 30)}
                  />
                </div>
              )}

              {/* Crowd threshold */}
              {ruleType === 'crowd_formation' && (
                <div className="space-y-1">
                  <Label className="text-xs">Max People Before Alert</Label>
                  <Input
                    type="number"
                    min={2}
                    max={500}
                    value={crowdThreshold}
                    onChange={(e) => setCrowdThreshold(parseInt(e.target.value) || 10)}
                  />
                </div>
              )}

              {/* Occupancy threshold */}
              {ruleType === 'occupancy_threshold' && (
                <div className="space-y-1">
                  <Label className="text-xs">Max Occupancy</Label>
                  <Input
                    type="number"
                    min={1}
                    max={10000}
                    value={occupancyLimit}
                    onChange={(e) => setOccupancyLimit(parseInt(e.target.value) || 50)}
                  />
                </div>
              )}

              {/* Speed limit */}
              {ruleType === 'speed_violation' && (
                <div className="space-y-1">
                  <Label className="text-xs">Speed Limit (km/h)</Label>
                  <Input
                    type="number"
                    min={5}
                    max={300}
                    value={speedLimit}
                    onChange={(e) => setSpeedLimit(parseInt(e.target.value) || 30)}
                  />
                </div>
              )}
            </div>
          )}

          {/* Cooldown */}
          <div className="space-y-2">
            <Label>Alert Cooldown (seconds)</Label>
            <Input
              type="number"
              min={0}
              max={86400}
              value={cooldown}
              onChange={(e) => setCooldown(parseInt(e.target.value) || 0)}
              placeholder="300"
            />
            <p className="text-xs text-muted-foreground">
              Minimum interval between repeated alerts of this type
            </p>
          </div>

          {/* Schedule */}
          <div className="space-y-2">
            <Label>Schedule (optional cron)</Label>
            <Input
              value={scheduleCron}
              onChange={(e) => setScheduleCron(e.target.value)}
              placeholder="* * * * * (always active)"
            />
            <p className="text-xs text-muted-foreground">
              Leave blank for 24/7 monitoring. Use cron syntax for specific schedules.
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={!ruleType || createRuleMutation.isPending}
          >
            {createRuleMutation.isPending ? (
              <>
                <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                Creating...
              </>
            ) : (
              'Create Rule'
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
