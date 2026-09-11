"use client";

import React from "react";
import Image from "next/image";
import {
  AlertTriangle,
  AlertCircle,
  Info,
  ShieldAlert,
  UserX,
  Flame,
  Car,
  HardHat,
  Eye,
  Check,
  XCircle,
  Flag,
  Clock,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { formatRelativeTime } from "@/lib/utils";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export type AlertSeverity = "critical" | "high" | "medium" | "low" | "info";
export type AlertStatus = "new" | "acknowledged" | "resolved" | "false_positive";

export interface AlertData {
  id: string;
  title: string;
  description: string;
  severity: AlertSeverity;
  status: AlertStatus;
  type: string;
  cameraName: string;
  cameraId: string;
  timestamp: string;
  snapshotUrl?: string;
  videoClipUrl?: string;
}

interface AlertCardProps {
  alert: AlertData;
  onAcknowledge?: (alert: AlertData) => void;
  onResolve?: (alert: AlertData) => void;
  onFalsePositive?: (alert: AlertData) => void;
  onClick?: (alert: AlertData) => void;
  compact?: boolean;
  className?: string;
}

const severityConfig: Record<
  AlertSeverity,
  { stripe: string; badge: AlertSeverity }
> = {
  critical: { stripe: "bg-red-500", badge: "critical" },
  high: { stripe: "bg-orange-500", badge: "high" },
  medium: { stripe: "bg-yellow-500", badge: "medium" },
  low: { stripe: "bg-blue-500", badge: "low" },
  info: { stripe: "bg-gray-500", badge: "info" },
};

const typeIcons: Record<string, React.ElementType> = {
  intrusion: ShieldAlert,
  fire: Flame,
  face_unknown: UserX,
  vehicle: Car,
  ppe_violation: HardHat,
  loitering: Eye,
  crowd: AlertTriangle,
  default: AlertCircle,
};

const statusConfig: Record<AlertStatus, { label: string; variant: "default" | "secondary" | "outline" }> = {
  new: { label: "New", variant: "default" },
  acknowledged: { label: "Acknowledged", variant: "secondary" },
  resolved: { label: "Resolved", variant: "outline" },
  false_positive: { label: "False Positive", variant: "outline" },
};

export function AlertCard({
  alert,
  onAcknowledge,
  onResolve,
  onFalsePositive,
  onClick,
  compact = false,
  className,
}: AlertCardProps) {
  const severity = severityConfig[alert.severity];
  const TypeIcon = typeIcons[alert.type] || typeIcons.default;
  const statusInfo = statusConfig[alert.status];

  return (
    <Card
      className={cn(
        "relative overflow-hidden transition-shadow hover:shadow-md cursor-pointer",
        className
      )}
      onClick={() => onClick?.(alert)}
    >
      {/* Severity stripe */}
      <div className={cn("absolute left-0 top-0 bottom-0 w-1", severity.stripe)} />

      <div className={cn("flex gap-3 p-4 pl-5", compact && "p-3 pl-4")}>
        {/* Snapshot thumbnail */}
        {alert.snapshotUrl && !compact && (
          <div className="relative h-20 w-28 shrink-0 overflow-hidden rounded-md bg-muted">
            <Image
              src={alert.snapshotUrl}
              alt="Alert snapshot"
              fill
              className="object-cover"
              sizes="112px"
            />
          </div>
        )}

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2">
            <div className="flex items-center gap-2 min-w-0">
              <TypeIcon className={cn("h-4 w-4 shrink-0", compact ? "h-3.5 w-3.5" : "")} />
              <h4
                className={cn(
                  "font-semibold truncate",
                  compact ? "text-xs" : "text-sm"
                )}
              >
                {alert.title}
              </h4>
            </div>
            <div className="flex items-center gap-1.5 shrink-0">
              <Badge variant={severity.badge} className="text-2xs">
                {alert.severity}
              </Badge>
              <Badge variant={statusInfo.variant} className="text-2xs">
                {statusInfo.label}
              </Badge>
            </div>
          </div>

          {!compact && (
            <p className="mt-1 text-xs text-muted-foreground line-clamp-2">
              {alert.description}
            </p>
          )}

          <div className="mt-1.5 flex items-center gap-3 text-xs text-muted-foreground">
            <span className="truncate">{alert.cameraName}</span>
            <span className="flex items-center gap-1 shrink-0">
              <Clock className="h-3 w-3" />
              {formatRelativeTime(alert.timestamp)}
            </span>
          </div>

          {/* Action buttons */}
          {!compact && alert.status === "new" && (
            <div className="mt-2.5 flex gap-1.5">
              <Button
                variant="outline"
                size="sm"
                className="h-7 text-xs gap-1"
                onClick={(e) => {
                  e.stopPropagation();
                  onAcknowledge?.(alert);
                }}
              >
                <Check className="h-3 w-3" />
                Acknowledge
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="h-7 text-xs gap-1"
                onClick={(e) => {
                  e.stopPropagation();
                  onResolve?.(alert);
                }}
              >
                <XCircle className="h-3 w-3" />
                Resolve
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs gap-1 text-muted-foreground"
                onClick={(e) => {
                  e.stopPropagation();
                  onFalsePositive?.(alert);
                }}
              >
                <Flag className="h-3 w-3" />
                False Positive
              </Button>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}
