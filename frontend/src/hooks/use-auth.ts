"use client";

import { useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/stores/auth-store";
import type { LoginRequest, User } from "@/types/api";

/**
 * Authentication hook providing user state and auth actions.
 *
 * Usage:
 * ```tsx
 * const { user, isAuthenticated, isLoading, login, logout } = useAuth();
 * ```
 */
export function useAuth() {
  const router = useRouter();

  const user = useAuthStore((state) => state.user);
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  const isLoading = useAuthStore((state) => state.isLoading);
  const isInitialized = useAuthStore((state) => state.isInitialized);
  const error = useAuthStore((state) => state.error);
  const storeLogin = useAuthStore((state) => state.login);
  const storeLogout = useAuthStore((state) => state.logout);
  const initialize = useAuthStore((state) => state.initialize);
  const clearError = useAuthStore((state) => state.clearError);
  const setUser = useAuthStore((state) => state.setUser);

  // Initialize auth state on mount
  useEffect(() => {
    if (!isInitialized) {
      initialize();
    }
  }, [isInitialized, initialize]);

  /**
   * Login with email and password, then redirect to dashboard.
   */
  const login = useCallback(
    async (credentials: LoginRequest) => {
      await storeLogin(credentials);
      router.push("/dashboard");
    },
    [storeLogin, router]
  );

  /**
   * Logout and redirect to login page.
   */
  const logout = useCallback(() => {
    storeLogout();
    router.push("/login");
  }, [storeLogout, router]);

  /**
   * Update user profile data in the store.
   */
  const updateUser = useCallback(
    (userData: User) => {
      setUser(userData);
    },
    [setUser]
  );

  return {
    /** Current authenticated user, or null. */
    user,
    /** Whether the user is authenticated. */
    isAuthenticated,
    /** Whether an auth operation is in progress. */
    isLoading,
    /** Whether the initial auth check has completed. */
    isInitialized,
    /** Error message from the last auth operation. */
    error,
    /** Login with credentials and redirect to dashboard. */
    login,
    /** Logout and redirect to login page. */
    logout,
    /** Update user profile data. */
    updateUser,
    /** Clear any auth error. */
    clearError,
  };
}

/**
 * Hook that requires authentication. Redirects to login if not authenticated.
 * Use this in protected page components.
 *
 * Usage:
 * ```tsx
 * export default function DashboardPage() {
 *   const { user, isLoading } = useRequireAuth();
 *   if (isLoading) return <LoadingSpinner />;
 *   return <Dashboard user={user} />;
 * }
 * ```
 */
export function useRequireAuth() {
  const auth = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (auth.isInitialized && !auth.isAuthenticated) {
      router.replace("/login");
    }
  }, [auth.isInitialized, auth.isAuthenticated, router]);

  return auth;
}

/**
 * Hook for role-based access control.
 *
 * @param allowedRoles - Array of roles allowed to access the resource
 * @returns Auth state plus a `hasAccess` boolean
 */
export function useRoleAuth(allowedRoles: string[]) {
  const auth = useRequireAuth();

  const hasAccess = auth.user ? allowedRoles.includes(auth.user.role) : false;

  return {
    ...auth,
    /** Whether the current user has access based on their role. */
    hasAccess,
  };
}
