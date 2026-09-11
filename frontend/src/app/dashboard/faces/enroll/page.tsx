'use client';

import { useState, useRef, useCallback } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import {
  ArrowLeft,
  Upload,
  X,
  Loader2,
  UserPlus,
  Image as ImageIcon,
  Plus,
  CheckCircle2,
  AlertCircle,
} from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { apiClient } from '@/lib/api-client';

const enrollSchema = z.object({
  name: z.string().min(1, 'Name is required').max(100),
  group: z.string().min(1, 'Group is required'),
  employee_id: z.string().optional(),
  notes: z.string().optional(),
});

type EnrollFormData = z.infer<typeof enrollSchema>;

interface UploadedImage {
  id: string;
  file: File;
  preview: string;
  status: 'pending' | 'uploading' | 'done' | 'error';
  error?: string;
}

export default function FaceEnrollPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const editId = searchParams.get('edit');
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [images, setImages] = useState<UploadedImage[]>([]);
  const [isEnrolling, setIsEnrolling] = useState(false);
  const [enrollSuccess, setEnrollSuccess] = useState(false);
  const [enrollError, setEnrollError] = useState<string | null>(null);

  const { data: existingPerson } = useQuery({
    queryKey: ['persons', editId],
    queryFn: async () => {
      const res = await apiClient.get(`/api/v1/faces/persons/${editId}`);
      return res.data;
    },
    enabled: !!editId,
  });

  const {
    register,
    handleSubmit,
    formState: { errors },
    reset,
  } = useForm<EnrollFormData>({
    resolver: zodResolver(enrollSchema),
    values: existingPerson
      ? {
          name: existingPerson.name,
          group: existingPerson.group,
          employee_id: existingPerson.employee_id || '',
          notes: existingPerson.notes || '',
        }
      : undefined,
  });

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    const newImages: UploadedImage[] = files.map((file) => ({
      id: crypto.randomUUID(),
      file,
      preview: URL.createObjectURL(file),
      status: 'pending' as const,
    }));
    setImages((prev) => [...prev, ...newImages]);
    if (fileInputRef.current) fileInputRef.current.value = '';
  }, []);

  const removeImage = (id: string) => {
    setImages((prev) => {
      const img = prev.find((i) => i.id === id);
      if (img) URL.revokeObjectURL(img.preview);
      return prev.filter((i) => i.id !== id);
    });
  };

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const files = Array.from(e.dataTransfer.files).filter((f) =>
      f.type.startsWith('image/')
    );
    const newImages: UploadedImage[] = files.map((file) => ({
      id: crypto.randomUUID(),
      file,
      preview: URL.createObjectURL(file),
      status: 'pending' as const,
    }));
    setImages((prev) => [...prev, ...newImages]);
  }, []);

  const onSubmit = async (data: EnrollFormData) => {
    if (images.length === 0 && !editId) {
      setEnrollError('Please upload at least one face image.');
      return;
    }

    setIsEnrolling(true);
    setEnrollError(null);

    try {
      let personId = editId;

      if (editId) {
        await apiClient.put(`/api/v1/faces/persons/${editId}`, data);
      } else {
        const res = await apiClient.post('/api/v1/faces/persons', data);
        personId = res.data.id;
      }

      // Upload face images
      for (let i = 0; i < images.length; i++) {
        const img = images[i];
        setImages((prev) =>
          prev.map((im) =>
            im.id === img.id ? { ...im, status: 'uploading' } : im
          )
        );

        try {
          const formData = new FormData();
          formData.append('file', img.file);
          await apiClient.post(`/api/v1/faces/persons/${personId}/faces`, formData, {
            headers: { 'Content-Type': 'multipart/form-data' },
          });

          setImages((prev) =>
            prev.map((im) =>
              im.id === img.id ? { ...im, status: 'done' } : im
            )
          );
        } catch (err: any) {
          setImages((prev) =>
            prev.map((im) =>
              im.id === img.id
                ? {
                    ...im,
                    status: 'error',
                    error: err?.response?.data?.detail || 'Upload failed',
                  }
                : im
            )
          );
        }
      }

      setEnrollSuccess(true);
      queryClient.invalidateQueries({ queryKey: ['persons'] });

      setTimeout(() => {
        router.push('/dashboard/faces');
      }, 2000);
    } catch (err: any) {
      setEnrollError(
        err?.response?.data?.detail || 'Failed to enroll person.'
      );
    } finally {
      setIsEnrolling(false);
    }
  };

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Link href="/dashboard/faces">
          <Button variant="ghost" size="sm">
            <ArrowLeft className="h-4 w-4" />
          </Button>
        </Link>
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">
            {editId ? 'Edit Person' : 'Enroll New Person'}
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {editId
              ? 'Update person details and add more face images'
              : 'Register a new person in the face recognition database'}
          </p>
        </div>
      </div>

      {enrollSuccess && (
        <div className="flex items-center gap-2 rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700 dark:border-green-800 dark:bg-green-900/30 dark:text-green-400">
          <CheckCircle2 className="h-4 w-4" />
          Person {editId ? 'updated' : 'enrolled'} successfully! Redirecting...
        </div>
      )}

      {enrollError && (
        <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">
          <AlertCircle className="h-4 w-4" />
          {enrollError}
        </div>
      )}

      <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
        {/* Person Details */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Person Details</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label>Full Name *</Label>
                <Input placeholder="John Doe" {...register('name')} />
                {errors.name && (
                  <p className="text-xs text-red-500">{errors.name.message}</p>
                )}
              </div>
              <div className="space-y-2">
                <Label>Group *</Label>
                <Input placeholder="employees, visitors, vip..." {...register('group')} />
                {errors.group && (
                  <p className="text-xs text-red-500">{errors.group.message}</p>
                )}
              </div>
            </div>
            <div className="space-y-2">
              <Label>Employee ID (optional)</Label>
              <Input placeholder="EMP-001" {...register('employee_id')} />
            </div>
            <div className="space-y-2">
              <Label>Notes (optional)</Label>
              <textarea
                placeholder="Additional notes about this person..."
                className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                rows={3}
                {...register('notes')}
              />
            </div>
          </CardContent>
        </Card>

        {/* Face Images */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Face Images</CardTitle>
            <CardDescription>
              Upload clear, front-facing photos. Multiple images improve recognition accuracy.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* Drop Zone */}
            <div
              onDragOver={(e) => e.preventDefault()}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              className="flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-slate-300 bg-slate-50 p-8 transition-colors hover:border-blue-400 hover:bg-blue-50/50 dark:border-slate-600 dark:bg-slate-800 dark:hover:border-blue-500 dark:hover:bg-blue-900/10"
            >
              <Upload className="mb-2 h-8 w-8 text-slate-400" />
              <p className="text-sm font-medium text-slate-600 dark:text-slate-300">
                Drop images here or click to browse
              </p>
              <p className="mt-1 text-xs text-slate-400">
                Supports JPG, PNG, WebP. Max 10MB per image.
              </p>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                multiple
                onChange={handleFileSelect}
                className="hidden"
              />
            </div>

            {/* Image Previews */}
            {images.length > 0 && (
              <div className="grid grid-cols-3 gap-3 sm:grid-cols-4">
                {images.map((img) => (
                  <div
                    key={img.id}
                    className="relative aspect-square overflow-hidden rounded-lg border border-slate-200 dark:border-slate-700"
                  >
                    <img
                      src={img.preview}
                      alt="Face"
                      className="h-full w-full object-cover"
                    />
                    {img.status === 'uploading' && (
                      <div className="absolute inset-0 flex items-center justify-center bg-black/50">
                        <Loader2 className="h-6 w-6 animate-spin text-white" />
                      </div>
                    )}
                    {img.status === 'done' && (
                      <div className="absolute inset-0 flex items-center justify-center bg-green-500/30">
                        <CheckCircle2 className="h-6 w-6 text-white" />
                      </div>
                    )}
                    {img.status === 'error' && (
                      <div className="absolute inset-0 flex flex-col items-center justify-center bg-red-500/30">
                        <AlertCircle className="h-6 w-6 text-white" />
                        <span className="mt-1 text-[10px] text-white">{img.error}</span>
                      </div>
                    )}
                    {img.status === 'pending' && (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          removeImage(img.id);
                        }}
                        className="absolute right-1 top-1 rounded-full bg-black/50 p-1 text-white hover:bg-black/70"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    )}
                  </div>
                ))}
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className="flex aspect-square items-center justify-center rounded-lg border-2 border-dashed border-slate-300 text-slate-400 hover:border-blue-400 hover:text-blue-500 dark:border-slate-600"
                >
                  <Plus className="h-8 w-8" />
                </button>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Submit */}
        <div className="flex justify-end gap-3">
          <Link href="/dashboard/faces">
            <Button type="button" variant="outline">
              Cancel
            </Button>
          </Link>
          <Button type="submit" disabled={isEnrolling || enrollSuccess}>
            {isEnrolling ? (
              <>
                <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                {editId ? 'Updating...' : 'Enrolling...'}
              </>
            ) : (
              <>
                <UserPlus className="mr-1 h-4 w-4" />
                {editId ? 'Update Person' : 'Enroll Person'}
              </>
            )}
          </Button>
        </div>
      </form>
    </div>
  );
}
