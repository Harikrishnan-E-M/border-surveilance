import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { User, LoginRequest, LoginResponse } from "@/types/api";
import {
  setAccessToken,
  setRefreshToken,
  getAccessToken,
  isAuthenticated as checkAuth,
  clearTokens,
  refreshAccessToken as doRefresh,
} from "@/lib/auth";
import { api } from "@/lib/api-client";

// ─── State Interface ────────────────────────────────────────────────────────

export interface AuthState {
  /** Current authenticated user, or null if not logged in. */
  user: User | null;

  /** Whether the user is currently authenticated. */
  isAuthenticated: boolean;

  /** Whether an auth operation is in progress. */
  isLoading: boolean;

  /** Error message from the last auth operation, or null. */
  error: string | null;

  /** Whether the initial auth check has completed. */
  isInitialized: boolean;
}

export interface AuthActions {
  /**
   * Authenticate the user with email and password.
   * Stores tokens and sets the user state.
   */
  login: (credentials: LoginRequest) => Promise<void>;

  /**
   * Log out the current user.
   * Clears tokens and resets state.
   */
  logout: () => void;

  /**
   * Refresh the access token using the stored refresh token.
   * Updates the user state on success, logs out on failure.
   */
  refreshToken: () => Promise<boolean>;

  /**
   * Initialize auth state from stored tokens.
   * Called once on app load to restore session.
   */
  initialize: () => Promise<void>;

  /**
   * Update the user profile in the store.
   */
  setUser: (user: User) => void;

  /**
   * Clear any auth error message.
   */
  clearError: () => void;
}

export type AuthStore = AuthState & AuthActions;

// ─── Store Implementation ───────────────────────────────────────────────────

export const useAuthStore = create<AuthStore>()(
  persist(
    (set, get) => ({
      // ── State ──────────────────────────────────────────────────────────
      user: null,
      isAuthenticated: false,
      isLoading: false,
      error: null,
      isInitialized: false,

      // ── Actions ────────────────────────────────────────────────────────

      login: async (credentials: LoginRequest) => {
        set({ isLoading: true, error: null });

        try {
          const response = await api.post<LoginResponse>("/api/v1/auth/login", credentials);

          setAccessToken(response.access_token);
          setRefreshToken(response.refresh_token);

          set({
            user: response.user,
            isAuthenticated: true,
            isLoading: false,
            error: null,
          });
        } catch (err: unknown) {
          const message =
            err && typeof err === "object" && "message" in err
              ? (err as { message: string }).message
              : "Login failed. Please check your credentials.";

          set({
            user: null,
            isAuthenticated: false,
            isLoading: false,
            error: message,
          });

          throw err;
        }
      },

      logout: () => {
        // Fire-and-forget server-side logout
        api.post("/api/v1/auth/logout").catch(() => {
          // Ignore errors during logout
        });

        clearTokens();

        set({
          user: null,
          isAuthenticated: false,
          isLoading: false,
          error: null,
        });
      },

      refreshToken: async () => {
        try {
          const newToken = await doRefresh();
          if (newToken) {
            // Fetch fresh user data
            const user = await api.get<User>("/api/v1/auth/me");
            set({ user, isAuthenticated: true });
            return true;
          }

          // Refresh failed
          get().logout();
          return false;
        } catch {
          get().logout();
          return false;
        }
      },

      initialize: async () => {
        const token = getAccessToken();

        if (!token || !checkAuth()) {
          set({ isInitialized: true, isAuthenticated: false, user: null });
          return;
        }

        set({ isLoading: true });

        try {
          const user = await api.get<User>("/api/v1/auth/me");
          set({
            user,
            isAuthenticated: true,
            isLoading: false,
            isInitialized: true,
          });
        } catch {
          // Token may be expired, try refreshing
          const refreshed = await get().refreshToken();
          set({
            isLoading: false,
            isInitialized: true,
            isAuthenticated: refreshed,
          });
        }
      },

      setUser: (user: User) => {
        set({ user });
      },

      clearError: () => {
        set({ error: null });
      },
    }),
    {
      name: "visionai-auth",
      storage: createJSONStorage(() => localStorage),
      partialize: (state) =>
        ({
          user: state.user,
          isAuthenticated: state.isAuthenticated,
        }) as Partial<AuthStore>,
    }
  )
);
