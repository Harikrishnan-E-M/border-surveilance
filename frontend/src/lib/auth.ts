import axios from "axios";

const ACCESS_TOKEN_KEY = "visionai_access_token";
const REFRESH_TOKEN_KEY = "visionai_refresh_token";
const BASE_URL = "/api/v1";

// ─── Token Storage ──────────────────────────────────────────────────────────

/**
 * Retrieve the stored JWT access token.
 */
export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

/**
 * Store the JWT access token.
 */
export function setAccessToken(token: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(ACCESS_TOKEN_KEY, token);
}

/**
 * Remove the JWT access token.
 */
export function removeAccessToken(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(ACCESS_TOKEN_KEY);
}

/**
 * Retrieve the stored refresh token.
 */
export function getRefreshToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(REFRESH_TOKEN_KEY);
}

/**
 * Store the refresh token.
 */
export function setRefreshToken(token: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(REFRESH_TOKEN_KEY, token);
}

/**
 * Remove the refresh token.
 */
export function removeRefreshToken(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(REFRESH_TOKEN_KEY);
}

/**
 * Remove all authentication tokens.
 */
export function clearTokens(): void {
  removeAccessToken();
  removeRefreshToken();
}

// ─── Token Inspection ───────────────────────────────────────────────────────

/**
 * JWT payload interface.
 */
export interface JWTPayload {
  sub: string;
  exp: number;
  iat: number;
  role?: string;
  email?: string;
  [key: string]: unknown;
}

/**
 * Parse a JWT token and extract its payload without verifying the signature.
 * This is intended for client-side inspection only; the server validates signatures.
 *
 * @param token - JWT string
 * @returns Decoded payload or null if the token is invalid
 */
export function parseJWT(token: string): JWTPayload | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;

    const base64Url = parts[1];
    const base64 = base64Url.replace(/-/g, "+").replace(/_/g, "/");
    const jsonPayload = decodeURIComponent(
      atob(base64)
        .split("")
        .map((c) => "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2))
        .join("")
    );

    return JSON.parse(jsonPayload) as JWTPayload;
  } catch {
    return null;
  }
}

/**
 * Check if the current user is authenticated (has a valid, non-expired token).
 *
 * @returns true if authenticated
 */
export function isAuthenticated(): boolean {
  const token = getAccessToken();
  if (!token) return false;

  const payload = parseJWT(token);
  if (!payload) return false;

  // Check expiration (with 30-second buffer)
  const now = Math.floor(Date.now() / 1000);
  return payload.exp > now + 30;
}

/**
 * Check if the access token is about to expire (within the next 5 minutes).
 *
 * @returns true if the token will expire within 5 minutes
 */
export function isTokenExpiringSoon(): boolean {
  const token = getAccessToken();
  if (!token) return true;

  const payload = parseJWT(token);
  if (!payload) return true;

  const now = Math.floor(Date.now() / 1000);
  const fiveMinutes = 5 * 60;
  return payload.exp < now + fiveMinutes;
}

// ─── Token Refresh ──────────────────────────────────────────────────────────

/**
 * Attempt to refresh the access token using the stored refresh token.
 *
 * @returns The new access token, or null if refresh failed
 */
export async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return null;

  try {
    const response = await axios.post<{
      access_token: string;
      refresh_token?: string;
      token_type: string;
    }>(`${BASE_URL}/auth/refresh`, {
      refresh_token: refreshToken,
    });

    const { access_token, refresh_token: newRefreshToken } = response.data;

    setAccessToken(access_token);
    if (newRefreshToken) {
      setRefreshToken(newRefreshToken);
    }

    return access_token;
  } catch {
    clearTokens();
    return null;
  }
}

/**
 * Get the current user's ID from the access token.
 */
export function getCurrentUserId(): string | null {
  const token = getAccessToken();
  if (!token) return null;

  const payload = parseJWT(token);
  return payload?.sub || null;
}

/**
 * Get the current user's role from the access token.
 */
export function getCurrentUserRole(): string | null {
  const token = getAccessToken();
  if (!token) return null;

  const payload = parseJWT(token);
  return payload?.role || null;
}
