'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useAuthStore } from '@/stores/useAuthStore';
import { Loader2, Eye } from 'lucide-react';

export default function RootPage() {
  const router = useRouter();
  const { accessToken, isAuthenticated } = useAuthStore();

  useEffect(() => {
    if (accessToken && isAuthenticated()) {
      router.replace('/dashboard');
    } else {
      router.replace('/login');
    }
  }, [accessToken, isAuthenticated, router]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-900 via-blue-950 to-slate-900">
      <div className="flex flex-col items-center gap-4">
        <div className="flex items-center gap-3">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-blue-600 shadow-lg shadow-blue-600/30">
            <Eye className="h-7 w-7 text-white" />
          </div>
          <span className="text-3xl font-bold text-white">IBVAP</span>
        </div>
        <Loader2 className="h-6 w-6 animate-spin text-blue-400" />
        <p className="text-sm text-slate-400">Redirecting...</p>
      </div>
    </div>
  );
}
