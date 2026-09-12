'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import {
  Camera,
  Plus,
  Search,
  LayoutGrid,
  List,
  MapPin,
  Wifi,
  WifiOff,
  Loader2,
  X,
  Video,
  Settings,
  AlertCircle,
  Trash2,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { apiClient } from '@/lib/api-client';

interface CameraItem {
  id: string;
  name: string;
  location: string;
  stream_url: string;
  status: 'online' | 'offline' | 'degraded';
  protocol: string;
  thumbnail_url: string | null;
  created_at: string;
}

const addCameraSchema = z.object({
  name: z.string().min(1, 'Camera name is required'),
  location: z.string().min(1, 'Location is required'),
  stream_url: z.string().min(1, 'Stream URL is required'),
  protocol: z.string().default('http'),
  username: z.string().optional(),
  password: z.string().optional(),
});

type AddCameraFormData = z.infer<typeof addCameraSchema>;

export default function CamerasPage() {
  const queryClient = useQueryClient();
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');
  const [searchQuery, setSearchQuery] = useState('');
  const [showAddDialog, setShowAddDialog] = useState(false);

  const { data: camerasData, isLoading } = useQuery({
    queryKey: ['cameras', 'all'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/cameras', { params: { limit: 200 } });
      return (res.data.items || res.data) as CameraItem[];
    },
  });

  const cameras = camerasData || [];
  const filteredCameras = cameras.filter(
    (cam) =>
      cam.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      cam.location.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const addCameraMutation = useMutation({
    mutationFn: async (data: AddCameraFormData) => {
      const res = await apiClient.post('/api/v1/cameras', data);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['cameras'] });
      setShowAddDialog(false);
      reset();
    },
  });

  const deleteCameraMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/api/v1/cameras/${id}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['cameras'] });
    },
  });

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<AddCameraFormData>({
    resolver: zodResolver(addCameraSchema),
    defaultValues: {
      protocol: 'rtsp',
    },
  });

  const statusBadge = (status: string) => {
    switch (status) {
      case 'online':
        return (
          <Badge className="bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400">
            <Wifi className="mr-1 h-3 w-3" />
            Online
          </Badge>
        );
      case 'offline':
        return (
          <Badge className="bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400">
            <WifiOff className="mr-1 h-3 w-3" />
            Offline
          </Badge>
        );
      default:
        return (
          <Badge className="bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400">
            <AlertCircle className="mr-1 h-3 w-3" />
            Degraded
          </Badge>
        );
    }
  };

  const protocolBadge = (protocol: string) => (
    <Badge variant="outline" className="text-xs">
      {protocol.toUpperCase()}
    </Badge>
  );

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Cameras</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {cameras.length} camera{cameras.length !== 1 ? 's' : ''} configured
          </p>
        </div>
        <Button onClick={() => setShowAddDialog(true)}>
          <Plus className="mr-1 h-4 w-4" />
          Add Camera
        </Button>
      </div>

      {/* Search + View Toggle */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <Input
            placeholder="Search cameras by name or location..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-10"
          />
        </div>
        <div className="flex rounded-lg border border-slate-200 dark:border-slate-700">
          <button
            onClick={() => setViewMode('grid')}
            className={`p-2 ${
              viewMode === 'grid'
                ? 'bg-blue-600 text-white'
                : 'bg-white text-slate-500 dark:bg-slate-800 dark:text-slate-400'
            } rounded-l-lg`}
          >
            <LayoutGrid className="h-4 w-4" />
          </button>
          <button
            onClick={() => setViewMode('list')}
            className={`p-2 ${
              viewMode === 'list'
                ? 'bg-blue-600 text-white'
                : 'bg-white text-slate-500 dark:bg-slate-800 dark:text-slate-400'
            } rounded-r-lg`}
          >
            <List className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="flex h-64 items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
        </div>
      ) : filteredCameras.length === 0 ? (
        <div className="flex h-64 flex-col items-center justify-center text-slate-400">
          <Camera className="mb-3 h-10 w-10" />
          <p className="text-sm font-medium">
            {searchQuery ? 'No cameras match your search' : 'No cameras configured'}
          </p>
          {!searchQuery && (
            <Button variant="link" className="mt-1" onClick={() => setShowAddDialog(true)}>
              Add your first camera
            </Button>
          )}
        </div>
      ) : viewMode === 'grid' ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {filteredCameras.map((cam) => (
            <Link key={cam.id} href={`/dashboard/cameras/${cam.id}`}>
              <Card className="overflow-hidden transition-shadow hover:shadow-md">
                {/* Thumbnail */}
                <div className="relative aspect-video bg-slate-900">
                  {cam.thumbnail_url ? (
                    <img
                      src={cam.thumbnail_url}
                      alt={cam.name}
                      className="h-full w-full object-cover"
                    />
                  ) : (
                    <div className="flex h-full w-full items-center justify-center">
                      <Video className="h-8 w-8 text-slate-600" />
                    </div>
                  )}
                  <div className="absolute left-2 top-2">
                    {statusBadge(cam.status)}
                  </div>
                  <div className="absolute right-2 top-2">
                    {protocolBadge(cam.protocol)}
                  </div>
                </div>
                <CardContent className="p-4 flex items-center justify-between">
                  <div>
                    <h3 className="font-semibold text-slate-900 dark:text-white">{cam.name}</h3>
                    <div className="mt-1 flex items-center gap-1 text-xs text-slate-500 dark:text-slate-400">
                      <MapPin className="h-3 w-3" />
                      {cam.location}
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-red-500 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-950/30"
                    title="Delete camera"
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      if (confirm(`Are you sure you want to delete camera "${cam.name}"?`)) {
                        deleteCameraMutation.mutate(cam.id);
                      }
                    }}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      ) : (
        <Card>
          <CardContent className="p-0">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50/50 dark:border-slate-700 dark:bg-slate-800/50">
                  <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                    Name
                  </th>
                  <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                    Location
                  </th>
                  <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                    Status
                  </th>
                  <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">
                    Protocol
                  </th>
                  <th className="px-4 py-3 text-right font-medium text-slate-600 dark:text-slate-300">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {filteredCameras.map((cam) => (
                  <tr
                    key={cam.id}
                    className="border-b border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50"
                  >
                    <td className="px-4 py-3">
                      <Link
                        href={`/dashboard/cameras/${cam.id}`}
                        className="font-medium text-blue-600 hover:underline dark:text-blue-400"
                      >
                        {cam.name}
                      </Link>
                    </td>
                    <td className="px-4 py-3 text-slate-600 dark:text-slate-400">
                      <div className="flex items-center gap-1">
                        <MapPin className="h-3 w-3" />
                        {cam.location}
                      </div>
                    </td>
                    <td className="px-4 py-3">{statusBadge(cam.status)}</td>
                    <td className="px-4 py-3">{protocolBadge(cam.protocol)}</td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex items-center justify-end gap-1">
                        <Link href={`/dashboard/cameras/${cam.id}`}>
                          <Button variant="ghost" size="sm" title="Camera Settings">
                            <Settings className="h-4 w-4" />
                          </Button>
                        </Link>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-red-500 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-950/30"
                          title="Delete camera"
                          onClick={() => {
                            if (confirm(`Are you sure you want to delete camera "${cam.name}"?`)) {
                              deleteCameraMutation.mutate(cam.id);
                            }
                          }}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}

      {/* Add Camera Dialog */}
      {showAddDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <Card className="w-full max-w-lg mx-4">
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle>Add Camera</CardTitle>
              <button
                onClick={() => {
                  setShowAddDialog(false);
                  reset();
                }}
                className="text-slate-400 hover:text-slate-600"
              >
                <X className="h-5 w-5" />
              </button>
            </CardHeader>
            <form onSubmit={handleSubmit((data) => addCameraMutation.mutate(data))}>
              <CardContent className="space-y-4">
                {addCameraMutation.isError && (
                  <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">
                    {(addCameraMutation.error as any)?.response?.data?.detail || 'Failed to add camera'}
                  </div>
                )}

                <div className="space-y-2">
                  <Label htmlFor="cam-name">Camera Name</Label>
                  <Input id="cam-name" placeholder="Front Entrance" {...register('name')} />
                  {errors.name && <p className="text-xs text-red-500">{errors.name.message}</p>}
                </div>

                <div className="space-y-2">
                  <Label htmlFor="cam-location">Location</Label>
                  <Input id="cam-location" placeholder="Building A, Floor 1" {...register('location')} />
                  {errors.location && <p className="text-xs text-red-500">{errors.location.message}</p>}
                </div>

                <div className="space-y-2">
                  <Label htmlFor="cam-url">Stream URL</Label>
                  <Input id="cam-url" placeholder="rtsp://192.168.1.100:554/stream" {...register('stream_url')} />
                  {errors.stream_url && <p className="text-xs text-red-500">{errors.stream_url.message}</p>}
                </div>

                <div className="space-y-2">
                  <Label htmlFor="cam-protocol">Protocol</Label>
                  <select
                    id="cam-protocol"
                    {...register('protocol')}
                    className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                  >
                    <option value="http">HTTP / MJPEG Stream</option>
                    <option value="rtsp">RTSP</option>
                    <option value="onvif">ONVIF</option>
                    <option value="usb">USB Device</option>
                    <option value="rtmp">RTMP</option>
                    <option value="hls">HLS</option>
                    <option value="webrtc">WebRTC</option>
                  </select>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label htmlFor="cam-user">Username (optional)</Label>
                    <Input id="cam-user" placeholder="admin" {...register('username')} />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="cam-pass">Password (optional)</Label>
                    <Input id="cam-pass" type="password" placeholder="********" {...register('password')} />
                  </div>
                </div>

                <div className="flex justify-end gap-2 pt-2">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => {
                      setShowAddDialog(false);
                      reset();
                    }}
                  >
                    Cancel
                  </Button>
                  <Button type="submit" disabled={addCameraMutation.isPending}>
                    {addCameraMutation.isPending ? (
                      <>
                        <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                        Adding...
                      </>
                    ) : (
                      <>
                        <Plus className="mr-1 h-4 w-4" />
                        Add Camera
                      </>
                    )}
                  </Button>
                </div>
              </CardContent>
            </form>
          </Card>
        </div>
      )}
    </div>
  );
}
