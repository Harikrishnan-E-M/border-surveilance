"use client";

import React, { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Video,
  Bell,
  Camera,
  Users,
  Car,
  BarChart3,
  Film,
  FileText,
  Settings,
  ChevronLeft,
  ChevronDown,
  ChevronRight,
  Shield,
  Footprints,
  Flame,
  UserCheck,
  HardHat,
  TrendingUp,
  UserCog,
  BellRing,
  ClipboardList,
  Eye,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";

interface NavItem {
  label: string;
  href?: string;
  icon: React.ElementType;
  children?: NavItem[];
}

const navigation: NavItem[] = [
  { label: "Dashboard", href: "/dashboard", icon: LayoutDashboard },
  { label: "Live View", href: "/dashboard/live", icon: Video },
  { label: "Alerts", href: "/dashboard/alerts", icon: Bell },
  { label: "Cameras", href: "/dashboard/cameras", icon: Camera },
  { label: "Faces", href: "/dashboard/faces", icon: Users },
  { label: "Vehicles", href: "/dashboard/vehicles/logs", icon: Car },
  { label: "Recordings", href: "/dashboard/recordings", icon: Film },
  { label: "Reports", href: "/dashboard/reports", icon: FileText },
  {
    label: "Admin",
    icon: Settings,
    children: [
      { label: "Users", href: "/dashboard/admin/users", icon: UserCog },
      { label: "Settings", href: "/dashboard/admin/settings", icon: Settings },
      { label: "Notifications", href: "/dashboard/admin/notifications", icon: BellRing },
      { label: "Audit Log", href: "/dashboard/admin/audit", icon: ClipboardList },
    ],
  },
];

interface SidebarProps {
  user?: {
    name: string;
    email: string;
    role: string;
    avatarUrl?: string;
  };
  className?: string;
}

export function Sidebar({ user, className }: SidebarProps) {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({
    Analytics: true,
    Admin: false,
  });

  const toggleGroup = (label: string) => {
    setExpandedGroups((prev) => ({ ...prev, [label]: !prev[label] }));
  };

  const isActive = (href?: string) => {
    if (!href) return false;
    if (href === "/dashboard") return pathname === "/dashboard";
    return pathname.startsWith(href);
  };

  const isGroupActive = (item: NavItem) => {
    if (item.children) {
      return item.children.some((child) => isActive(child.href));
    }
    return false;
  };

  return (
    <TooltipProvider delayDuration={0}>
      <aside
        className={cn(
          "relative flex h-full flex-col border-r bg-card transition-all duration-300",
          collapsed ? "w-[68px]" : "w-[260px]",
          className
        )}
      >
        <div className="flex h-16 items-center border-b px-4">
          <Link href="/dashboard" className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary">
              <Shield className="h-5 w-5 text-primary-foreground" />
            </div>
            {!collapsed && (
              <div className="flex flex-col">
                <span className="text-base font-bold tracking-tight leading-tight">IBVAP</span>
                <span className="text-[10px] text-muted-foreground leading-tight font-medium">Border Analytics</span>
              </div>
            )}
          </Link>
        </div>

        {/* Navigation */}
        <ScrollArea className="flex-1 py-2">
          <nav className="flex flex-col gap-1 px-2">
            {navigation.map((item) => {
              const Icon = item.icon;

              // Item with children (submenu)
              if (item.children) {
                const groupActive = isGroupActive(item);
                const isExpanded = expandedGroups[item.label] ?? false;

                if (collapsed) {
                  return (
                    <Tooltip key={item.label}>
                      <TooltipTrigger asChild>
                        <button
                          className={cn(
                            "flex h-10 w-full items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground",
                            groupActive && "bg-accent text-accent-foreground"
                          )}
                        >
                          <Icon className="h-5 w-5" />
                        </button>
                      </TooltipTrigger>
                      <TooltipContent side="right" className="flex flex-col gap-1 p-2">
                        <span className="font-semibold text-xs mb-1">{item.label}</span>
                        {item.children.map((child) => (
                          <Link
                            key={child.href}
                            href={child.href!}
                            className={cn(
                              "flex items-center gap-2 rounded-sm px-2 py-1 text-xs transition-colors hover:bg-accent",
                              isActive(child.href) && "bg-accent font-medium"
                            )}
                          >
                            <child.icon className="h-3.5 w-3.5" />
                            {child.label}
                          </Link>
                        ))}
                      </TooltipContent>
                    </Tooltip>
                  );
                }

                return (
                  <div key={item.label}>
                    <button
                      onClick={() => toggleGroup(item.label)}
                      className={cn(
                        "flex h-10 w-full items-center gap-3 rounded-md px-3 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground",
                        groupActive && "text-foreground"
                      )}
                    >
                      <Icon className="h-5 w-5 shrink-0" />
                      <span className="flex-1 text-left">{item.label}</span>
                      {isExpanded ? (
                        <ChevronDown className="h-4 w-4" />
                      ) : (
                        <ChevronRight className="h-4 w-4" />
                      )}
                    </button>
                    {isExpanded && (
                      <div className="ml-4 mt-1 flex flex-col gap-0.5 border-l pl-3">
                        {item.children.map((child) => {
                          const ChildIcon = child.icon;
                          const active = isActive(child.href);
                          return (
                            <Link
                              key={child.href}
                              href={child.href!}
                              className={cn(
                                "flex h-9 items-center gap-2.5 rounded-md px-2.5 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground",
                                active &&
                                  "bg-accent text-accent-foreground font-medium"
                              )}
                            >
                              <ChildIcon className="h-4 w-4 shrink-0" />
                              {child.label}
                            </Link>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              }

              // Simple link item
              const active = isActive(item.href);

              if (collapsed) {
                return (
                  <Tooltip key={item.label}>
                    <TooltipTrigger asChild>
                      <Link
                        href={item.href!}
                        className={cn(
                          "flex h-10 w-full items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground",
                          active && "bg-accent text-accent-foreground"
                        )}
                      >
                        <Icon className="h-5 w-5" />
                      </Link>
                    </TooltipTrigger>
                    <TooltipContent side="right">{item.label}</TooltipContent>
                  </Tooltip>
                );
              }

              return (
                <Link
                  key={item.label}
                  href={item.href!}
                  className={cn(
                    "flex h-10 items-center gap-3 rounded-md px-3 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground",
                    active && "bg-accent text-accent-foreground font-medium"
                  )}
                >
                  <Icon className="h-5 w-5 shrink-0" />
                  {item.label}
                </Link>
              );
            })}
          </nav>
        </ScrollArea>

        <Separator />

        {/* User info */}
        {user && (
          <div className={cn("p-3", collapsed && "flex justify-center")}>
            {collapsed ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary text-primary-foreground text-xs font-bold">
                    {user.name
                      .split(" ")
                      .map((n) => n[0])
                      .join("")
                      .toUpperCase()
                      .slice(0, 2)}
                  </div>
                </TooltipTrigger>
                <TooltipContent side="right">
                  <div>
                    <p className="font-medium">{user.name}</p>
                    <p className="text-xs text-muted-foreground">{user.email}</p>
                  </div>
                </TooltipContent>
              </Tooltip>
            ) : (
              <div className="flex items-center gap-3">
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground text-xs font-bold">
                  {user.name
                    .split(" ")
                    .map((n) => n[0])
                    .join("")
                    .toUpperCase()
                    .slice(0, 2)}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{user.name}</p>
                  <p className="text-xs text-muted-foreground truncate">{user.email}</p>
                </div>
                <Badge variant="secondary" className="text-2xs shrink-0">
                  {user.role}
                </Badge>
              </div>
            )}
          </div>
        )}

        {/* Collapse toggle */}
        <div className="absolute -right-3 top-20 z-10">
          <Button
            variant="outline"
            size="icon"
            className="h-6 w-6 rounded-full border bg-background shadow-sm"
            onClick={() => setCollapsed(!collapsed)}
          >
            <ChevronLeft
              className={cn(
                "h-3.5 w-3.5 transition-transform",
                collapsed && "rotate-180"
              )}
            />
          </Button>
        </div>
      </aside>
    </TooltipProvider>
  );
}
