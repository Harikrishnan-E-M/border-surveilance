"use client";

import React, { useMemo } from "react";
import {
  ResponsiveContainer,
  ComposedChart,
  Line,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  Legend,
} from "recharts";
import { format, parseISO } from "date-fns";
import { cn } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export interface FootfallDataPoint {
  timestamp: string;
  entries: number;
  exits: number;
  occupancy?: number;
}

interface FootfallChartProps {
  data: FootfallDataPoint[];
  title?: string;
  chartType?: "line" | "bar" | "composed";
  height?: number;
  className?: string;
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: Array<{
    name: string;
    value: number;
    color: string;
  }>;
  label?: string;
}

function CustomTooltip({ active, payload, label }: CustomTooltipProps) {
  if (!active || !payload || !label) return null;

  let formattedLabel = label;
  try {
    formattedLabel = format(parseISO(label), "MMM d, yyyy HH:mm");
  } catch {
    // Use raw label if parsing fails
  }

  return (
    <div className="rounded-lg border bg-background p-3 shadow-md">
      <p className="mb-1.5 text-xs font-medium text-muted-foreground">
        {formattedLabel}
      </p>
      {payload.map((entry, index) => (
        <div key={index} className="flex items-center gap-2 text-sm">
          <div
            className="h-2.5 w-2.5 rounded-full"
            style={{ backgroundColor: entry.color }}
          />
          <span className="text-muted-foreground">{entry.name}:</span>
          <span className="font-semibold">{entry.value.toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}

export function FootfallChart({
  data,
  title = "Footfall Analytics",
  chartType = "composed",
  height = 350,
  className,
}: FootfallChartProps) {
  const formattedData = useMemo(() => {
    return data.map((point) => {
      let displayLabel = point.timestamp;
      try {
        displayLabel = format(parseISO(point.timestamp), "HH:mm");
      } catch {
        // keep raw
      }
      return {
        ...point,
        displayLabel,
      };
    });
  }, [data]);

  const maxValue = useMemo(() => {
    return Math.max(
      ...data.map((d) => Math.max(d.entries, d.exits, d.occupancy ?? 0)),
      10
    );
  }, [data]);

  return (
    <Card className={cn(className)}>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <div className="flex items-center justify-center" style={{ height }}>
            <p className="text-sm text-muted-foreground">No footfall data available</p>
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={height}>
            <ComposedChart data={formattedData} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
              <XAxis
                dataKey="timestamp"
                tickFormatter={(value) => {
                  try {
                    return format(parseISO(value), "HH:mm");
                  } catch {
                    return value;
                  }
                }}
                className="text-xs"
                tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }}
              />
              <YAxis
                domain={[0, Math.ceil(maxValue * 1.1)]}
                className="text-xs"
                tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 11 }}
              />
              <RechartsTooltip content={<CustomTooltip />} />
              <Legend
                wrapperStyle={{ fontSize: 12, paddingTop: "8px" }}
              />

              {(chartType === "bar" || chartType === "composed") && (
                <>
                  <Bar
                    dataKey="entries"
                    name="Entries"
                    fill="hsl(142, 76%, 36%)"
                    radius={[2, 2, 0, 0]}
                    opacity={0.8}
                  />
                  <Bar
                    dataKey="exits"
                    name="Exits"
                    fill="hsl(0, 84%, 60%)"
                    radius={[2, 2, 0, 0]}
                    opacity={0.8}
                  />
                </>
              )}

              {(chartType === "line" || chartType === "composed") && data[0]?.occupancy !== undefined && (
                <Line
                  type="monotone"
                  dataKey="occupancy"
                  name="Occupancy"
                  stroke="hsl(221, 83%, 53%)"
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4 }}
                />
              )}

              {chartType === "line" && (
                <>
                  <Line
                    type="monotone"
                    dataKey="entries"
                    name="Entries"
                    stroke="hsl(142, 76%, 36%)"
                    strokeWidth={2}
                    dot={false}
                    activeDot={{ r: 4 }}
                  />
                  <Line
                    type="monotone"
                    dataKey="exits"
                    name="Exits"
                    stroke="hsl(0, 84%, 60%)"
                    strokeWidth={2}
                    dot={false}
                    activeDot={{ r: 4 }}
                  />
                </>
              )}
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
