import axios, {
  AxiosError,
  AxiosInstance,
  AxiosRequestConfig,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from "axios";
import { getAccessToken, refreshAccessToken, removeAccessToken } from "./auth";
import type { ApiResponse, ErrorResponse } from "@/types/api";

function getDynamicBaseUrl(): string {
  if (process.env.NEXT_PUBLIC_API_BASE_URL) {
    return process.env.NEXT_PUBLIC_API_BASE_URL;
  }
  if (typeof window !== "undefined") {
    const hostname = window.location.hostname;
    return `http://${hostname}:8000`;
  }
  return "http://localhost:8000";
}

/**
 * Axios instance pre-configured with base URL, timeout, and interceptors.
 */
const axiosInstance: AxiosInstance = axios.create({
  baseURL: getDynamicBaseUrl(),
  timeout: 30000,
  headers: {
    "Content-Type": "application/json",
    Accept: "application/json",
  },
});

/**
 * Flag to prevent multiple concurrent token refresh requests.
 */
let isRefreshing = false;

/**
 * Queue of failed requests waiting for a token refresh.
 */
let failedQueue: Array<{
  resolve: (token: string) => void;
  reject: (error: unknown) => void;
}> = [];

/**
 * Process the queue of failed requests after a successful token refresh.
 */
function processQueue(error: unknown, token: string | null = null): void {
  failedQueue.forEach((promise) => {
    if (error) {
      promise.reject(error);
    } else {
      promise.resolve(token!);
    }
  });
  failedQueue = [];
}

/**
 * Request interceptor: attach JWT access token from localStorage and update host dynamically.
 */
axiosInstance.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    if (typeof window !== "undefined" && (!config.baseURL || config.baseURL.includes("localhost"))) {
      const hostname = window.location.hostname;
      if (hostname !== "localhost" && hostname !== "127.0.0.1") {
        config.baseURL = `http://${hostname}:8000`;
      }
    }
    const token = getAccessToken();
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error: AxiosError) => {
    return Promise.reject(error);
  }
);

/**
 * Response interceptor:
 * - On 401: attempt token refresh, then retry the original request.
 * - On other errors: normalize and reject.
 */
axiosInstance.interceptors.response.use(
  (response: AxiosResponse) => {
    // Auto-unwrap backend {status, data} / {status, data, meta} envelope
    const body = response.data;
    if (
      body &&
      typeof body === "object" &&
      !Array.isArray(body) &&
      "status" in body &&
      "data" in body
    ) {
      if (body.meta && typeof body.meta === "object") {
        // Paginated: {status, data: [...], meta: {total, page, ...}}
        // Flatten to: {data: [...], total, page, ...}
        response.data = { ...body.meta, data: body.data };
      } else {
        // Simple wrapped: {status, data: ...}
        response.data = body.data;
      }
    }
    return response;
  },
  async (error: AxiosError<ErrorResponse>) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & {
      _retry?: boolean;
    };

    // Handle 401 Unauthorized - attempt token refresh
    if (error.response?.status === 401 && !originalRequest._retry) {
      if (isRefreshing) {
        // Queue subsequent 401 requests while refreshing
        return new Promise((resolve, reject) => {
          failedQueue.push({
            resolve: (token: string) => {
              if (originalRequest.headers) {
                originalRequest.headers.Authorization = `Bearer ${token}`;
              }
              resolve(axiosInstance(originalRequest));
            },
            reject,
          });
        });
      }

      originalRequest._retry = true;
      isRefreshing = true;

      try {
        const newToken = await refreshAccessToken();
        if (newToken) {
          processQueue(null, newToken);
          if (originalRequest.headers) {
            originalRequest.headers.Authorization = `Bearer ${newToken}`;
          }
          return axiosInstance(originalRequest);
        } else {
          processQueue(new Error("Token refresh failed"));
          removeAccessToken();
          // Redirect to login if in browser
          if (typeof window !== "undefined") {
            window.location.href = "/login";
          }
          return Promise.reject(error);
        }
      } catch (refreshError) {
        processQueue(refreshError);
        removeAccessToken();
        if (typeof window !== "undefined") {
          window.location.href = "/login";
        }
        return Promise.reject(refreshError);
      } finally {
        isRefreshing = false;
      }
    }

    // Normalize error response
    let baseMessage =
      error.response?.data?.message ||
      error.response?.data?.detail ||
      error.message ||
      "An unexpected error occurred";

    const rawErrors = error.response?.data?.errors;
    let formattedMessage = baseMessage;

    if (Array.isArray(rawErrors) && rawErrors.length > 0) {
      const detailedMsgs = rawErrors
        .map((e: any) => {
          if (typeof e === "string") return e;
          if (e && typeof e === "object") {
            const fieldName = e.field ? e.field.replace(/^body\s*->\s*/, "") : "";
            return fieldName ? `${fieldName}: ${e.message || "invalid value"}` : (e.message || "invalid value");
          }
          return null;
        })
        .filter(Boolean);

      if (detailedMsgs.length > 0) {
        formattedMessage = detailedMsgs.join("; ");
      }
    }

    const errorResponse: ErrorResponse = {
      message: formattedMessage,
      detail: error.response?.data?.detail || baseMessage,
      status: error.response?.status || 500,
      errors: rawErrors,
    };

    return Promise.reject(errorResponse);
  }
);

/**
 * Typed API client methods wrapping the Axios instance.
 */
export const api = {
  /**
   * Perform a GET request. Response envelope is auto-unwrapped by interceptor.
   */
  get<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    return axiosInstance.get<T>(url, config).then((res) => res.data);
  },

  /**
   * Perform a GET request and return the raw Axios response (for pagination, etc.).
   */
  getRaw<T>(url: string, config?: AxiosRequestConfig): Promise<AxiosResponse<T>> {
    return axiosInstance.get<T>(url, config);
  },

  /**
   * Perform a POST request. Response envelope is auto-unwrapped by interceptor.
   */
  post<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return axiosInstance.post<T>(url, data, config).then((res) => res.data);
  },

  /**
   * Perform a PUT request. Response envelope is auto-unwrapped by interceptor.
   */
  put<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return axiosInstance.put<T>(url, data, config).then((res) => res.data);
  },

  /**
   * Perform a PATCH request. Response envelope is auto-unwrapped by interceptor.
   */
  patch<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return axiosInstance.patch<T>(url, data, config).then((res) => res.data);
  },

  /**
   * Perform a DELETE request. Response envelope is auto-unwrapped by interceptor.
   */
  delete<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    return axiosInstance.delete<T>(url, config).then((res) => res.data);
  },

  /**
   * Upload a file via multipart/form-data POST. Response envelope is auto-unwrapped by interceptor.
   */
  upload<T>(url: string, formData: FormData, config?: AxiosRequestConfig): Promise<T> {
    return axiosInstance
      .post<T>(url, formData, {
        ...config,
        headers: {
          ...config?.headers,
          "Content-Type": "multipart/form-data",
        },
      })
      .then((res) => res.data);
  },
};

/**
 * Export the raw axios instance for advanced use cases.
 */
export { axiosInstance };
export { axiosInstance as apiClient };
export default api;
