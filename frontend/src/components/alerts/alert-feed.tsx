"use client";

import React, { useEffect, useRef, useState, useCallback } from "react";
import { Bell, Volume2, VolumeX } from "lucide-react";
import { cn } from "@/lib/utils";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { AlertCard, type AlertData } from "@/components/alerts/alert-card";

interface AlertFeedProps {
  alerts?: AlertData[];
  websocketUrl?: string;
  maxAlerts?: number;
  onAlertClick?: (alert: AlertData) => void;
  onAcknowledge?: (alert: AlertData) => void;
  onResolve?: (alert: AlertData) => void;
  onFalsePositive?: (alert: AlertData) => void;
  className?: string;
}

export function AlertFeed({
  alerts: initialAlerts = [],
  websocketUrl,
  maxAlerts = 100,
  onAlertClick,
  onAcknowledge,
  onResolve,
  onFalsePositive,
  className,
}: AlertFeedProps) {
  const [alerts, setAlerts] = useState<AlertData[]>(initialAlerts);
  const [soundEnabled, setSoundEnabled] = useState(true);
  const [isConnected, setIsConnected] = useState(false);
  const [newAlertCount, setNewAlertCount] = useState(0);
  const wsRef = useRef<WebSocket | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Initialize audio for critical alerts
  useEffect(() => {
    audioRef.current = new Audio("/sounds/alert.mp3");
    audioRef.current.volume = 0.5;
    return () => {
      audioRef.current = null;
    };
  }, []);

  const addAlert = useCallback(
    (alert: AlertData) => {
      setAlerts((prev) => {
        const updated = [alert, ...prev].slice(0, maxAlerts);
        return updated;
      });
      setNewAlertCount((prev) => prev + 1);

      // Play sound for critical alerts
      if (alert.severity === "critical" && soundEnabled && audioRef.current) {
        audioRef.current.play().catch(() => {
          // Audio play failed (autoplay policy)
        });
      }
    },
    [maxAlerts, soundEnabled]
  );

  // WebSocket connection
  useEffect(() => {
    if (!websocketUrl) return;

    const connect = () => {
      const ws = new WebSocket(websocketUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setIsConnected(true);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === "alert" && data.payload) {
            addAlert(data.payload as AlertData);
          }
        } catch {
          // Invalid message format
        }
      };

      ws.onclose = () => {
        setIsConnected(false);
        // Reconnect after 3 seconds
        setTimeout(connect, 3000);
      };

      ws.onerror = () => {
        setIsConnected(false);
        ws.close();
      };
    };

    connect();

    return () => {
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [websocketUrl, addAlert]);

  // Update from prop changes
  useEffect(() => {
    if (initialAlerts.length > 0) {
      setAlerts(initialAlerts);
    }
  }, [initialAlerts]);

  const criticalCount = alerts.filter((a) => a.severity === "critical" && a.status === "new").length;

  return (
    <div className={cn("flex flex-col rounded-lg border bg-card", className)}>
      {/* Header */}
      <div className="flex items-center justify-between border-b px-4 py-3">
        <div className="flex items-center gap-2">
          <Bell className="h-4 w-4" />
          <h3 className="text-sm font-semibold">Alert Feed</h3>
          {alerts.length > 0 && (
            <Badge variant="secondary" className="text-2xs">
              {alerts.length}
            </Badge>
          )}
          {criticalCount > 0 && (
            <Badge variant="critical" className="text-2xs animate-alert-pulse">
              {criticalCount} critical
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          {websocketUrl && (
            <div
              className={cn(
                "h-2 w-2 rounded-full",
                isConnected ? "bg-green-500" : "bg-red-500"
              )}
              title={isConnected ? "Connected" : "Disconnected"}
            />
          )}
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={() => setSoundEnabled(!soundEnabled)}
            title={soundEnabled ? "Mute alerts" : "Unmute alerts"}
          >
            {soundEnabled ? (
              <Volume2 className="h-4 w-4" />
            ) : (
              <VolumeX className="h-4 w-4" />
            )}
          </Button>
        </div>
      </div>

      {/* Alert list */}
      <ScrollArea className="flex-1" ref={scrollRef}>
        <div className="flex flex-col gap-2 p-3">
          {alerts.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-center">
              <Bell className="h-10 w-10 text-muted-foreground/30" />
              <p className="mt-2 text-sm text-muted-foreground">
                No alerts yet
              </p>
              <p className="text-xs text-muted-foreground">
                Alerts will appear here in real-time
              </p>
            </div>
          ) : (
            alerts.map((alert) => (
              <AlertCard
                key={alert.id}
                alert={alert}
                compact
                onClick={onAlertClick}
                onAcknowledge={onAcknowledge}
                onResolve={onResolve}
                onFalsePositive={onFalsePositive}
              />
            ))
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
