import { getAccessToken } from "./auth";

/**
 * Subscription callback type.
 */
type MessageHandler<T = unknown> = (data: T) => void;

/**
 * Connection state.
 */
export type ConnectionState = "connecting" | "connected" | "disconnecting" | "disconnected";

/**
 * WebSocket event types emitted by the server.
 */
export interface WSMessage<T = unknown> {
  event: string;
  data: T;
  timestamp?: string;
}

/**
 * Options for creating a WebSocketManager.
 */
export interface WebSocketManagerOptions {
  /** Base WebSocket URL (default: from env) */
  url?: string;
  /** Auto-reconnect on disconnect (default: true) */
  autoReconnect?: boolean;
  /** Maximum number of reconnection attempts (default: 10) */
  maxReconnectAttempts?: number;
  /** Base delay for exponential backoff in ms (default: 1000) */
  baseReconnectDelay?: number;
  /** Maximum reconnection delay in ms (default: 30000) */
  maxReconnectDelay?: number;
  /** Heartbeat/ping interval in ms (default: 30000) */
  heartbeatInterval?: number;
}

/**
 * WebSocket manager with auto-reconnect, event subscriptions, and heartbeat.
 *
 * Usage:
 * ```ts
 * const ws = new WebSocketManager({ url: "ws://localhost:8000/ws" });
 * ws.connect("/alerts");
 * ws.subscribe("new_alert", (data) => console.log(data));
 * ws.disconnect();
 * ```
 */
export class WebSocketManager {
  private ws: WebSocket | null = null;
  private url: string;
  private channel: string = "";
  private options: Required<WebSocketManagerOptions>;
  private subscribers: Map<string, Set<MessageHandler>> = new Map();
  private stateListeners: Set<(state: ConnectionState) => void> = new Set();
  private reconnectAttempts: number = 0;
  private reconnectTimeoutId: ReturnType<typeof setTimeout> | null = null;
  private heartbeatIntervalId: ReturnType<typeof setInterval> | null = null;
  private _state: ConnectionState = "disconnected";
  private manualDisconnect: boolean = false;

  constructor(options: WebSocketManagerOptions = {}) {
    let baseUrl = options.url || process.env.NEXT_PUBLIC_WS_URL;
    if (!baseUrl) {
      if (typeof window !== "undefined") {
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const hostname = window.location.hostname;
        baseUrl = `${protocol}//${hostname}:8000/api/v1/ws`;
      } else {
        baseUrl = "ws://localhost:8000/api/v1/ws";
      }
    }

    this.url = baseUrl;
    this.options = {
      url: baseUrl,
      autoReconnect: options.autoReconnect ?? true,
      maxReconnectAttempts: options.maxReconnectAttempts ?? 10,
      baseReconnectDelay: options.baseReconnectDelay ?? 1000,
      maxReconnectDelay: options.maxReconnectDelay ?? 30000,
      heartbeatInterval: options.heartbeatInterval ?? 30000,
    };
  }

  // ─── Connection State ───────────────────────────────────────────────────

  get state(): ConnectionState {
    return this._state;
  }

  private setState(state: ConnectionState): void {
    this._state = state;
    this.stateListeners.forEach((listener) => listener(state));
  }

  /**
   * Listen for connection state changes.
   */
  onStateChange(listener: (state: ConnectionState) => void): () => void {
    this.stateListeners.add(listener);
    return () => {
      this.stateListeners.delete(listener);
    };
  }

  // ─── Connection ─────────────────────────────────────────────────────────

  /**
   * Connect to a WebSocket channel.
   *
   * @param channel - Channel path (e.g., "/alerts", "/cameras/1/stream")
   */
  connect(channel: string = ""): void {
    // Prevent duplicate connections
    if (this.ws && (this._state === "connecting" || this._state === "connected")) {
      if (this.channel === channel) return;
      this.disconnect();
    }

    this.channel = channel;
    this.manualDisconnect = false;
    this.setState("connecting");

    // Build the URL with channel and authentication token
    const token = getAccessToken();
    const separator = this.url.includes("?") ? "&" : "?";
    const authParam = token ? `${separator}token=${encodeURIComponent(token)}` : "";
    const fullUrl = `${this.url}${channel}${authParam}`;

    try {
      this.ws = new WebSocket(fullUrl);
      this.setupEventHandlers();
    } catch (error) {
      console.error("[WebSocketManager] Connection error:", error);
      this.setState("disconnected");
      this.scheduleReconnect();
    }
  }

  /**
   * Disconnect from the WebSocket.
   */
  disconnect(): void {
    this.manualDisconnect = true;
    this.setState("disconnecting");
    this.clearHeartbeat();
    this.clearReconnectTimeout();

    if (this.ws) {
      this.ws.onopen = null;
      this.ws.onclose = null;
      this.ws.onmessage = null;
      this.ws.onerror = null;

      if (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING) {
        this.ws.close(1000, "Client disconnect");
      }
      this.ws = null;
    }

    this.setState("disconnected");
  }

  // ─── Messaging ──────────────────────────────────────────────────────────

  /**
   * Send a message through the WebSocket connection.
   *
   * @param event - Event name
   * @param data - Event payload
   */
  send<T = unknown>(event: string, data?: T): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.warn("[WebSocketManager] Cannot send - connection not open");
      return;
    }

    const message: WSMessage<T | undefined> = {
      event,
      data,
      timestamp: new Date().toISOString(),
    };

    this.ws.send(JSON.stringify(message));
  }

  /**
   * Subscribe to a specific event type.
   *
   * @param event - Event name to subscribe to (or "*" for all events)
   * @param handler - Callback invoked when the event is received
   * @returns Unsubscribe function
   */
  subscribe<T = unknown>(event: string, handler: MessageHandler<T>): () => void {
    if (!this.subscribers.has(event)) {
      this.subscribers.set(event, new Set());
    }
    this.subscribers.get(event)!.add(handler as MessageHandler);

    return () => {
      const handlers = this.subscribers.get(event);
      if (handlers) {
        handlers.delete(handler as MessageHandler);
        if (handlers.size === 0) {
          this.subscribers.delete(event);
        }
      }
    };
  }

  /**
   * Subscribe to all incoming messages regardless of event type.
   *
   * @param handler - Callback invoked on any message
   * @returns Unsubscribe function
   */
  onMessage<T = unknown>(handler: MessageHandler<WSMessage<T>>): () => void {
    return this.subscribe("*", handler as MessageHandler);
  }

  // ─── Internal Handlers ──────────────────────────────────────────────────

  private setupEventHandlers(): void {
    if (!this.ws) return;

    this.ws.onopen = () => {
      console.info(`[WebSocketManager] Connected to ${this.channel || "/"}`);
      this.setState("connected");
      this.reconnectAttempts = 0;
      this.startHeartbeat();
    };

    this.ws.onclose = (event: CloseEvent) => {
      console.info(
        `[WebSocketManager] Disconnected (code: ${event.code}, reason: ${event.reason})`
      );
      this.setState("disconnected");
      this.clearHeartbeat();

      if (!this.manualDisconnect && this.options.autoReconnect) {
        this.scheduleReconnect();
      }
    };

    this.ws.onerror = (event: Event) => {
      console.error("[WebSocketManager] Error:", event);
    };

    this.ws.onmessage = (event: MessageEvent) => {
      try {
        const message: WSMessage = JSON.parse(event.data);
        this.dispatchMessage(message);
      } catch {
        // Handle non-JSON messages (e.g., pong)
        console.debug("[WebSocketManager] Non-JSON message:", event.data);
      }
    };
  }

  private dispatchMessage(message: WSMessage): void {
    // Dispatch to specific event subscribers
    const handlers = this.subscribers.get(message.event);
    if (handlers) {
      handlers.forEach((handler) => handler(message.data));
    }

    // Dispatch to wildcard subscribers
    const wildcardHandlers = this.subscribers.get("*");
    if (wildcardHandlers) {
      wildcardHandlers.forEach((handler) => handler(message));
    }
  }

  // ─── Heartbeat ──────────────────────────────────────────────────────────

  private startHeartbeat(): void {
    this.clearHeartbeat();
    this.heartbeatIntervalId = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.send("ping");
      }
    }, this.options.heartbeatInterval);
  }

  private clearHeartbeat(): void {
    if (this.heartbeatIntervalId) {
      clearInterval(this.heartbeatIntervalId);
      this.heartbeatIntervalId = null;
    }
  }

  // ─── Reconnection ──────────────────────────────────────────────────────

  private scheduleReconnect(): void {
    if (this.reconnectAttempts >= this.options.maxReconnectAttempts) {
      console.error(
        `[WebSocketManager] Max reconnect attempts (${this.options.maxReconnectAttempts}) reached`
      );
      return;
    }

    // Exponential backoff with jitter
    const delay = Math.min(
      this.options.baseReconnectDelay * Math.pow(2, this.reconnectAttempts) +
        Math.random() * 1000,
      this.options.maxReconnectDelay
    );

    console.info(
      `[WebSocketManager] Reconnecting in ${Math.round(delay)}ms (attempt ${this.reconnectAttempts + 1}/${this.options.maxReconnectAttempts})`
    );

    this.reconnectTimeoutId = setTimeout(() => {
      this.reconnectAttempts++;
      this.connect(this.channel);
    }, delay);
  }

  private clearReconnectTimeout(): void {
    if (this.reconnectTimeoutId) {
      clearTimeout(this.reconnectTimeoutId);
      this.reconnectTimeoutId = null;
    }
  }

  // ─── Cleanup ────────────────────────────────────────────────────────────

  /**
   * Destroy the manager, cleaning up all resources and subscriptions.
   */
  destroy(): void {
    this.disconnect();
    this.subscribers.clear();
    this.stateListeners.clear();
  }
}

/**
 * Singleton instance for shared global WebSocket connection.
 * Use this for the main application WebSocket (e.g., alerts, notifications).
 */
let globalWsManager: WebSocketManager | null = null;

export function getGlobalWebSocket(): WebSocketManager {
  if (!globalWsManager) {
    globalWsManager = new WebSocketManager();
  }
  return globalWsManager;
}

export function destroyGlobalWebSocket(): void {
  if (globalWsManager) {
    globalWsManager.destroy();
    globalWsManager = null;
  }
}
