"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import {
  WebSocketManager,
  type ConnectionState,
  type WebSocketManagerOptions,
  type WSMessage,
} from "@/lib/websocket";

/**
 * React hook for managing a WebSocket connection to a specific channel.
 *
 * Features:
 * - Auto-connect on mount, auto-disconnect on unmount
 * - Exposes connection state, send, and subscribe functions
 * - Manages a single WebSocketManager instance per hook
 *
 * Usage:
 * ```tsx
 * const { state, send, subscribe, lastMessage } = useWebSocket("/alerts");
 *
 * useEffect(() => {
 *   const unsub = subscribe("new_alert", (data) => {
 *     console.log("New alert:", data);
 *   });
 *   return unsub;
 * }, [subscribe]);
 * ```
 *
 * @param channel - WebSocket channel path (e.g., "/alerts", "/cameras/1/stream")
 * @param options - Optional WebSocketManager configuration
 */
export function useWebSocket(
  channel: string,
  options?: WebSocketManagerOptions & {
    /** Whether to connect automatically (default: true) */
    autoConnect?: boolean;
    /** Whether the hook is enabled (default: true) */
    enabled?: boolean;
  }
) {
  const { autoConnect = true, enabled = true, ...wsOptions } = options || {};

  const managerRef = useRef<WebSocketManager | null>(null);
  const [state, setState] = useState<ConnectionState>("disconnected");
  const [lastMessage, setLastMessage] = useState<WSMessage | null>(null);

  // Create or get the manager
  const getManager = useCallback(() => {
    if (!managerRef.current) {
      managerRef.current = new WebSocketManager(wsOptions);
    }
    return managerRef.current;
  }, []); // wsOptions intentionally omitted - only use initial values

  // Connect to the channel
  const connect = useCallback(() => {
    const manager = getManager();
    manager.connect(channel);
  }, [channel, getManager]);

  // Disconnect
  const disconnect = useCallback(() => {
    managerRef.current?.disconnect();
  }, []);

  // Send a message
  const send = useCallback(<T = unknown>(event: string, data?: T) => {
    managerRef.current?.send(event, data);
  }, []);

  // Subscribe to a specific event
  const subscribe = useCallback(
    <T = unknown>(event: string, handler: (data: T) => void): (() => void) => {
      const manager = getManager();
      return manager.subscribe<T>(event, handler);
    },
    [getManager]
  );

  // Auto-connect on mount and track state changes
  useEffect(() => {
    if (!enabled) return;

    const manager = getManager();

    // Listen for state changes
    const unsubState = manager.onStateChange((newState) => {
      setState(newState);
    });

    // Listen for all messages to update lastMessage
    const unsubMessages = manager.onMessage((message) => {
      setLastMessage(message as WSMessage);
    });

    // Auto-connect if configured
    if (autoConnect) {
      manager.connect(channel);
    }

    return () => {
      unsubState();
      unsubMessages();
      manager.disconnect();
      managerRef.current = null;
    };
  }, [channel, enabled, autoConnect, getManager]);

  return {
    /** Current connection state. */
    state,
    /** Whether the connection is currently active. */
    isConnected: state === "connected",
    /** Whether the connection is being established. */
    isConnecting: state === "connecting",
    /** Last received WebSocket message. */
    lastMessage,
    /** Connect to the channel. */
    connect,
    /** Disconnect from the channel. */
    disconnect,
    /** Send a message through the connection. */
    send,
    /** Subscribe to a specific event type. Returns an unsubscribe function. */
    subscribe,
    /** Direct access to the WebSocket manager instance. */
    manager: managerRef.current,
  };
}

/**
 * Hook for subscribing to a specific WebSocket event with a callback.
 * Convenience wrapper around useWebSocket for single-event subscriptions.
 *
 * Usage:
 * ```tsx
 * const alerts = useWebSocketEvent<Alert>("/alerts", "new_alert", (alert) => {
 *   console.log("Got alert:", alert);
 * });
 * ```
 */
export function useWebSocketEvent<T = unknown>(
  channel: string,
  event: string,
  handler: (data: T) => void,
  options?: WebSocketManagerOptions
) {
  const ws = useWebSocket(channel, options);
  const handlerRef = useRef(handler);

  // Keep handler ref up to date without re-subscribing
  useEffect(() => {
    handlerRef.current = handler;
  }, [handler]);

  useEffect(() => {
    const unsub = ws.subscribe<T>(event, (data) => {
      handlerRef.current(data);
    });
    return unsub;
  }, [event, ws.subscribe]);

  return ws;
}
