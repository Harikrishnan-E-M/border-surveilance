"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronRight, Home } from "lucide-react";
import { cn } from "@/lib/utils";

interface BreadcrumbProps {
  className?: string;
}

const labelMap: Record<string, string> = {
  dashboard: "Dashboard",
  live: "Live View",
  alerts: "Alerts",
  cameras: "Cameras",
  faces: "Face Recognition",
  enroll: "Enroll",
  search: "Search",
  vehicles: "Vehicles",
  logs: "Logs",
  analytics: "Analytics",
  footfall: "Footfall",
  heatmap: "Heatmap",
  attendance: "Attendance",
  ppe: "PPE Compliance",
  patterns: "Patterns",
  recordings: "Recordings",
  reports: "Reports",
  admin: "Administration",
  users: "Users",
  settings: "Settings",
  notifications: "Notifications",
  audit: "Audit Log",
};

function formatSegment(segment: string): string {
  return labelMap[segment] || segment.charAt(0).toUpperCase() + segment.slice(1).replace(/-/g, " ");
}

export function Breadcrumb({ className }: BreadcrumbProps) {
  const pathname = usePathname();

  const segments = pathname
    .split("/")
    .filter(Boolean)
    .filter((seg) => seg !== "dashboard" || pathname === "/dashboard");

  const breadcrumbs = segments.map((segment, index) => {
    const href = "/" + segments.slice(0, index + 1).join("/");
    const label = formatSegment(segment);
    const isLast = index === segments.length - 1;

    return { href, label, isLast, segment };
  });

  return (
    <nav aria-label="Breadcrumb" className={cn("flex items-center", className)}>
      <ol className="flex items-center gap-1.5 text-sm text-muted-foreground">
        <li>
          <Link
            href="/dashboard"
            className="flex items-center hover:text-foreground transition-colors"
          >
            <Home className="h-4 w-4" />
          </Link>
        </li>
        {breadcrumbs.map(({ href, label, isLast }) => (
          <li key={href} className="flex items-center gap-1.5">
            <ChevronRight className="h-3.5 w-3.5 text-muted-foreground/50" />
            {isLast ? (
              <span className="font-medium text-foreground">{label}</span>
            ) : (
              <Link
                href={href}
                className="hover:text-foreground transition-colors"
              >
                {label}
              </Link>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}
