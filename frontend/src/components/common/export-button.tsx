"use client";

import React, { useState } from "react";
import { Download, FileSpreadsheet, FileText, File, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export type ExportFormat = "csv" | "excel" | "pdf";

interface ExportButtonProps {
  onExport: (format: ExportFormat) => Promise<void> | void;
  formats?: ExportFormat[];
  disabled?: boolean;
  className?: string;
}

const formatConfig: Record<
  ExportFormat,
  { label: string; icon: React.ElementType }
> = {
  csv: { label: "Export as CSV", icon: FileText },
  excel: { label: "Export as Excel", icon: FileSpreadsheet },
  pdf: { label: "Export as PDF", icon: File },
};

export function ExportButton({
  onExport,
  formats = ["csv", "excel", "pdf"],
  disabled = false,
  className,
}: ExportButtonProps) {
  const [loadingFormat, setLoadingFormat] = useState<ExportFormat | null>(null);

  const handleExport = async (format: ExportFormat) => {
    try {
      setLoadingFormat(format);
      await onExport(format);
    } catch (error) {
      console.error(`Export failed for format ${format}:`, error);
    } finally {
      setLoadingFormat(null);
    }
  };

  const isLoading = loadingFormat !== null;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className={cn("gap-1.5", className)}
          disabled={disabled || isLoading}
        >
          {isLoading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Download className="h-4 w-4" />
          )}
          {isLoading ? "Exporting..." : "Export"}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {formats.map((format) => {
          const config = formatConfig[format];
          const Icon = config.icon;
          const isFormatLoading = loadingFormat === format;

          return (
            <DropdownMenuItem
              key={format}
              onClick={() => handleExport(format)}
              disabled={isLoading}
            >
              {isFormatLoading ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Icon className="mr-2 h-4 w-4" />
              )}
              {config.label}
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
