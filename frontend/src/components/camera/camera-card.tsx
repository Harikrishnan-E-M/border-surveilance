"use client";

import React from "react";
import Image from "next/image";
import {
  Camera,
  Eye,
  Pencil,
  Trash2,
  MoreVertical,
  MapPin,
  Wifi,
  WifiOff,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export interface CameraData {
  id: string;
  name: string;
  location: string;
  status: "online" | "offline" | "degraded";
  protocol: string;
  thumbnailUrl?: string;
  streamUrl?: string;
  fps?: number;
  resolution?: string;
}

interface CameraCardProps {
  camera: CameraData;
  onView?: (camera: CameraData) => void;
  onEdit?: (camera: CameraData) => void;
  onDelete?: (camera: CameraData) => void;
  className?: string;
}

const statusConfig: Record<
  CameraData["status"],
  { label: string; className: string; icon: React.ElementType }
> = {
  online: {
    label: "Online",
    className: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400",
    icon: Wifi,
  },
  offline: {
    label: "Offline",
    className: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
    icon: WifiOff,
  },
  degraded: {
    label: "Degraded",
    className: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400",
    icon: Wifi,
  },
};

export function CameraCard({
  camera,
  onView,
  onEdit,
  onDelete,
  className,
}: CameraCardProps) {
  const status = statusConfig[camera.status];
  const StatusIcon = status.icon;

  return (
    <Card className={cn("group overflow-hidden transition-shadow hover:shadow-md", className)}>
      {/* Thumbnail */}
      <div className="relative aspect-video bg-muted">
        {camera.thumbnailUrl ? (
          <Image
            src={camera.thumbnailUrl}
            alt={camera.name}
            fill
            className="object-cover"
            sizes="(max-width: 768px) 100vw, (max-width: 1200px) 50vw, 33vw"
          />
        ) : (
          <div className="flex h-full items-center justify-center">
            <Camera className="h-12 w-12 text-muted-foreground/40" />
          </div>
        )}

        {/* Status overlay */}
        <div className="absolute left-2 top-2">
          <div
            className={cn(
              "flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
              status.className
            )}
          >
            <StatusIcon className="h-3 w-3" />
            {status.label}
          </div>
        </div>

        {/* Hover overlay */}
        <div className="absolute inset-0 flex items-center justify-center bg-black/0 opacity-0 transition-all group-hover:bg-black/30 group-hover:opacity-100">
          <Button
            variant="secondary"
            size="sm"
            className="gap-1.5"
            onClick={() => onView?.(camera)}
          >
            <Eye className="h-4 w-4" />
            View
          </Button>
        </div>

        {/* Protocol badge */}
        <div className="absolute bottom-2 right-2">
          <Badge variant="secondary" className="text-2xs bg-background/80 backdrop-blur-sm">
            {camera.protocol}
          </Badge>
        </div>
      </div>

      {/* Info */}
      <CardContent className="p-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-sm font-semibold">{camera.name}</h3>
            <div className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
              <MapPin className="h-3 w-3 shrink-0" />
              <span className="truncate">{camera.location}</span>
            </div>
            {camera.resolution && (
              <p className="mt-0.5 text-xs text-muted-foreground">
                {camera.resolution}
                {camera.fps ? ` @ ${camera.fps}fps` : ""}
              </p>
            )}
          </div>

          {/* Actions dropdown */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0">
                <MoreVertical className="h-4 w-4" />
                <span className="sr-only">Camera actions</span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => onView?.(camera)}>
                <Eye className="mr-2 h-4 w-4" />
                View Stream
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => onEdit?.(camera)}>
                <Pencil className="mr-2 h-4 w-4" />
                Edit Camera
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onClick={() => onDelete?.(camera)}
                className="text-destructive focus:text-destructive"
              >
                <Trash2 className="mr-2 h-4 w-4" />
                Delete Camera
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </CardContent>
    </Card>
  );
}
