'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { ArrowLeft, Eye, Loader2, Mail, CheckCircle2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { apiClient } from '@/lib/api-client';

const forgotPasswordSchema = z.object({
  email: z.string().email('Please enter a valid email address'),
});

type ForgotPasswordFormData = z.infer<typeof forgotPasswordSchema>;

export default function ForgotPasswordPage() {
  const [serverError, setServerError] = useState<string | null>(null);
  const [isSuccess, setIsSuccess] = useState(false);
  const [submittedEmail, setSubmittedEmail] = useState('');

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<ForgotPasswordFormData>({
    resolver: zodResolver(forgotPasswordSchema),
    defaultValues: {
      email: '',
    },
  });

  const onSubmit = async (data: ForgotPasswordFormData) => {
    setServerError(null);
    try {
      await apiClient.post('/api/v1/auth/forgot-password', {
        email: data.email,
      });
      setSubmittedEmail(data.email);
      setIsSuccess(true);
    } catch (error: any) {
      const message =
        error?.message ||
        error?.detail ||
        error?.response?.data?.detail ||
        'Failed to send reset link. Please try again.';
      setServerError(message);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-50 via-blue-50 to-slate-100 px-4 dark:from-slate-900 dark:via-blue-950 dark:to-slate-900">
      <div className="w-full max-w-md">
        {/* Logo */}
        <div className="mb-8 flex flex-col items-center">
          <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-blue-600 shadow-lg shadow-blue-600/30">
            <Eye className="h-8 w-8 text-white" />
          </div>
          <h1 className="text-3xl font-bold tracking-tight text-slate-900 dark:text-white">
            IBVAP
          </h1>
        </div>

        <Card className="border-slate-200 shadow-xl dark:border-slate-700 dark:bg-slate-800/50">
          {isSuccess ? (
            <>
              <CardHeader className="space-y-1 pb-4 text-center">
                <div className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-full bg-green-100 dark:bg-green-900/30">
                  <CheckCircle2 className="h-6 w-6 text-green-600 dark:text-green-400" />
                </div>
                <CardTitle className="text-xl font-semibold">Check your email</CardTitle>
                <CardDescription>
                  We sent a password reset link to{' '}
                  <span className="font-medium text-slate-700 dark:text-slate-300">
                    {submittedEmail}
                  </span>
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4 text-center">
                <p className="text-sm text-slate-500 dark:text-slate-400">
                  Click the link in the email to reset your password. If you do not see the
                  email, check your spam folder.
                </p>
              </CardContent>
              <CardFooter className="flex flex-col gap-3">
                <Button
                  variant="outline"
                  className="w-full"
                  onClick={() => {
                    setIsSuccess(false);
                    setSubmittedEmail('');
                  }}
                >
                  Try a different email
                </Button>
                <Link
                  href="/login"
                  className="inline-flex items-center gap-1 text-sm text-blue-600 hover:text-blue-700 hover:underline dark:text-blue-400"
                >
                  <ArrowLeft className="h-3 w-3" />
                  Back to sign in
                </Link>
              </CardFooter>
            </>
          ) : (
            <>
              <CardHeader className="space-y-1 pb-4">
                <CardTitle className="text-xl font-semibold text-center">
                  Reset your password
                </CardTitle>
                <CardDescription className="text-center">
                  Enter your email address and we will send you a reset link
                </CardDescription>
              </CardHeader>

              <form onSubmit={handleSubmit(onSubmit)}>
                <CardContent className="space-y-4">
                  {serverError && (
                    <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">
                      {serverError}
                    </div>
                  )}

                  <div className="space-y-2">
                    <Label htmlFor="email">Email address</Label>
                    <div className="relative">
                      <Mail className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                      <Input
                        id="email"
                        type="email"
                        placeholder="name@company.com"
                        className="pl-10"
                        autoComplete="email"
                        autoFocus
                        disabled={isSubmitting}
                        {...register('email')}
                      />
                    </div>
                    {errors.email && (
                      <p className="text-xs text-red-500">{errors.email.message}</p>
                    )}
                  </div>
                </CardContent>

                <CardFooter className="flex flex-col gap-3">
                  <Button type="submit" className="w-full" disabled={isSubmitting}>
                    {isSubmitting ? (
                      <>
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        Sending reset link...
                      </>
                    ) : (
                      'Send reset link'
                    )}
                  </Button>
                  <Link
                    href="/login"
                    className="inline-flex items-center gap-1 text-sm text-blue-600 hover:text-blue-700 hover:underline dark:text-blue-400"
                  >
                    <ArrowLeft className="h-3 w-3" />
                    Back to sign in
                  </Link>
                </CardFooter>
              </form>
            </>
          )}
        </Card>
      </div>
    </div>
  );
}
