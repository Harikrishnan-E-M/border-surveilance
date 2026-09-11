"use client";

import React, { useState } from "react";
import Image from "next/image";
import { Clock, Layers } from "lucide-react";
import { cn } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";

interface HeatmapOverlayProps {
  backgroundImageUrl: string;
  heatmapImageUrl: string;
  title?: string;
  timeRange?: { start: string; end: string };
  className?: string;
}

export function HeatmapOverlay({
  backgroundImageUrl,
  heatmapImageUrl,
  title = "Heatmap",
  timeRange,
  className,
}: HeatmapOverlayProps) {
  const [opacity, setOpacity] = useState(0.6);

  return (
    <Card className={cn(className)}>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base">
            <Layers className="h-4 w-4" />
            {title}
          </CardTitle>
          {timeRange && (
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Clock className="h-3.5 w-3.5" />
              <span>
                {timeRange.start} - {timeRange.end}
              </span>
            </div>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Image container */}
        <div className="relative aspect-video w-full overflow-hidden rounded-lg bg-muted">
          {/* Background camera image */}
          <Image
            src={backgroundImageUrl}
            alt="Camera view"
            fill
            className="object-cover"
            sizes="(max-width: 768px) 100vw, 640px"
          />

          {/* Heatmap overlay */}
          <Image
            src={heatmapImageUrl}
            alt="Heatmap overlay"
            fill
            className="object-cover mix-blend-multiply"
            style={{ opacity }}
            sizes="(max-width: 768px) 100vw, 640px"
          />

          {/* Color legend */}
          <div className="absolute bottom-3 right-3 rounded-md bg-background/80 p-2 backdrop-blur-sm">
            <div className="flex items-center gap-1.5">
              <div className="flex h-3 w-20 rounded-sm overflow-hidden">
                <div className="flex-1 bg-blue-500" />
                <div className="flex-1 bg-cyan-400" />
                <div className="flex-1 bg-green-400" />
                <div className="flex-1 bg-yellow-400" />
                <div className="flex-1 bg-orange-500" />
                <div className="flex-1 bg-red-500" />
              </div>
            </div>
            <div className="flex justify-between mt-0.5">
              <span className="text-[9px] text-muted-foreground">Low</span>
              <span className="text-[9px] text-muted-foreground">High</span>
            </div>
          </div>
        </div>

        {/* Opacity slider */}
        <div className="flex items-center gap-3">
          <Label className="text-xs shrink-0 w-16">Opacity</Label>
          <input
            type="range"
            min={0}
            max={100}
            value={Math.round(opacity * 100)}
            onChange={(e) => setOpacity(Number(e.target.value) / 100)}
            className="flex-1 accent-primary h-2 rounded-lg appearance-none bg-secondary cursor-pointer"
          />
          <span className="text-xs text-muted-foreground w-10 text-right">
            {Math.round(opacity * 100)}%
          </span>
        </div>
      </CardContent>
    </Card>
  );
}
