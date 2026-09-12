'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Users,
  UserPlus,
  Search,
  LayoutGrid,
  List,
  Loader2,
  Trash2,
  Pencil,
  ScanFace,
} from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { apiClient } from '@/lib/api-client';

interface Person {
  id: string;
  name: string;
  group: string;
  thumbnail_url: string | null;
  face_count: number;
  last_seen: string | null;
  created_at: string;
}

export default function FacesPage() {
  const queryClient = useQueryClient();
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');
  const [searchQuery, setSearchQuery] = useState('');
  const [groupFilter, setGroupFilter] = useState('');

  const { data: personsData, isLoading } = useQuery<{ items: Person[]; total: number; groups: string[] }>({
    queryKey: ['persons', searchQuery, groupFilter],
    queryFn: async () => {
      const params: Record<string, any> = { limit: 200 };
      if (searchQuery) params.search = searchQuery;
      if (groupFilter) params.group = groupFilter;
      const res: any = await apiClient.get('/api/v1/faces/persons', { params });
      const itemsList = res?.items || res?.data?.items || (Array.isArray(res?.data) ? res.data : (Array.isArray(res) ? res : []));
      const items: Person[] = (itemsList || []).map((p: any) => ({
        id: p.id,
        name: p.name || p.full_name || 'Unknown',
        group: p.group || p.department || p.person_type || 'employee',
        thumbnail_url: p.thumbnail_url || null,
        face_count: p.face_count ?? p.enrollment_count ?? 0,
        last_seen: p.last_seen || null,
        created_at: p.created_at || new Date().toISOString(),
      }));
      return {
        items,
        total: res?.total || res?.data?.total || items.length,
        groups: res?.groups || res?.data?.groups || ['employees', 'visitors', 'vip', 'blacklisted'],
      };
    },
  });

  const persons = personsData?.items || [];
  const groups = personsData?.groups || [];

  const deleteMutation = useMutation({
    mutationFn: async (personId: string) => {
      await apiClient.delete(`/api/v1/faces/persons/${personId}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['persons'] });
    },
  });

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Face Registry</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {personsData?.total || 0} person{(personsData?.total || 0) !== 1 ? 's' : ''} enrolled
          </p>
        </div>
        <div className="flex gap-2">
          <Link href="/dashboard/faces/search">
            <Button variant="outline" size="sm">
              <ScanFace className="mr-1 h-4 w-4" />
              Face Search
            </Button>
          </Link>
          <Link href="/dashboard/faces/enroll">
            <Button size="sm">
              <UserPlus className="mr-1 h-4 w-4" />
              Enroll Person
            </Button>
          </Link>
        </div>
      </div>

      {/* Search and Filters */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <Input
            placeholder="Search by name..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-10"
          />
        </div>
        <select
          value={groupFilter}
          onChange={(e) => setGroupFilter(e.target.value)}
          className="rounded-md border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
        >
          <option value="">All groups</option>
          {groups.map((g) => (
            <option key={g} value={g}>{g}</option>
          ))}
        </select>
        <div className="flex rounded-lg border border-slate-200 dark:border-slate-700">
          <button
            onClick={() => setViewMode('grid')}
            className={`p-2 ${
              viewMode === 'grid' ? 'bg-blue-600 text-white' : 'bg-white text-slate-500 dark:bg-slate-800'
            } rounded-l-lg`}
          >
            <LayoutGrid className="h-4 w-4" />
          </button>
          <button
            onClick={() => setViewMode('list')}
            className={`p-2 ${
              viewMode === 'list' ? 'bg-blue-600 text-white' : 'bg-white text-slate-500 dark:bg-slate-800'
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
      ) : persons.length === 0 ? (
        <div className="flex h-64 flex-col items-center justify-center text-slate-400">
          <Users className="mb-3 h-10 w-10" />
          <p className="text-sm font-medium">
            {searchQuery ? 'No persons match your search' : 'No persons enrolled'}
          </p>
          {!searchQuery && (
            <Link href="/dashboard/faces/enroll">
              <Button variant="link" className="mt-1">
                Enroll your first person
              </Button>
            </Link>
          )}
        </div>
      ) : viewMode === 'grid' ? (
        <div className="grid gap-4 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {persons.map((person) => (
            <Card key={person.id} className="overflow-hidden transition-shadow hover:shadow-md">
              <div className="relative aspect-square bg-slate-100 dark:bg-slate-800">
                {person.thumbnail_url ? (
                  <img
                    src={person.thumbnail_url}
                    alt={person.name}
                    className="h-full w-full object-cover"
                  />
                ) : (
                  <div className="flex h-full w-full items-center justify-center">
                    <Users className="h-12 w-12 text-slate-300 dark:text-slate-600" />
                  </div>
                )}
                <div className="absolute right-2 top-2">
                  <Badge variant="secondary" className="text-xs">
                    {person.group}
                  </Badge>
                </div>
              </div>
              <CardContent className="p-3">
                <h3 className="font-semibold text-slate-900 dark:text-white">{person.name}</h3>
                <div className="mt-1 flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
                  <span>{person.face_count} face{person.face_count !== 1 ? 's' : ''}</span>
                  {person.last_seen && (
                    <span>
                      Last seen {new Date(person.last_seen).toLocaleDateString()}
                    </span>
                  )}
                </div>
                <div className="mt-2 flex gap-1">
                  <Link href={`/dashboard/faces/enroll?edit=${person.id}`} className="flex-1">
                    <Button variant="outline" size="sm" className="w-full">
                      <Pencil className="mr-1 h-3 w-3" />
                      Edit
                    </Button>
                  </Link>
                  <Button
                    variant="outline"
                    size="sm"
                    className="text-red-500 hover:text-red-700"
                    onClick={() => {
                      if (confirm(`Delete ${person.name}?`)) {
                        deleteMutation.mutate(person.id);
                      }
                    }}
                  >
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <Card>
          <CardContent className="p-0">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50/50 dark:border-slate-700 dark:bg-slate-800/50">
                  <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">Name</th>
                  <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">Group</th>
                  <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">Faces</th>
                  <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">Last Seen</th>
                  <th className="px-4 py-3 text-left font-medium text-slate-600 dark:text-slate-300">Enrolled</th>
                  <th className="px-4 py-3 text-right font-medium text-slate-600 dark:text-slate-300">Actions</th>
                </tr>
              </thead>
              <tbody>
                {persons.map((person) => (
                  <tr key={person.id} className="border-b border-slate-100 dark:border-slate-800 hover:bg-slate-50 dark:hover:bg-slate-800/50">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-3">
                        <div className="h-8 w-8 shrink-0 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-700">
                          {person.thumbnail_url ? (
                            <img src={person.thumbnail_url} alt="" className="h-full w-full object-cover" />
                          ) : (
                            <div className="flex h-full w-full items-center justify-center">
                              <Users className="h-4 w-4 text-slate-400" />
                            </div>
                          )}
                        </div>
                        <span className="font-medium text-slate-900 dark:text-white">{person.name}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant="outline" className="text-xs">{person.group}</Badge>
                    </td>
                    <td className="px-4 py-3 text-slate-600 dark:text-slate-400">{person.face_count}</td>
                    <td className="px-4 py-3 text-slate-500">
                      {person.last_seen ? new Date(person.last_seen).toLocaleDateString() : 'Never'}
                    </td>
                    <td className="px-4 py-3 text-slate-500">
                      {new Date(person.created_at).toLocaleDateString()}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex justify-end gap-1">
                        <Link href={`/dashboard/faces/enroll?edit=${person.id}`}>
                          <Button variant="ghost" size="sm"><Pencil className="h-3 w-3" /></Button>
                        </Link>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-red-500 hover:text-red-700"
                          onClick={() => {
                            if (confirm(`Delete ${person.name}?`)) deleteMutation.mutate(person.id);
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
    </div>
  );
}
