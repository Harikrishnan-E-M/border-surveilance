"use client";

import React, { useEffect, useRef, useState, useCallback } from "react";
import Hls from "hls.js";
import {
  Maximize,
  Minimize,
  RefreshCw,
  Loader2,
  AlertTriangle,
  Volume2,
  VolumeX,
  Play,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

export interface BoundingBox {
  id: string;
  label: string;
  confidence: number;
  x: number;
  y: number;
  width: number;
  height: number;
  color?: string;
}

interface StreamPlayerProps {
  /** Stream URL – MJPEG endpoint, HLS .m3u8, or direct video URL */
  url: string;
  title?: string;
  status?: "online" | "offline" | "degraded";
  overlayData?: BoundingBox[];
  autoPlay?: boolean;
  muted?: boolean;
  className?: string;
  onError?: (error: string) => void;
}

type PlayerState = "loading" | "playing" | "error" | "idle";

/** Detect if the URL is an MJPEG stream endpoint */
function isMjpegUrl(url: string): boolean {
  return url.includes("/stream/mjpeg") || url.includes("mjpeg");
}

export function StreamPlayer({
  url,
  title,
  status = "online",
  overlayData = [],
  autoPlay = true,
  muted: initialMuted = true,
  className,
  onError,
}: StreamPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const hlsRef = useRef<Hls | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const [playerState, setPlayerState] = useState<PlayerState>("idle");
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [isMuted, setIsMuted] = useState(initialMuted);
  const [errorMessage, setErrorMessage] = useState("");

  const mjpeg = isMjpegUrl(url);

  const destroyHls = useCallback(() => {
    if (hlsRef.current) {
      hlsRef.current.destroy();
      hlsRef.current = null;
    }
  }, []);

  const initPlayer = useCallback(() => {
    if (!url) return;

    // MJPEG mode – handled by <img> tag, no JS setup needed
    if (mjpeg) {
      setPlayerState("loading");
      setErrorMessage("");
      return;
    }

    const video = videoRef.current;
    if (!video) return;

    destroyHls();
    setPlayerState("loading");
    setErrorMessage("");

    // HLS stream
    if (url.includes(".m3u8")) {
      if (Hls.isSupported()) {
        const hls = new Hls({
          enableWorker: true,
          lowLatencyMode: true,
          backBufferLength: 90,
        });
        hlsRef.current = hls;

        hls.loadSource(url);
        hls.attachMedia(video);

        hls.on(Hls.Events.MANIFEST_PARSED, () => {
          if (autoPlay) {
            video.play().catch(() => {});
          }
          setPlayerState("playing");
        });

        hls.on(Hls.Events.ERROR, (_event, data) => {
          if (data.fatal) {
            const msg = `Stream error: ${data.details}`;
            setErrorMessage(msg);
            setPlayerState("error");
            onError?.(msg);

            switch (data.type) {
              case Hls.ErrorTypes.NETWORK_ERROR:
                hls.startLoad();
                break;
              case Hls.ErrorTypes.MEDIA_ERROR:
                hls.recoverMediaError();
                break;
              default:
                destroyHls();
                break;
            }
          }
        });
      } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
        // Safari native HLS
        video.src = url;
        video.addEventListener("loadedmetadata", () => {
          if (autoPlay) video.play();
          setPlayerState("playing");
        });
      }
    } else {
      // Direct video URL (MP4, WebM, etc.)
      video.src = url;
      video.addEventListener("loadeddata", () => {
        if (autoPlay) video.play();
        setPlayerState("playing");
      });
      video.addEventListener("error", () => {
        const msg = "Failed to load video stream";
        setErrorMessage(msg);
        setPlayerState("error");
        onError?.(msg);
      });
    }
  }, [url, autoPlay, destroyHls, onError, mjpeg]);

  useEffect(() => {
    initPlayer();
    return () => destroyHls();
  }, [initPlayer, destroyHls]);

  // Draw bounding box overlay
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || overlayData.length === 0) return;

    const parent = canvas.parentElement;
    const ctx = canvas.getContext("2d");
    if (!ctx || !parent) return;

    const drawOverlay = () => {
      canvas.width = parent.clientWidth;
      canvas.height = parent.clientHeight;
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      overlayData.forEach((box) => {
        const color = box.color || "#00ff00";
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.strokeRect(box.x, box.y, box.width, box.height);

        const labelText = `${box.label} ${Math.round(box.confidence * 100)}%`;
        ctx.font = "12px sans-serif";
        const textMetrics = ctx.measureText(labelText);
        const textHeight = 16;

        ctx.fillStyle = color;
        ctx.fillRect(box.x, box.y - textHeight, textMetrics.width + 8, textHeight);

        ctx.fillStyle = "#000";
        ctx.fillText(labelText, box.x + 4, box.y - 4);
      });

      requestAnimationFrame(drawOverlay);
    };

    const animId = requestAnimationFrame(drawOverlay);
    return () => cancelAnimationFrame(animId);
  }, [overlayData]);

  const toggleFullscreen = async () => {
    const container = containerRef.current;
    if (!container) return;

    if (!document.fullscreenElement) {
      await container.requestFullscreen();
      setIsFullscreen(true);
    } else {
      await document.exitFullscreen();
      setIsFullscreen(false);
    }
  };

  const toggleMute = () => {
    const video = videoRef.current;
    if (video) {
      video.muted = !video.muted;
      setIsMuted(video.muted);
    }
  };

  const handleMjpegLoad = () => {
    setPlayerState("playing");
    setErrorMessage("");
  };

  const handleMjpegError = () => {
    const msg = "MJPEG stream disconnected";
    setErrorMessage(msg);
    setPlayerState("error");
    onError?.(msg);
  };

  const statusColors: Record<string, string> = {
    online: "bg-green-500",
    offline: "bg-red-500",
    degraded: "bg-yellow-500",
  };

  return (
    <div
      ref={containerRef}
      className={cn(
        "relative overflow-hidden rounded-lg bg-black",
        className
      )}
    >
      {/* MJPEG mode: uses <img> tag */}
      {mjpeg ? (
        <img
          ref={imgRef}
          src={url}
          alt={title || "Camera stream"}
          className="h-full w-full object-contain"
          onLoad={handleMjpegLoad}
          onError={handleMjpegError}
        />
      ) : (
        /* HLS / direct video mode: uses <video> tag */
        <video
          ref={videoRef}
          className="h-full w-full object-contain"
          muted={isMuted}
          playsInline
          crossOrigin="anonymous"
        />
      )}

      {/* Overlay canvas for bounding boxes */}
      {overlayData.length > 0 && (
        <canvas
          ref={canvasRef}
          className="absolute inset-0 h-full w-full pointer-events-none"
        />
      )}

      {/* Status indicator */}
      <div className="absolute left-3 top-3 flex items-center gap-2">
        <div className={cn("h-2.5 w-2.5 rounded-full animate-pulse-dot", statusColors[status])} />
        {title && (
          <span className="text-xs font-medium text-white drop-shadow-md">
            {title}
          </span>
        )}
      </div>

      {/* LIVE badge */}
      {playerState === "playing" && (
        <div className="absolute right-3 top-3">
          <span className="inline-flex items-center gap-1 rounded bg-red-600 px-1.5 py-0.5 text-[10px] font-bold text-white uppercase">
            <span className="h-1.5 w-1.5 rounded-full bg-white animate-pulse" />
            Live
          </span>
        </div>
      )}

      {/* Loading overlay */}
      {playerState === "loading" && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/50">
          <div className="flex flex-col items-center gap-2 text-white">
            <Loader2 className="h-8 w-8 animate-spin" />
            <span className="text-sm">Connecting to stream...</span>
          </div>
        </div>
      )}

      {/* Error overlay */}
      {playerState === "error" && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/70">
          <div className="flex flex-col items-center gap-3 text-white">
            <AlertTriangle className="h-10 w-10 text-yellow-400" />
            <p className="text-sm text-center max-w-xs">
              {errorMessage || "Stream unavailable"}
            </p>
            <Button
              variant="secondary"
              size="sm"
              onClick={initPlayer}
              className="gap-1.5"
            >
              <RefreshCw className="h-4 w-4" />
              Retry
            </Button>
          </div>
        </div>
      )}

      {/* Controls overlay */}
      <div className="absolute bottom-0 left-0 right-0 flex items-center justify-between bg-gradient-to-t from-black/60 to-transparent px-3 py-2 opacity-0 transition-opacity hover:opacity-100">
        <div className="flex items-center gap-1">
          {!mjpeg && (
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-white hover:bg-white/20"
              onClick={toggleMute}
            >
              {isMuted ? (
                <VolumeX className="h-4 w-4" />
              ) : (
                <Volume2 className="h-4 w-4" />
              )}
            </Button>
          )}
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8 text-white hover:bg-white/20"
            onClick={toggleFullscreen}
          >
            {isFullscreen ? (
              <Minimize className="h-4 w-4" />
            ) : (
              <Maximize className="h-4 w-4" />
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}
