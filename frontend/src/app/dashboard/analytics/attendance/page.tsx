'use client';

import { useState, useMemo, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { format } from 'date-fns';
import {
  Loader2,
  Download,
  Search,
  CalendarDays,
  UserCheck,
  UserX,
  Clock,
  UserMinus,
  FileSpreadsheet,
  FileText,
  RefreshCw,
  Users,
} from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { AttendanceTable, type AttendanceRecord } from '@/components/analytics/attendance-table';
import { apiClient } from '@/lib/api-client';
import { cn } from '@/lib/utils';

// -------------------------------------------------------------------
// Types
// -------------------------------------------------------------------

interface Department {
  id: string;
  name: string;
}

interface AttendanceSummary {
  total_employees: number;
  present: number;
  late: number;
  absent: number;
  half_day: number;
}

interface AttendanceResponse {
  summary: AttendanceSummary;
  records: AttendanceRecord[];
  departments: Department[];
}

// -------------------------------------------------------------------
// Page
// -------------------------------------------------------------------

export default function AttendanceManagementPage() {
  // Filters
  const [selectedDate, setSelectedDate] = useState(() => format(new Date(), 'yyyy-MM-dd'));
  const [selectedDepartment, setSelectedDepartment] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [exportLoading, setExportLoading] = useState<string | null>(null);

  // -------------------------------------------------------------------
  // Queries
  // -------------------------------------------------------------------

  const { data: departments } = useQuery<Department[]>({
    queryKey: ['departments'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/departments');
      return res.data?.results ?? res.data ?? [];
    },
  });

  const {
    data: attendanceData,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery<AttendanceResponse>({
    queryKey: ['attendance', selectedDate, selectedDepartment, searchQuery],
    queryFn: async () => {
      const params: Record<string, string> = { date: selectedDate };
      if (selectedDepartment !== 'all') params.department_id = selectedDepartment;
      if (searchQuery.trim()) params.search = searchQuery.trim();

      const res = await apiClient.get('/api/v1/analytics/attendance', { params });
      return res.data;
    },
  });

  // -------------------------------------------------------------------
  // Derived
  // -------------------------------------------------------------------

  const summary = attendanceData?.summary;
  const records = attendanceData?.records ?? [];
  const departmentList = attendanceData?.departments ?? departments ?? [];

  const filteredRecords = useMemo(() => {
    let result = records;
    if (searchQuery.trim()) {
      const q = searchQuery.trim().toLowerCase();
      result = result.filter(
        (r) =>
          r.name.toLowerCase().includes(q) ||
          r.department.toLowerCase().includes(q)
      );
    }
    return result;
  }, [records, searchQuery]);

  // -------------------------------------------------------------------
  // Export handler
  // -------------------------------------------------------------------

  const handleExport = useCallback(
    async (fmt: 'csv' | 'excel' | 'pdf') => {
      setExportLoading(fmt);
      try {
        const params: Record<string, string> = {
          date: selectedDate,
          format: fmt,
        };
        if (selectedDepartment !== 'all') params.department_id = selectedDepartment;
        if (searchQuery.trim()) params.search = searchQuery.trim();

        const res = await apiClient.get('/api/v1/analytics/attendance/export', {
          params,
          responseType: 'blob',
        });

        const extensions: Record<string, string> = { csv: 'csv', excel: 'xlsx', pdf: 'pdf' };
        const mimeTypes: Record<string, string> = {
          csv: 'text/csv',
          excel: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          pdf: 'application/pdf',
        };

        const blob = new Blob([res.data], { type: mimeTypes[fmt] });
        const url = window.URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', `attendance_${selectedDate}.${extensions[fmt]}`);
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
      } catch {
        // Export failed silently
      } finally {
        setExportLoading(null);
      }
    },
    [selectedDate, selectedDepartment, searchQuery]
  );

  // -------------------------------------------------------------------
  // Stat cards
  // -------------------------------------------------------------------

  const statCards = useMemo(
    () => [
      {
        title: 'Present',
        value: summary?.present ?? 0,
        total: summary?.total_employees ?? 0,
        icon: UserCheck,
        color: 'bg-green-50 text-green-600 dark:bg-green-900/30 dark:text-green-400',
        badgeColor: 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400',
      },
      {
        title: 'Late',
        value: summary?.late ?? 0,
        total: summary?.total_employees ?? 0,
        icon: Clock,
        color: 'bg-yellow-50 text-yellow-600 dark:bg-yellow-900/30 dark:text-yellow-400',
        badgeColor: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-400',
      },
      {
        title: 'Absent',
        value: summary?.absent ?? 0,
        total: summary?.total_employees ?? 0,
        icon: UserX,
        color: 'bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-400',
        badgeColor: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-400',
      },
      {
        title: 'Half Day',
        value: summary?.half_day ?? 0,
        total: summary?.total_employees ?? 0,
        icon: UserMinus,
        color: 'bg-blue-50 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400',
        badgeColor: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400',
      },
    ],
    [summary]
  );

  // -------------------------------------------------------------------
  // Loading
  // -------------------------------------------------------------------

  if (isLoading && !attendanceData) {
    return (
      <div className="flex h-96 items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
          <p className="text-sm text-slate-500">Loading attendance data...</p>
        </div>
      </div>
    );
  }

  // -------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">
            Attendance Management
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Track employee attendance via face recognition across all cameras
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => refetch()}>
            <RefreshCw className="mr-1.5 h-4 w-4" />
            Refresh
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => handleExport('csv')}
            disabled={exportLoading === 'csv'}
          >
            {exportLoading === 'csv' ? (
              <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
            ) : (
              <Download className="mr-1.5 h-4 w-4" />
            )}
            CSV
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => handleExport('excel')}
            disabled={exportLoading === 'excel'}
          >
            {exportLoading === 'excel' ? (
              <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
            ) : (
              <FileSpreadsheet className="mr-1.5 h-4 w-4" />
            )}
            Excel
          </Button>
          <Button
            size="sm"
            onClick={() => handleExport('pdf')}
            disabled={exportLoading === 'pdf'}
          >
            {exportLoading === 'pdf' ? (
              <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
            ) : (
              <FileText className="mr-1.5 h-4 w-4" />
            )}
            PDF
          </Button>
        </div>
      </div>

      {/* Filters */}
      <Card>
        <CardContent className="p-4">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {/* Date Picker */}
            <div className="space-y-1.5">
              <Label className="text-xs">Date</Label>
              <div className="relative">
                <CalendarDays className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  type="date"
                  value={selectedDate}
                  onChange={(e) => setSelectedDate(e.target.value)}
                  className="pl-9"
                />
              </div>
            </div>

            {/* Department Filter */}
            <div className="space-y-1.5">
              <Label className="text-xs">Department</Label>
              <Select value={selectedDepartment} onValueChange={setSelectedDepartment}>
                <SelectTrigger>
                  <SelectValue placeholder="All Departments" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Departments</SelectItem>
                  {departmentList.map((dept) => (
                    <SelectItem key={dept.id} value={dept.id}>
                      {dept.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Search by Name */}
            <div className="space-y-1.5">
              <Label className="text-xs">Search by Name</Label>
              <div className="relative">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  type="text"
                  placeholder="Search employees..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="pl-9"
                />
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Error */}
      {isError && (
        <Card className="border-red-200 bg-red-50 dark:border-red-800 dark:bg-red-950/30">
          <CardContent className="flex items-center gap-3 p-4">
            <Users className="h-5 w-5 text-red-500" />
            <div>
              <p className="text-sm font-medium text-red-700 dark:text-red-400">
                Failed to load attendance data
              </p>
              <p className="text-xs text-red-600 dark:text-red-500">
                {(error as { message?: string })?.message ?? 'An unexpected error occurred.'}
              </p>
            </div>
            <Button variant="outline" size="sm" className="ml-auto" onClick={() => refetch()}>
              Retry
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Summary Stats */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {statCards.map((stat) => {
          const Icon = stat.icon;
          const percentage =
            stat.total > 0 ? Math.round((stat.value / stat.total) * 100) : 0;

          return (
            <Card key={stat.title} className="transition-shadow hover:shadow-md">
              <CardContent className="p-6">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium text-slate-500 dark:text-slate-400">
                      {stat.title}
                    </p>
                    <div className="mt-1 flex items-baseline gap-2">
                      <p className="text-3xl font-bold text-slate-900 dark:text-white tabular-nums">
                        {stat.value}
                      </p>
                      <span className="text-sm text-slate-400 dark:text-slate-500">
                        / {stat.total}
                      </span>
                    </div>
                    <div className="mt-2">
                      <div className="h-1.5 w-full rounded-full bg-slate-100 dark:bg-slate-700">
                        <div
                          className={cn(
                            'h-1.5 rounded-full transition-all duration-500',
                            stat.title === 'Present'
                              ? 'bg-green-500'
                              : stat.title === 'Late'
                              ? 'bg-yellow-500'
                              : stat.title === 'Absent'
                              ? 'bg-red-500'
                              : 'bg-blue-500'
                          )}
                          style={{ width: `${percentage}%` }}
                        />
                      </div>
                      <p className="mt-1 text-xs text-slate-400 tabular-nums">
                        {percentage}%
                      </p>
                    </div>
                  </div>
                  <div className={cn('rounded-xl p-3', stat.color)}>
                    <Icon className="h-6 w-6" />
                  </div>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      {/* Attendance Table */}
      <AttendanceTable
        data={filteredRecords}
        title="Attendance Records"
        onExport={handleExport}
      />
    </div>
  );
}
