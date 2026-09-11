"use client";

import React, { useState, useCallback } from "react";
import {
  startOfDay,
  endOfDay,
  subDays,
  startOfMonth,
  endOfMonth,
  format,
} from "date-fns";
import { Calendar, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export interface DateRange {
  start: Date;
  end: Date;
}

interface DateRangePickerProps {
  value?: DateRange;
  onChange?: (range: DateRange | undefined) => void;
  className?: string;
}

interface PresetButton {
  label: string;
  getRange: () => DateRange;
}

const presets: PresetButton[] = [
  {
    label: "Today",
    getRange: () => ({
      start: startOfDay(new Date()),
      end: endOfDay(new Date()),
    }),
  },
  {
    label: "Yesterday",
    getRange: () => ({
      start: startOfDay(subDays(new Date(), 1)),
      end: endOfDay(subDays(new Date(), 1)),
    }),
  },
  {
    label: "Last 7 days",
    getRange: () => ({
      start: startOfDay(subDays(new Date(), 7)),
      end: endOfDay(new Date()),
    }),
  },
  {
    label: "Last 30 days",
    getRange: () => ({
      start: startOfDay(subDays(new Date(), 30)),
      end: endOfDay(new Date()),
    }),
  },
  {
    label: "This month",
    getRange: () => ({
      start: startOfMonth(new Date()),
      end: endOfMonth(new Date()),
    }),
  },
];

export function DateRangePicker({
  value,
  onChange,
  className,
}: DateRangePickerProps) {
  const [startDate, setStartDate] = useState<string>(
    value ? format(value.start, "yyyy-MM-dd'T'HH:mm") : ""
  );
  const [endDate, setEndDate] = useState<string>(
    value ? format(value.end, "yyyy-MM-dd'T'HH:mm") : ""
  );

  const applyRange = useCallback(() => {
    if (startDate && endDate) {
      const start = new Date(startDate);
      const end = new Date(endDate);
      if (!isNaN(start.getTime()) && !isNaN(end.getTime()) && start <= end) {
        onChange?.({ start, end });
      }
    }
  }, [startDate, endDate, onChange]);

  const applyPreset = (preset: PresetButton) => {
    const range = preset.getRange();
    setStartDate(format(range.start, "yyyy-MM-dd'T'HH:mm"));
    setEndDate(format(range.end, "yyyy-MM-dd'T'HH:mm"));
    onChange?.(range);
  };

  const clearRange = () => {
    setStartDate("");
    setEndDate("");
    onChange?.(undefined);
  };

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      {/* Presets */}
      <div className="flex flex-wrap gap-1.5">
        {presets.map((preset) => (
          <Button
            key={preset.label}
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            onClick={() => applyPreset(preset)}
          >
            {preset.label}
          </Button>
        ))}
      </div>

      {/* Date inputs */}
      <div className="flex items-end gap-2">
        <div className="space-y-1 flex-1">
          <Label className="text-xs">Start Date</Label>
          <div className="relative">
            <Calendar className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              type="datetime-local"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="h-9 pl-8 text-xs"
            />
          </div>
        </div>
        <span className="pb-2 text-muted-foreground text-sm">to</span>
        <div className="space-y-1 flex-1">
          <Label className="text-xs">End Date</Label>
          <div className="relative">
            <Calendar className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              type="datetime-local"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              className="h-9 pl-8 text-xs"
            />
          </div>
        </div>
        <Button size="sm" className="h-9" onClick={applyRange}>
          Apply
        </Button>
        {value && (
          <Button
            variant="ghost"
            size="icon"
            className="h-9 w-9 shrink-0"
            onClick={clearRange}
          >
            <X className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  );
}
