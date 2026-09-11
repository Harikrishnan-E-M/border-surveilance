'use client';

import { useState, useCallback } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2, Layers } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { apiClient } from '@/lib/api-client';
import { ZoneEditor, type Zone as EditorZone } from '@/components/camera/zone-editor';

interface CreateZoneDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  cameraId: string;
  snapshotUrl: string;
}

export function CreateZoneDialog({
  open,
  onOpenChange,
  cameraId,
  snapshotUrl,
}: CreateZoneDialogProps) {
  const queryClient = useQueryClient();
  const [saving, setSaving] = useState(false);

  const saveZonesMutation = useMutation({
    mutationFn: async (zones: EditorZone[]) => {
      // Save each zone to the API
      const results = [];
      for (const zone of zones) {
        // Convert points {x,y} to normalized [[x,y]] polygon
        const maxDim = 1000; // canvas coordinates
        const polygon = zone.points.map((p) => [
          Math.round((p.x / maxDim) * 10000) / 10000,
          Math.round((p.y / maxDim) * 10000) / 10000,
        ]);

        const res = await apiClient.post(`/api/v1/cameras/${cameraId}/zones`, {
          name: zone.name,
          zone_type: zone.type,
          coordinates: polygon,
          color: zone.color,
        });
        results.push(res.data);
      }
      return results;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['cameras', cameraId, 'zones'] });
      onOpenChange(false);
    },
  });

  const handleSave = useCallback(
    (zones: EditorZone[]) => {
      if (zones.length === 0) {
        onOpenChange(false);
        return;
      }
      setSaving(true);
      saveZonesMutation.mutate(zones, {
        onSettled: () => setSaving(false),
      });
    },
    [saveZonesMutation, onOpenChange]
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Layers className="h-5 w-5" />
            Create Detection Zones
          </DialogTitle>
          <DialogDescription>
            Draw polygon zones on the camera view. Click to add points, click near
            the first point to close the polygon.
          </DialogDescription>
        </DialogHeader>

        {saving && (
          <div className="flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-700 dark:border-blue-800 dark:bg-blue-900/30 dark:text-blue-400">
            <Loader2 className="h-4 w-4 animate-spin" />
            Saving zones...
          </div>
        )}

        {saveZonesMutation.isError && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">
            Failed to save zones. Please try again.
          </div>
        )}

        <ZoneEditor
          imageUrl={snapshotUrl}
          onSave={handleSave}
          onCancel={() => onOpenChange(false)}
        />
      </DialogContent>
    </Dialog>
  );
}
