"use client";

import React from "react";
import Image from "next/image";
import {
  Check,
  XCircle,
  Flag,
  Clock,
  Camera,
  Film,
  Download,
  ExternalLink,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { formatDate, formatRelativeTime } from "@/lib/utils";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import type { AlertData, AlertSeverity, AlertStatus } from "@/components/alerts/alert-card";

interface StatusChange {
  status: AlertStatus;
  timestamp: string;
  user?: string;
  note?: string;
}

interface AlertDetailModalProps {
  alert: AlertData | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  statusHistory?: StatusChange[];
  onAcknowledge?: (alert: AlertData) => void;
  onResolve?: (alert: AlertData) => void;
  onFalsePositive?: (alert: AlertData) => void;
}

const severityColors: Record<AlertSeverity, string> = {
  critical: "text-red-600 dark:text-red-400",
  high: "text-orange-600 dark:text-orange-400",
  medium: "text-yellow-600 dark:text-yellow-400",
  low: "text-blue-600 dark:text-blue-400",
  info: "text-gray-600 dark:text-gray-400",
};

const statusLabels: Record<AlertStatus, string> = {
  new: "New",
  acknowledged: "Acknowledged",
  resolved: "Resolved",
  false_positive: "False Positive",
};

export function AlertDetailModal({
  alert,
  open,
  onOpenChange,
  statusHistory = [],
  onAcknowledge,
  onResolve,
  onFalsePositive,
}: AlertDetailModalProps) {
  if (!alert) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <div className="flex items-center gap-2">
            <Badge variant={alert.severity as AlertSeverity}>
              {alert.severity}
            </Badge>
            <Badge variant="outline">{statusLabels[alert.status]}</Badge>
          </div>
          <DialogTitle className="text-xl">{alert.title}</DialogTitle>
          <DialogDescription>{alert.description}</DialogDescription>
        </DialogHeader>

        {/* Snapshot image */}
        {alert.snapshotUrl && (
          <div className="relative aspect-video w-full overflow-hidden rounded-lg bg-muted">
            <Image
              src={alert.snapshotUrl}
              alt="Alert snapshot"
              fill
              className="object-contain"
              sizes="(max-width: 768px) 100vw, 640px"
            />
          </div>
        )}

        {/* Alert details */}
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">Camera</p>
            <div className="flex items-center gap-1.5">
              <Camera className="h-3.5 w-3.5" />
              <span>{alert.cameraName}</span>
            </div>
          </div>
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">Alert Type</p>
            <p className="capitalize">{alert.type.replace(/_/g, " ")}</p>
          </div>
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">Timestamp</p>
            <div className="flex items-center gap-1.5">
              <Clock className="h-3.5 w-3.5" />
              <span>{formatDate(alert.timestamp)}</span>
            </div>
          </div>
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">Severity</p>
            <span className={cn("font-medium capitalize", severityColors[alert.severity])}>
              {alert.severity}
            </span>
          </div>
        </div>

        <Separator />

        {/* Timeline / Status History */}
        {statusHistory.length > 0 && (
          <div className="space-y-3">
            <h4 className="text-sm font-semibold">Status Timeline</h4>
            <div className="relative ml-3 border-l pl-6 space-y-4">
              {statusHistory.map((change, index) => (
                <div key={index} className="relative">
                  <div className="absolute -left-[29px] flex h-5 w-5 items-center justify-center rounded-full border bg-background">
                    <div
                      className={cn(
                        "h-2.5 w-2.5 rounded-full",
                        change.status === "new" && "bg-blue-500",
                        change.status === "acknowledged" && "bg-yellow-500",
                        change.status === "resolved" && "bg-green-500",
                        change.status === "false_positive" && "bg-gray-500"
                      )}
                    />
                  </div>
                  <div>
                    <p className="text-sm font-medium">
                      {statusLabels[change.status]}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {formatDate(change.timestamp)}
                      {change.user && ` by ${change.user}`}
                    </p>
                    {change.note && (
                      <p className="mt-1 text-xs text-muted-foreground">
                        {change.note}
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Evidence section */}
        {alert.videoClipUrl && (
          <>
            <Separator />
            <div className="space-y-2">
              <h4 className="text-sm font-semibold">Evidence</h4>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" className="gap-1.5" asChild>
                  <a href={alert.videoClipUrl} target="_blank" rel="noopener noreferrer">
                    <Film className="h-4 w-4" />
                    View Video Clip
                    <ExternalLink className="h-3 w-3" />
                  </a>
                </Button>
                <Button variant="outline" size="sm" className="gap-1.5" asChild>
                  <a href={alert.videoClipUrl} download>
                    <Download className="h-4 w-4" />
                    Download Clip
                  </a>
                </Button>
              </div>
            </div>
          </>
        )}

        {/* Actions */}
        <DialogFooter className="gap-2 sm:gap-0">
          {alert.status === "new" && (
            <>
              <Button
                variant="outline"
                className="gap-1.5"
                onClick={() => onAcknowledge?.(alert)}
              >
                <Check className="h-4 w-4" />
                Acknowledge
              </Button>
              <Button
                variant="outline"
                className="gap-1.5"
                onClick={() => onFalsePositive?.(alert)}
              >
                <Flag className="h-4 w-4" />
                False Positive
              </Button>
              <Button className="gap-1.5" onClick={() => onResolve?.(alert)}>
                <XCircle className="h-4 w-4" />
                Resolve
              </Button>
            </>
          )}
          {alert.status === "acknowledged" && (
            <>
              <Button
                variant="outline"
                className="gap-1.5"
                onClick={() => onFalsePositive?.(alert)}
              >
                <Flag className="h-4 w-4" />
                False Positive
              </Button>
              <Button className="gap-1.5" onClick={() => onResolve?.(alert)}>
                <XCircle className="h-4 w-4" />
                Resolve
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
