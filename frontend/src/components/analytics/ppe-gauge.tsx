"use client";

import React from "react";
import { cn } from "@/lib/utils";

interface PPEGaugeProps {
  percentage: number;
  label?: string;
  size?: number;
  strokeWidth?: number;
  className?: string;
}

export function PPEGauge({
  percentage,
  label = "Compliance",
  size = 160,
  strokeWidth = 12,
  className,
}: PPEGaugeProps) {
  const clampedPercentage = Math.min(100, Math.max(0, percentage));
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (clampedPercentage / 100) * circumference;
  const center = size / 2;

  // Color based on compliance thresholds
  let color: string;
  let bgColor: string;
  let textColor: string;

  if (clampedPercentage < 70) {
    color = "hsl(0, 84%, 60%)"; // red
    bgColor = "bg-red-50 dark:bg-red-950/20";
    textColor = "text-red-600 dark:text-red-400";
  } else if (clampedPercentage < 90) {
    color = "hsl(45, 93%, 47%)"; // yellow
    bgColor = "bg-yellow-50 dark:bg-yellow-950/20";
    textColor = "text-yellow-600 dark:text-yellow-400";
  } else {
    color = "hsl(142, 76%, 36%)"; // green
    bgColor = "bg-green-50 dark:bg-green-950/20";
    textColor = "text-green-600 dark:text-green-400";
  }

  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-lg p-4",
        bgColor,
        className
      )}
    >
      <div className="relative" style={{ width: size, height: size }}>
        <svg
          width={size}
          height={size}
          className="transform -rotate-90"
        >
          {/* Background circle */}
          <circle
            cx={center}
            cy={center}
            r={radius}
            fill="none"
            stroke="hsl(var(--muted))"
            strokeWidth={strokeWidth}
          />
          {/* Progress circle */}
          <circle
            cx={center}
            cy={center}
            r={radius}
            fill="none"
            stroke={color}
            strokeWidth={strokeWidth}
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            className="transition-all duration-700 ease-in-out"
          />
        </svg>

        {/* Center text */}
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className={cn("text-3xl font-bold tabular-nums", textColor)}>
            {Math.round(clampedPercentage)}
          </span>
          <span className="text-xs text-muted-foreground -mt-0.5">%</span>
        </div>
      </div>

      {/* Label */}
      <p className="mt-2 text-sm font-medium text-muted-foreground">{label}</p>
    </div>
  );
}
