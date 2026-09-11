"use client";

import React, { useState, useMemo } from "react";
import { ArrowUpDown, ArrowUp, ArrowDown, Download } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatDate, formatDuration } from "@/lib/utils";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export type AttendanceStatus = "present" | "late" | "absent" | "half_day";

export interface AttendanceRecord {
  id: string;
  name: string;
  department: string;
  date: string;
  firstSeen: string | null;
  lastSeen: string | null;
  durationSeconds: number;
  status: AttendanceStatus;
}

interface AttendanceTableProps {
  data: AttendanceRecord[];
  title?: string;
  onExport?: (format: "csv" | "excel" | "pdf") => void;
  className?: string;
}

const statusConfig: Record<
  AttendanceStatus,
  { label: string; variant: "default" | "secondary" | "destructive" | "outline" | "low" | "medium" | "critical" }
> = {
  present: { label: "Present", variant: "default" },
  late: { label: "Late", variant: "medium" },
  absent: { label: "Absent", variant: "critical" },
  half_day: { label: "Half Day", variant: "low" },
};

type SortField = "name" | "department" | "date" | "firstSeen" | "lastSeen" | "durationSeconds" | "status";
type SortDirection = "asc" | "desc";

export function AttendanceTable({
  data,
  title = "Attendance Records",
  onExport,
  className,
}: AttendanceTableProps) {
  const [sortField, setSortField] = useState<SortField>("name");
  const [sortDir, setSortDir] = useState<SortDirection>("asc");

  const toggleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir((prev) => (prev === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDir("asc");
    }
  };

  const sortedData = useMemo(() => {
    return [...data].sort((a, b) => {
      const aVal = a[sortField];
      const bVal = b[sortField];

      if (aVal === null && bVal === null) return 0;
      if (aVal === null) return 1;
      if (bVal === null) return -1;

      let comparison = 0;
      if (typeof aVal === "number" && typeof bVal === "number") {
        comparison = aVal - bVal;
      } else {
        comparison = String(aVal).localeCompare(String(bVal));
      }

      return sortDir === "asc" ? comparison : -comparison;
    });
  }, [data, sortField, sortDir]);

  const SortIcon = ({ field }: { field: SortField }) => {
    if (sortField !== field) return <ArrowUpDown className="ml-1 h-3.5 w-3.5" />;
    return sortDir === "asc" ? (
      <ArrowUp className="ml-1 h-3.5 w-3.5" />
    ) : (
      <ArrowDown className="ml-1 h-3.5 w-3.5" />
    );
  };

  return (
    <Card className={cn(className)}>
      <CardHeader className="flex flex-row items-center justify-between pb-3">
        <CardTitle className="text-base">{title}</CardTitle>
        {onExport && (
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5"
            onClick={() => onExport("csv")}
          >
            <Download className="h-4 w-4" />
            Export
          </Button>
        )}
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <div className="flex items-center justify-center py-12">
            <p className="text-sm text-muted-foreground">
              No attendance records available
            </p>
          </div>
        ) : (
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>
                    <button
                      className="flex items-center text-xs font-medium"
                      onClick={() => toggleSort("name")}
                    >
                      Name
                      <SortIcon field="name" />
                    </button>
                  </TableHead>
                  <TableHead>
                    <button
                      className="flex items-center text-xs font-medium"
                      onClick={() => toggleSort("department")}
                    >
                      Department
                      <SortIcon field="department" />
                    </button>
                  </TableHead>
                  <TableHead>
                    <button
                      className="flex items-center text-xs font-medium"
                      onClick={() => toggleSort("date")}
                    >
                      Date
                      <SortIcon field="date" />
                    </button>
                  </TableHead>
                  <TableHead>
                    <button
                      className="flex items-center text-xs font-medium"
                      onClick={() => toggleSort("firstSeen")}
                    >
                      First Seen
                      <SortIcon field="firstSeen" />
                    </button>
                  </TableHead>
                  <TableHead>
                    <button
                      className="flex items-center text-xs font-medium"
                      onClick={() => toggleSort("lastSeen")}
                    >
                      Last Seen
                      <SortIcon field="lastSeen" />
                    </button>
                  </TableHead>
                  <TableHead>
                    <button
                      className="flex items-center text-xs font-medium"
                      onClick={() => toggleSort("durationSeconds")}
                    >
                      Duration
                      <SortIcon field="durationSeconds" />
                    </button>
                  </TableHead>
                  <TableHead>
                    <button
                      className="flex items-center text-xs font-medium"
                      onClick={() => toggleSort("status")}
                    >
                      Status
                      <SortIcon field="status" />
                    </button>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sortedData.map((record) => {
                  const statusInfo = statusConfig[record.status];
                  return (
                    <TableRow key={record.id}>
                      <TableCell className="font-medium">
                        {record.name}
                      </TableCell>
                      <TableCell>{record.department}</TableCell>
                      <TableCell>
                        {formatDate(record.date, "MMM d, yyyy")}
                      </TableCell>
                      <TableCell>
                        {record.firstSeen
                          ? formatDate(record.firstSeen, "HH:mm:ss")
                          : "-"}
                      </TableCell>
                      <TableCell>
                        {record.lastSeen
                          ? formatDate(record.lastSeen, "HH:mm:ss")
                          : "-"}
                      </TableCell>
                      <TableCell>
                        {record.durationSeconds > 0
                          ? formatDuration(record.durationSeconds)
                          : "-"}
                      </TableCell>
                      <TableCell>
                        <Badge variant={statusInfo.variant as any}>
                          {statusInfo.label}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
