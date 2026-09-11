"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import Hls from "hls.js";
import type { StreamStats } from "@/types/camera";

/**
 * Camera stream hook return type.
 */
export interface UseCameraStreamReturn {
  /** The stream URL for the video element. */
  streamUrl: string | null;
  /** Whether the stream is currently loading. */
  isLoading: boolean;
  /** Whether the stream is actively playing. */
  isPlaying: boolean;
  /** Whether an error occurred. */
  isError: boolean;
  /** Error message, if any. */
  error: string | null;
  /** Stream statistics (FPS, bitrate, etc.). */
  stats: StreamStats | null;
  /** Ref to attach to a <video> element. */
  videoRef: React.RefObject<HTMLVideoElement | null>;
  /** Start or resume the stream. */
  play: () => void;
  /** Pause the stream. */
  pause: () => void;
  /** Stop and clean up the stream. */
  stop: () => void;
  /** Retry the connection on error. */
  retry: () => void;
  /** Take a snapshot of the current frame. Returns a data URL. */
  takeSnapshot: () => string | null;
}

/**
 * Configuration options for the camera stream hook.
 */
export interface UseCameraStreamOptions {
  /** Whether to start playing automatically (default: true). */
  autoPlay?: boolean;
  /** Whether to mute the stream (default: true). */
  muted?: boolean;
  /** HLS.js configuration overrides. */
  hlsConfig?: Partial<{
    maxBufferLength: number;
    maxMaxBufferLength: number;
    liveSyncDurationCount: number;
    liveMaxLatencyDurationCount: number;
    lowLatencyMode: boolean;
    backBufferLength: number;
  }>;
  /** Whether the hook is enabled (default: true). */
  enabled?: boolean;
}

const API_URL = "/api/v1";

/**
 * React hook for managing an HLS camera stream.
 *
 * Handles HLS.js initialization, auto-recovery from errors, and stream statistics.
 * Supports both HLS-capable browsers (via native playback) and others (via hls.js).
 *
 * Usage:
 * ```tsx
 * function CameraView({ cameraId }: { cameraId: string }) {
 *   const { videoRef, isLoading, isError, error, retry } = useCameraStream(cameraId);
 *
 *   return (
 *     <div className="video-container">
 *       {isLoading && <LoadingSpinner />}
 *       {isError && <ErrorOverlay message={error} onRetry={retry} />}
 *       <video ref={videoRef} />
 *     </div>
 *   );
 * }
 * ```
 *
 * @param cameraId - The camera ID to stream from
 * @param options - Stream configuration options
 */
export function useCameraStream(
  cameraId: string | null | undefined,
  options: UseCameraStreamOptions = {}
): UseCameraStreamReturn {
  const {
    autoPlay = true,
    muted = true,
    hlsConfig = {},
    enabled = true,
  } = options;

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const hlsRef = useRef<Hls | null>(null);
  const statsIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const [streamUrl, setStreamUrl] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isError, setIsError] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState<StreamStats | null>(null);

  /**
   * Build the HLS stream URL for a camera.
   */
  const getStreamUrl = useCallback(
    (camId: string): string => {
      return `${API_URL}/cameras/${camId}/stream/hls/index.m3u8`;
    },
    []
  );

  /**
   * Collect stream statistics from the HLS instance and video element.
   */
  const collectStats = useCallback(() => {
    const video = videoRef.current;
    const hls = hlsRef.current;
    if (!video || !hls) return;

    const level = hls.levels?.[hls.currentLevel];
    const buffered = video.buffered;
    const bufferedSeconds =
      buffered.length > 0 ? buffered.end(buffered.length - 1) - video.currentTime : 0;

    setStats({
      fps: level?.attrs?.["FRAME-RATE"]
        ? parseFloat(level.attrs["FRAME-RATE"])
        : 0,
      bitrate_kbps: level?.bitrate ? Math.round(level.bitrate / 1000) : 0,
      resolution: level
        ? `${level.width}x${level.height}`
        : "unknown",
      latency_ms: 0, // HLS inherent latency not easily measured
      buffered_seconds: Math.round(bufferedSeconds * 10) / 10,
      dropped_frames: (video as HTMLVideoElement & { getVideoPlaybackQuality?: () => { droppedVideoFrames: number } })
        .getVideoPlaybackQuality?.()?.droppedVideoFrames ?? 0,
    });
  }, []);

  /**
   * Initialize and start the HLS stream.
   */
  const initStream = useCallback(() => {
    if (!cameraId || !enabled) return;

    const url = getStreamUrl(cameraId);
    setStreamUrl(url);
    setIsLoading(true);
    setIsError(false);
    setError(null);

    const video = videoRef.current;
    if (!video) {
      setIsLoading(false);
      setIsError(true);
      setError("Video element not available");
      return;
    }

    // Clean up any existing HLS instance
    if (hlsRef.current) {
      hlsRef.current.destroy();
      hlsRef.current = null;
    }

    if (Hls.isSupported()) {
      const hls = new Hls({
        maxBufferLength: hlsConfig.maxBufferLength ?? 10,
        maxMaxBufferLength: hlsConfig.maxMaxBufferLength ?? 30,
        liveSyncDurationCount: hlsConfig.liveSyncDurationCount ?? 3,
        liveMaxLatencyDurationCount: hlsConfig.liveMaxLatencyDurationCount ?? 6,
        lowLatencyMode: hlsConfig.lowLatencyMode ?? true,
        backBufferLength: hlsConfig.backBufferLength ?? 0,
        enableWorker: true,
        startFragPrefetch: true,
      });

      hls.loadSource(url);
      hls.attachMedia(video);

      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        setIsLoading(false);
        if (autoPlay) {
          video.muted = muted;
          video.play().catch(() => {
            // Autoplay may be blocked by browser
          });
        }
      });

      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (data.fatal) {
          switch (data.type) {
            case Hls.ErrorTypes.NETWORK_ERROR:
              console.error("[CameraStream] Network error, attempting recovery...");
              hls.startLoad();
              break;
            case Hls.ErrorTypes.MEDIA_ERROR:
              console.error("[CameraStream] Media error, attempting recovery...");
              hls.recoverMediaError();
              break;
            default:
              console.error("[CameraStream] Fatal error:", data);
              setIsError(true);
              setError(`Stream error: ${data.details}`);
              setIsLoading(false);
              hls.destroy();
              break;
          }
        }
      });

      hlsRef.current = hls;

      // Start stats collection
      statsIntervalRef.current = setInterval(collectStats, 2000);
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      // Native HLS support (Safari)
      video.src = url;
      video.muted = muted;

      video.addEventListener("loadedmetadata", () => {
        setIsLoading(false);
        if (autoPlay) {
          video.play().catch(() => {});
        }
      });

      video.addEventListener("error", () => {
        setIsError(true);
        setError("Failed to load stream");
        setIsLoading(false);
      });
    } else {
      setIsError(true);
      setError("HLS is not supported in this browser");
      setIsLoading(false);
    }
  }, [cameraId, enabled, autoPlay, muted, hlsConfig, getStreamUrl, collectStats]);

  /**
   * Play the video.
   */
  const play = useCallback(() => {
    const video = videoRef.current;
    if (video) {
      video.play().catch(() => {});
    }
  }, []);

  /**
   * Pause the video.
   */
  const pause = useCallback(() => {
    const video = videoRef.current;
    if (video) {
      video.pause();
    }
  }, []);

  /**
   * Stop the stream and clean up resources.
   */
  const stop = useCallback(() => {
    if (hlsRef.current) {
      hlsRef.current.destroy();
      hlsRef.current = null;
    }
    if (statsIntervalRef.current) {
      clearInterval(statsIntervalRef.current);
      statsIntervalRef.current = null;
    }
    const video = videoRef.current;
    if (video) {
      video.pause();
      video.removeAttribute("src");
      video.load();
    }
    setIsPlaying(false);
    setStats(null);
  }, []);

  /**
   * Retry the stream connection.
   */
  const retry = useCallback(() => {
    stop();
    initStream();
  }, [stop, initStream]);

  /**
   * Take a snapshot of the current video frame.
   * @returns Data URL of the snapshot image, or null if unavailable.
   */
  const takeSnapshot = useCallback((): string | null => {
    const video = videoRef.current;
    if (!video || video.readyState < 2) return null;

    try {
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      const ctx = canvas.getContext("2d");
      if (!ctx) return null;
      ctx.drawImage(video, 0, 0);
      return canvas.toDataURL("image/jpeg", 0.85);
    } catch {
      return null;
    }
  }, []);

  // Track video playing state
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    const handlePlay = () => setIsPlaying(true);
    const handlePause = () => setIsPlaying(false);
    const handleEnded = () => setIsPlaying(false);

    video.addEventListener("play", handlePlay);
    video.addEventListener("pause", handlePause);
    video.addEventListener("ended", handleEnded);

    return () => {
      video.removeEventListener("play", handlePlay);
      video.removeEventListener("pause", handlePause);
      video.removeEventListener("ended", handleEnded);
    };
  }, []);

  // Initialize stream when cameraId changes
  useEffect(() => {
    if (cameraId && enabled) {
      initStream();
    }

    return () => {
      stop();
    };
  }, [cameraId, enabled]); // eslint-disable-line react-hooks/exhaustive-deps

  return {
    streamUrl,
    isLoading,
    isPlaying,
    isError,
    error,
    stats,
    videoRef,
    play,
    pause,
    stop,
    retry,
    takeSnapshot,
  };
}
