'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import {
  Car,
  Plus,
  Search,
  Loader2,
  Pencil,
  Trash2,
  X,
  ClipboardList,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { apiClient } from '@/lib/api-client';

interface Vehicle {
  id: string;
  plate_number: string;
  make: string;
  model: string;
  color: string;
  owner_name: string;
  category: string;
  notes: string;
  last_seen: string | null;
  created_at: string;
}

const vehicleSchema = z.object({
  plate_number: z.string().min(1, 'Plate number is required'),
  make: z.string().optional(),
  model: z.string().optional(),
  color: z.string().optional(),
  owner_name: z.string().optional(),
  category: z.string().min(1, 'Category is required'),
  notes: z.string().optional(),
});

type VehicleFormData = z.infer<typeof vehicleSchema>;

const categoryOptions = ['authorized', 'blacklisted', 'visitor', 'employee', 'vip'];

export default function VehiclesPage() {
  const queryClient = useQueryClient();
  const [searchQuery, setSearchQuery] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [showAddDialog, setShowAddDialog] = useState(false);

  const { data: vehiclesData, isLoading } = useQuery<{ items: Vehicle[]; total: number }>({
    queryKey: ['vehicles', searchQuery, categoryFilter],
    queryFn: async () => {
      const params: Record<string, any> = { limit: 200 };
      if (searchQuery) params.search = searchQuery;
      if (categoryFilter) params.category = categoryFilter;
      const res: any = await apiClient.get('/api/v1/vehicles', { params });
      const itemsList = res?.items || res?.data?.items || (Array.isArray(res?.data) ? res.data : (Array.isArray(res) ? res : []));
      const items: Vehicle[] = (itemsList || []).map((v: any) => ({
        id: v.id,
        plate_number: v.plate_number,
        make: v.make || '',
        model: v.model || v.model_name || '',
        color: v.color || '',
        owner_name: v.owner_name || '',
        category: v.category || 'authorized',
        notes: v.notes || '',
        last_seen: v.last_seen || null,
        created_at: v.created_at || new Date().toISOString(),
      }));
      return {
        items,
        total: res?.total || res?.data?.total || items.length,
      };
    },
  });

  const vehicles = vehiclesData?.items || [];

  const addMutation = useMutation({
    mutationFn: async (data: VehicleFormData) => {
      await apiClient.post('/api/v1/vehicles', {
        ...data,
        model_name: data.model,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['vehicles'] });
      setShowAddDialog(false);
      reset();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/api/v1/vehicles/${id}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['vehicles'] });
    },
  });

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<VehicleFormData>({
    resolver: zodResolver(vehicleSchema),
    defaultValues: { category: 'authorized' },
  });

  const [editingVehicle, setEditingVehicle] = useState<Vehicle | null>(null);

  const editMutation = useMutation({
    mutationFn: async (data: VehicleFormData & { id: string }) => {
      await apiClient.put(`/api/v1/vehicles/${data.id}`, {
        plate_number: data.plate_number,
        license_plate: data.plate_number,
        make: data.make,
        model: data.model,
        model_name: data.model,
        color: data.color,
        owner_name: data.owner_name,
        category: data.category,
        notes: data.notes,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['vehicles'] });
      setEditingVehicle(null);
    },
  });

  const {
    register: registerEdit,
    handleSubmit: handleSubmitEdit,
    setValue: setEditValue,
    formState: { errors: editErrors },
  } = useForm<VehicleFormData>({
    resolver: zodResolver(vehicleSchema),
  });

  const handleOpenEdit = (v: Vehicle) => {
    setEditingVehicle(v);
    setEditValue('plate_number', v.plate_number);
    setEditValue('make', v.make);
    setEditValue('model', v.model);
    setEditValue('color', v.color);
    setEditValue('owner_name', v.owner_name);
    setEditValue('category', v.category || 'authorized');
    setEditValue('notes', v.notes);
  };

  const categoryBadge = (category: string) => {
    const colors: Record<string, string> = {
      authorized: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
      blacklisted: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
      visitor: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
      employee: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400',
      vip: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
    };
    return (
      <Badge className={`text-xs ${colors[category] || 'bg-slate-100 text-slate-600'}`}>
        {category.charAt(0).toUpperCase() + category.slice(1)}
      </Badge>
    );
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Vehicle Registry</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {vehiclesData?.total || 0} vehicle{(vehiclesData?.total || 0) !== 1 ? 's' : ''} registered
          </p>
        </div>
        <div className="flex gap-2">
          <Link href="/dashboard/vehicles/logs">
            <Button variant="outline" size="sm">
              <ClipboardList className="mr-1 h-4 w-4" />
              Event Logs
            </Button>
          </Link>
          <Button size="sm" onClick={() => setShowAddDialog(true)}>
            <Plus className="mr-1 h-4 w-4" />
            Add Vehicle
          </Button>
        </div>
      </div>

      {/* Search and Filters */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <Input
            placeholder="Search by plate number, make, model, owner..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-10"
          />
        </div>
        <select
          value={categoryFilter}
          onChange={(e) => setCategoryFilter(e.target.value)}
          className="rounded-md border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
        >
          <option value="">All categories</option>
          {categoryOptions.map((c) => (
            <option key={c} value={c}>
              {c.charAt(0).toUpperCase() + c.slice(1)}
            </option>
          ))}
        </select>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="flex h-64 items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
        </div>
      ) : vehicles.length === 0 ? (
        <div className="flex h-64 flex-col items-center justify-center text-slate-400">
          <Car className="mb-3 h-10 w-10" />
          <p className="text-sm font-medium">
            {searchQuery ? 'No vehicles match your search' : 'No vehicles registered'}
          </p>
          {!searchQuery && (
            <Button variant="link" className="mt-1" onClick={() => setShowAddDialog(true)}>
              Register your first vehicle
            </Button>
          )}
        </div>
      ) : (
        <Card>
          <CardContent className="p-0">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50/50 dark:border-slate-700 dark:bg-slate-800/50">
                  <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">Plate</th>
                  <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">Vehicle</th>
                  <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">Color</th>
                  <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">Owner</th>
                  <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">Category</th>
                  <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">Last Seen</th>
                  <th className="px-4 py-3 text-right font-medium text-slate-600 dark:text-slate-300">Actions</th>
                </tr>
              </thead>
              <tbody>
                {vehicles.map((v) => (
                  <tr key={v.id} className="border-b border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50">
                    <td className="px-4 py-3">
                      <span className="rounded bg-slate-100 px-2 py-1 font-mono text-sm font-semibold text-slate-800 dark:bg-slate-700 dark:text-slate-200">
                        {v.plate_number}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-700 dark:text-slate-300">
                      {[v.make, v.model].filter(Boolean).join(' ') || '-'}
                    </td>
                    <td className="px-4 py-3 text-slate-600 dark:text-slate-400">{v.color || '-'}</td>
                    <td className="px-4 py-3 text-slate-600 dark:text-slate-400">{v.owner_name || '-'}</td>
                    <td className="px-4 py-3">{categoryBadge(v.category)}</td>
                    <td className="px-4 py-3 text-slate-500">
                      {v.last_seen ? new Date(v.last_seen).toLocaleString() : 'Never'}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex justify-end gap-1">
                        <Button variant="ghost" size="sm" title="Edit" onClick={() => handleOpenEdit(v)}>
                          <Pencil className="h-3 w-3" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-red-500 hover:text-red-700"
                          title="Delete"
                          onClick={() => {
                            if (confirm(`Delete vehicle ${v.plate_number}?`)) {
                              deleteMutation.mutate(v.id);
                            }
                          }}
                        >
                          <Trash2 className="h-3 w-3" />
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

      {/* Add Vehicle Dialog */}
      {showAddDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <Card className="w-full max-w-lg mx-4">
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle>Register Vehicle</CardTitle>
              <button onClick={() => { setShowAddDialog(false); reset(); }} className="text-slate-400 hover:text-slate-600">
                <X className="h-5 w-5" />
              </button>
            </CardHeader>
            <form onSubmit={handleSubmit((data) => addMutation.mutate(data))}>
              <CardContent className="space-y-4">
                {addMutation.isError && (
                  <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">
                    {(addMutation.error as any)?.response?.data?.detail || 'Failed to register vehicle'}
                  </div>
                )}

                <div className="space-y-2">
                  <Label>Plate Number *</Label>
                  <Input placeholder="ABC-1234" {...register('plate_number')} />
                  {errors.plate_number && <p className="text-xs text-red-500">{errors.plate_number.message}</p>}
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label>Make</Label>
                    <Input placeholder="Toyota" {...register('make')} />
                  </div>
                  <div className="space-y-2">
                    <Label>Model</Label>
                    <Input placeholder="Camry" {...register('model')} />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label>Color</Label>
                    <Input placeholder="White" {...register('color')} />
                  </div>
                  <div className="space-y-2">
                    <Label>Category *</Label>
                    <select
                      {...register('category')}
                      className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                    >
                      {categoryOptions.map((c) => (
                        <option key={c} value={c}>
                          {c.charAt(0).toUpperCase() + c.slice(1)}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                <div className="space-y-2">
                  <Label>Owner Name</Label>
                  <Input placeholder="John Doe" {...register('owner_name')} />
                </div>

                <div className="space-y-2">
                  <Label>Notes</Label>
                  <textarea
                    placeholder="Additional notes..."
                    className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                    rows={2}
                    {...register('notes')}
                  />
                </div>

                <div className="flex justify-end gap-2 pt-2">
                  <Button type="button" variant="outline" onClick={() => { setShowAddDialog(false); reset(); }}>
                    Cancel
                  </Button>
                  <Button type="submit" disabled={addMutation.isPending}>
                    {addMutation.isPending ? (
                      <><Loader2 className="mr-1 h-4 w-4 animate-spin" />Registering...</>
                    ) : (
                      <><Plus className="mr-1 h-4 w-4" />Register Vehicle</>
                    )}
                  </Button>
                </div>
              </CardContent>
            </form>
          </Card>
        </div>
      )}

      {/* Edit Vehicle Dialog */}
      {editingVehicle && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <Card className="w-full max-w-lg mx-4">
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle>Edit Vehicle Details</CardTitle>
              <button onClick={() => setEditingVehicle(null)} className="text-slate-400 hover:text-slate-600">
                <X className="h-5 w-5" />
              </button>
            </CardHeader>
            <form onSubmit={handleSubmitEdit((data) => editMutation.mutate({ ...data, id: editingVehicle.id }))}>
              <CardContent className="space-y-4">
                {editMutation.isError && (
                  <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">
                    {(editMutation.error as any)?.response?.data?.detail || 'Failed to update vehicle'}
                  </div>
                )}

                <div className="space-y-2">
                  <Label>Plate Number *</Label>
                  <Input placeholder="ABC-1234" {...registerEdit('plate_number')} />
                  {editErrors.plate_number && <p className="text-xs text-red-500">{editErrors.plate_number.message}</p>}
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label>Make</Label>
                    <Input placeholder="Toyota" {...registerEdit('make')} />
                  </div>
                  <div className="space-y-2">
                    <Label>Model</Label>
                    <Input placeholder="Camry" {...registerEdit('model')} />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label>Color</Label>
                    <Input placeholder="White" {...registerEdit('color')} />
                  </div>
                  <div className="space-y-2">
                    <Label>Category *</Label>
                    <select
                      {...registerEdit('category')}
                      className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                    >
                      {categoryOptions.map((c) => (
                        <option key={c} value={c}>
                          {c.charAt(0).toUpperCase() + c.slice(1)}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                <div className="space-y-2">
                  <Label>Owner Name</Label>
                  <Input placeholder="John Doe" {...registerEdit('owner_name')} />
                </div>

                <div className="space-y-2">
                  <Label>Notes</Label>
                  <textarea
                    placeholder="Additional notes..."
                    className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                    rows={2}
                    {...registerEdit('notes')}
                  />
                </div>

                <div className="flex justify-end gap-2 pt-2">
                  <Button type="button" variant="outline" onClick={() => setEditingVehicle(null)}>
                    Cancel
                  </Button>
                  <Button type="submit" disabled={editMutation.isPending}>
                    {editMutation.isPending ? (
                      <><Loader2 className="mr-1 h-4 w-4 animate-spin" />Saving...</>
                    ) : (
                      <>Save Changes</>
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
