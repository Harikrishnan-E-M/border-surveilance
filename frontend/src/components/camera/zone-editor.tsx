"use client";

import React, { useRef, useState, useEffect, useCallback } from "react";
import { Trash2, Save, X, Plus } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export interface Point {
  x: number;
  y: number;
}

export interface Zone {
  id: string;
  name: string;
  type: string;
  color: string;
  points: Point[];
}

interface ZoneEditorProps {
  imageUrl: string;
  existingZones?: Zone[];
  onSave?: (zones: Zone[]) => void;
  onCancel?: () => void;
  className?: string;
}

const ZONE_TYPES = [
  { value: "detection", label: "Detection Zone" },
  { value: "exclusion", label: "Exclusion Zone" },
  { value: "counting", label: "Counting Line" },
  { value: "intrusion", label: "Intrusion Zone" },
  { value: "loitering", label: "Loitering Zone" },
  { value: "ppe", label: "PPE Zone" },
];

const COLORS = [
  "#3b82f6",
  "#ef4444",
  "#22c55e",
  "#f59e0b",
  "#8b5cf6",
  "#ec4899",
  "#06b6d4",
  "#f97316",
];

const SNAP_DISTANCE = 12;

export function ZoneEditor({
  imageUrl,
  existingZones = [],
  onSave,
  onCancel,
  className,
}: ZoneEditorProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);

  const [zones, setZones] = useState<Zone[]>(existingZones);
  const [currentPoints, setCurrentPoints] = useState<Point[]>([]);
  const [isDrawing, setIsDrawing] = useState(false);
  const [selectedZoneId, setSelectedZoneId] = useState<string | null>(null);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dragZoneId, setDragZoneId] = useState<string | null>(null);
  const [newZoneName, setNewZoneName] = useState("Zone " + (zones.length + 1));
  const [newZoneType, setNewZoneType] = useState("detection");
  const [newZoneColor, setNewZoneColor] = useState(COLORS[zones.length % COLORS.length]);
  const [imageLoaded, setImageLoaded] = useState(false);

  // Load image
  useEffect(() => {
    const img = new window.Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      imageRef.current = img;
      setImageLoaded(true);
    };
    img.src = imageUrl;
  }, [imageUrl]);

  // Get mouse position relative to canvas
  const getCanvasPoint = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>): Point => {
      const canvas = canvasRef.current!;
      const rect = canvas.getBoundingClientRect();
      return {
        x: ((e.clientX - rect.left) / rect.width) * canvas.width,
        y: ((e.clientY - rect.top) / rect.height) * canvas.height,
      };
    },
    []
  );

  // Draw everything on canvas
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    const img = imageRef.current;
    if (!canvas || !ctx || !img) return;

    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;

    // Draw image
    ctx.drawImage(img, 0, 0);

    // Draw existing zones
    zones.forEach((zone) => {
      if (zone.points.length < 2) return;

      ctx.beginPath();
      ctx.moveTo(zone.points[0].x, zone.points[0].y);
      zone.points.forEach((p, i) => {
        if (i > 0) ctx.lineTo(p.x, p.y);
      });
      ctx.closePath();

      // Fill
      ctx.fillStyle = zone.color + "33"; // 20% opacity
      ctx.fill();

      // Stroke
      ctx.strokeStyle = zone.color;
      ctx.lineWidth = zone.id === selectedZoneId ? 3 : 2;
      ctx.stroke();

      // Vertices
      zone.points.forEach((p) => {
        ctx.beginPath();
        ctx.arc(p.x, p.y, 5, 0, Math.PI * 2);
        ctx.fillStyle = zone.color;
        ctx.fill();
        ctx.strokeStyle = "#fff";
        ctx.lineWidth = 1.5;
        ctx.stroke();
      });

      // Label
      if (zone.points.length > 0) {
        const centroidX =
          zone.points.reduce((s, p) => s + p.x, 0) / zone.points.length;
        const centroidY =
          zone.points.reduce((s, p) => s + p.y, 0) / zone.points.length;

        ctx.font = "bold 14px sans-serif";
        const textWidth = ctx.measureText(zone.name).width;
        ctx.fillStyle = zone.color + "cc";
        ctx.fillRect(
          centroidX - textWidth / 2 - 4,
          centroidY - 10,
          textWidth + 8,
          20
        );
        ctx.fillStyle = "#fff";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(zone.name, centroidX, centroidY);
      }
    });

    // Draw current polygon being created
    if (currentPoints.length > 0) {
      ctx.beginPath();
      ctx.moveTo(currentPoints[0].x, currentPoints[0].y);
      currentPoints.forEach((p, i) => {
        if (i > 0) ctx.lineTo(p.x, p.y);
      });

      if (currentPoints.length > 1) {
        ctx.strokeStyle = newZoneColor;
        ctx.lineWidth = 2;
        ctx.setLineDash([6, 4]);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      // Vertices
      currentPoints.forEach((p, i) => {
        ctx.beginPath();
        ctx.arc(p.x, p.y, i === 0 ? 7 : 5, 0, Math.PI * 2);
        ctx.fillStyle = i === 0 ? "#fff" : newZoneColor;
        ctx.fill();
        ctx.strokeStyle = newZoneColor;
        ctx.lineWidth = 2;
        ctx.stroke();
      });
    }
  }, [zones, currentPoints, selectedZoneId, newZoneColor]);

  useEffect(() => {
    if (imageLoaded) draw();
  }, [imageLoaded, draw]);

  const handleCanvasClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const point = getCanvasPoint(e);

    if (!isDrawing) {
      // Check if clicking an existing zone
      for (const zone of zones) {
        for (const p of zone.points) {
          const dist = Math.sqrt((p.x - point.x) ** 2 + (p.y - point.y) ** 2);
          if (dist < SNAP_DISTANCE) {
            setSelectedZoneId(zone.id);
            draw();
            return;
          }
        }
      }
      setSelectedZoneId(null);
      draw();
      return;
    }

    // Check if clicking near first point to close polygon
    if (currentPoints.length >= 3) {
      const first = currentPoints[0];
      const dist = Math.sqrt(
        (first.x - point.x) ** 2 + (first.y - point.y) ** 2
      );
      if (dist < SNAP_DISTANCE) {
        // Close polygon - create zone
        const zone: Zone = {
          id: `zone_${Date.now()}`,
          name: newZoneName,
          type: newZoneType,
          color: newZoneColor,
          points: [...currentPoints],
        };
        setZones((prev) => [...prev, zone]);
        setCurrentPoints([]);
        setIsDrawing(false);
        setNewZoneName("Zone " + (zones.length + 2));
        setNewZoneColor(COLORS[(zones.length + 1) % COLORS.length]);
        draw();
        return;
      }
    }

    setCurrentPoints((prev) => [...prev, point]);
    draw();
  };

  const handleMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (isDrawing) return;
    const point = getCanvasPoint(e);

    // Check if clicking on a vertex for dragging
    for (const zone of zones) {
      for (let i = 0; i < zone.points.length; i++) {
        const p = zone.points[i];
        const dist = Math.sqrt((p.x - point.x) ** 2 + (p.y - point.y) ** 2);
        if (dist < SNAP_DISTANCE) {
          setDragIndex(i);
          setDragZoneId(zone.id);
          return;
        }
      }
    }
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (dragIndex === null || !dragZoneId) return;
    const point = getCanvasPoint(e);

    setZones((prev) =>
      prev.map((zone) => {
        if (zone.id !== dragZoneId) return zone;
        const newPoints = [...zone.points];
        newPoints[dragIndex] = point;
        return { ...zone, points: newPoints };
      })
    );
    draw();
  };

  const handleMouseUp = () => {
    setDragIndex(null);
    setDragZoneId(null);
  };

  const deleteSelectedZone = () => {
    if (!selectedZoneId) return;
    setZones((prev) => prev.filter((z) => z.id !== selectedZoneId));
    setSelectedZoneId(null);
  };

  const startDrawing = () => {
    setIsDrawing(true);
    setCurrentPoints([]);
    setSelectedZoneId(null);
  };

  const cancelDrawing = () => {
    setIsDrawing(false);
    setCurrentPoints([]);
  };

  return (
    <div className={cn("flex flex-col gap-4", className)}>
      {/* Toolbar */}
      <div className="flex flex-wrap items-end gap-3">
        {isDrawing ? (
          <>
            <div className="space-y-1">
              <Label className="text-xs">Zone Name</Label>
              <Input
                value={newZoneName}
                onChange={(e) => setNewZoneName(e.target.value)}
                className="h-9 w-40"
                placeholder="Zone name"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Zone Type</Label>
              <Select value={newZoneType} onValueChange={setNewZoneType}>
                <SelectTrigger className="h-9 w-40">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ZONE_TYPES.map((t) => (
                    <SelectItem key={t.value} value={t.value}>
                      {t.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Color</Label>
              <div className="flex gap-1">
                {COLORS.map((color) => (
                  <button
                    key={color}
                    className={cn(
                      "h-9 w-9 rounded-md border-2 transition-all",
                      newZoneColor === color
                        ? "border-foreground scale-110"
                        : "border-transparent"
                    )}
                    style={{ backgroundColor: color }}
                    onClick={() => setNewZoneColor(color)}
                  />
                ))}
              </div>
            </div>
            <Button variant="outline" size="sm" onClick={cancelDrawing}>
              <X className="mr-1.5 h-4 w-4" />
              Cancel
            </Button>
            <p className="text-xs text-muted-foreground self-center">
              Click to add points. Click near first point to close polygon.
              ({currentPoints.length} points placed)
            </p>
          </>
        ) : (
          <>
            <Button size="sm" onClick={startDrawing}>
              <Plus className="mr-1.5 h-4 w-4" />
              Add Zone
            </Button>
            {selectedZoneId && (
              <Button
                variant="destructive"
                size="sm"
                onClick={deleteSelectedZone}
              >
                <Trash2 className="mr-1.5 h-4 w-4" />
                Delete Zone
              </Button>
            )}
          </>
        )}
      </div>

      {/* Canvas */}
      <div ref={containerRef} className="relative overflow-hidden rounded-lg border">
        <canvas
          ref={canvasRef}
          className="w-full cursor-crosshair"
          onClick={handleCanvasClick}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
        />
        {!imageLoaded && (
          <div className="absolute inset-0 flex items-center justify-center bg-muted">
            <p className="text-sm text-muted-foreground">Loading image...</p>
          </div>
        )}
      </div>

      {/* Zone list */}
      {zones.length > 0 && (
        <div className="space-y-2">
          <Label className="text-xs font-medium">Zones ({zones.length})</Label>
          <div className="flex flex-wrap gap-2">
            {zones.map((zone) => (
              <button
                key={zone.id}
                onClick={() => setSelectedZoneId(zone.id === selectedZoneId ? null : zone.id)}
                className={cn(
                  "flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs transition-colors",
                  zone.id === selectedZoneId
                    ? "border-foreground bg-accent"
                    : "hover:bg-accent"
                )}
              >
                <div
                  className="h-3 w-3 rounded-sm"
                  style={{ backgroundColor: zone.color }}
                />
                {zone.name}
                <span className="text-muted-foreground">({zone.type})</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Save/Cancel */}
      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button onClick={() => onSave?.(zones)}>
          <Save className="mr-1.5 h-4 w-4" />
          Save Zones
        </Button>
      </div>
    </div>
  );
}
