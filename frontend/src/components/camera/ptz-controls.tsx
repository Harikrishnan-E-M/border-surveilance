"use client";

import React, { useState } from "react";
import {
  ArrowUp,
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ZoomIn,
  ZoomOut,
  Home,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";

interface PTZControlsProps {
  onMove?: (direction: "up" | "down" | "left" | "right") => void;
  onZoom?: (direction: "in" | "out") => void;
  onPreset?: (preset: number) => void;
  onHome?: () => void;
  onSpeedChange?: (speed: number) => void;
  disabled?: boolean;
  className?: string;
}

export function PTZControls({
  onMove,
  onZoom,
  onPreset,
  onHome,
  onSpeedChange,
  disabled = false,
  className,
}: PTZControlsProps) {
  const [speed, setSpeed] = useState(50);

  const handleSpeedChange = (value: number) => {
    setSpeed(value);
    onSpeedChange?.(value);
  };

  const presets = Array.from({ length: 8 }, (_, i) => i + 1);

  return (
    <div className={cn("flex flex-col gap-4 rounded-lg border bg-card p-4", className)}>
      <h3 className="text-sm font-semibold">PTZ Controls</h3>

      {/* Directional Pad */}
      <div className="flex flex-col items-center gap-1">
        <Button
          variant="outline"
          size="icon"
          className="h-10 w-10"
          disabled={disabled}
          onClick={() => onMove?.("up")}
          onMouseDown={() => onMove?.("up")}
        >
          <ArrowUp className="h-5 w-5" />
        </Button>
        <div className="flex items-center gap-1">
          <Button
            variant="outline"
            size="icon"
            className="h-10 w-10"
            disabled={disabled}
            onClick={() => onMove?.("left")}
          >
            <ArrowLeft className="h-5 w-5" />
          </Button>
          <Button
            variant="outline"
            size="icon"
            className="h-10 w-10"
            disabled={disabled}
            onClick={() => onHome?.()}
          >
            <Home className="h-4 w-4" />
          </Button>
          <Button
            variant="outline"
            size="icon"
            className="h-10 w-10"
            disabled={disabled}
            onClick={() => onMove?.("right")}
          >
            <ArrowRight className="h-5 w-5" />
          </Button>
        </div>
        <Button
          variant="outline"
          size="icon"
          className="h-10 w-10"
          disabled={disabled}
          onClick={() => onMove?.("down")}
        >
          <ArrowDown className="h-5 w-5" />
        </Button>
      </div>

      {/* Zoom Controls */}
      <div className="flex items-center justify-center gap-2">
        <Button
          variant="outline"
          size="sm"
          className="gap-1.5"
          disabled={disabled}
          onClick={() => onZoom?.("out")}
        >
          <ZoomOut className="h-4 w-4" />
          Zoom -
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="gap-1.5"
          disabled={disabled}
          onClick={() => onZoom?.("in")}
        >
          <ZoomIn className="h-4 w-4" />
          Zoom +
        </Button>
      </div>

      {/* Speed Slider */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label className="text-xs">Speed</Label>
          <span className="text-xs text-muted-foreground">{speed}%</span>
        </div>
        <input
          type="range"
          min={1}
          max={100}
          value={speed}
          onChange={(e) => handleSpeedChange(Number(e.target.value))}
          disabled={disabled}
          className="w-full accent-primary h-2 rounded-lg appearance-none bg-secondary cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
        />
        <div className="flex justify-between text-2xs text-muted-foreground">
          <span>Slow</span>
          <span>Fast</span>
        </div>
      </div>

      {/* Presets */}
      <div className="space-y-2">
        <Label className="text-xs">Presets</Label>
        <div className="grid grid-cols-4 gap-1.5">
          {presets.map((preset) => (
            <Button
              key={preset}
              variant="outline"
              size="sm"
              className="h-8 text-xs"
              disabled={disabled}
              onClick={() => onPreset?.(preset)}
            >
              {preset}
            </Button>
          ))}
        </div>
      </div>
    </div>
  );
}
