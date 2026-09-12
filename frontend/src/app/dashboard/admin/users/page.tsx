'use client';

import { useState, useEffect, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Users,
  Plus,
  Search,
  Filter,
  Loader2,
  MoreHorizontal,
  Pencil,
  UserX,
  UserCheck,
  ShieldCheck,
  Shield,
  Eye,
  UserCog,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  X,
  AlertCircle,
  CheckCircle2,
} from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { apiClient } from '@/lib/api-client';
import { cn, getInitials, formatDate } from '@/lib/utils';

interface User {
  id: string;
  name: string;
  email: string;
  role: 'super_admin' | 'org_admin' | 'operator' | 'viewer' | 'guard';
  status: 'active' | 'inactive' | 'suspended';
  last_login: string | null;
  created_at: string;
  avatar_url: string | null;
}

interface UsersResponse {
  items: User[];
  total: number;
  page: number;
  page_size: number;
}

interface UserFormData {
  name: string;
  email: string;
  role: string;
  password: string;
}

const ROLES = ['super_admin', 'org_admin', 'operator', 'viewer', 'guard'] as const;
const STATUSES = ['active', 'inactive', 'suspended'] as const;
const PAGE_SIZE_OPTIONS = [10, 20, 50, 100];

const roleConfig: Record<string, { label: string; color: string; icon: React.ElementType }> = {
  super_admin: {
    label: 'Super Admin',
    color: 'bg-red-100 text-red-700 border-red-200 dark:bg-red-900/30 dark:text-red-400 dark:border-red-800',
    icon: ShieldCheck,
  },
  org_admin: {
    label: 'Org Admin',
    color: 'bg-purple-100 text-purple-700 border-purple-200 dark:bg-purple-900/30 dark:text-purple-400 dark:border-purple-800',
    icon: Shield,
  },
  operator: {
    label: 'Operator',
    color: 'bg-blue-100 text-blue-700 border-blue-200 dark:bg-blue-900/30 dark:text-blue-400 dark:border-blue-800',
    icon: UserCog,
  },
  viewer: {
    label: 'Viewer',
    color: 'bg-green-100 text-green-700 border-green-200 dark:bg-green-900/30 dark:text-green-400 dark:border-green-800',
    icon: Eye,
  },
  guard: {
    label: 'Guard',
    color: 'bg-amber-100 text-amber-700 border-amber-200 dark:bg-amber-900/30 dark:text-amber-400 dark:border-amber-800',
    icon: Shield,
  },
};

const statusConfig: Record<string, { label: string; color: string }> = {
  active: {
    label: 'Active',
    color: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  },
  inactive: {
    label: 'Inactive',
    color: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400',
  },
  suspended: {
    label: 'Suspended',
    color: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  },
};

export default function UsersPage() {
  const queryClient = useQueryClient();

  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState<string>('all');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [showFilters, setShowFilters] = useState(false);
  const [selectedUsers, setSelectedUsers] = useState<Set<string>>(new Set());

  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [editingUser, setEditingUser] = useState<User | null>(null);

  const [formData, setFormData] = useState<UserFormData>({
    name: '',
    email: '',
    role: 'viewer',
    password: '',
  });
  const [formErrors, setFormErrors] = useState<Partial<Record<keyof UserFormData, string>>>({});

  // Debounce search input
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  const { data: usersData, isLoading, isFetching } = useQuery<UsersResponse>({
    queryKey: ['admin-users', debouncedSearch, roleFilter, statusFilter, page, pageSize],
    queryFn: async () => {
      const params: Record<string, string | number> = {
        page,
        page_size: pageSize,
      };
      if (debouncedSearch) params.search = debouncedSearch;
      if (roleFilter !== 'all') params.role = roleFilter;
      if (statusFilter !== 'all') params.status = statusFilter;

      const res = await apiClient.get('/api/v1/admin/users', { params });
      return res.data as UsersResponse;
    },
  });

  const users = usersData?.items || [];
  const totalUsers = usersData?.total || 0;
  const totalPages = Math.ceil(totalUsers / pageSize);

  const validateForm = useCallback((data: UserFormData, isEdit: boolean): boolean => {
    const errors: Partial<Record<keyof UserFormData, string>> = {};

    if (!data.name.trim()) errors.name = 'Name is required';
    if (!data.email.trim()) {
      errors.email = 'Email is required';
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(data.email)) {
      errors.email = 'Invalid email format';
    }
    if (!data.role) errors.role = 'Role is required';
    if (!isEdit && !data.password) {
      errors.password = 'Password is required';
    } else if (!isEdit && data.password.length < 8) {
      errors.password = 'Password must be at least 8 characters';
    }

    setFormErrors(errors);
    return Object.keys(errors).length === 0;
  }, []);

  const addUserMutation = useMutation({
    mutationFn: async (data: UserFormData) => {
      const res = await apiClient.post('/api/v1/admin/users', data);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] });
      setAddDialogOpen(false);
      resetForm();
    },
  });

  const editUserMutation = useMutation({
    mutationFn: async ({ id, data }: { id: string; data: Partial<UserFormData> }) => {
      const res = await apiClient.put(`/api/v1/admin/users/${id}`, data);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] });
      setEditDialogOpen(false);
      setEditingUser(null);
      resetForm();
    },
  });

  const toggleStatusMutation = useMutation({
    mutationFn: async ({ id, status }: { id: string; status: string }) => {
      const res = await apiClient.patch(`/api/v1/admin/users/${id}/status`, { status });
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] });
    },
  });

  const bulkDeactivateMutation = useMutation({
    mutationFn: async (userIds: string[]) => {
      await apiClient.post('/api/v1/admin/users/bulk-deactivate', { user_ids: userIds });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] });
      setSelectedUsers(new Set());
    },
  });

  const resetForm = () => {
    setFormData({ name: '', email: '', role: 'viewer', password: '' });
    setFormErrors({});
  };

  const openEditDialog = (user: User) => {
    setEditingUser(user);
    setFormData({
      name: user.name,
      email: user.email,
      role: user.role,
      password: '',
    });
    setFormErrors({});
    setEditDialogOpen(true);
  };

  const handleAddSubmit = () => {
    if (!validateForm(formData, false)) return;
    addUserMutation.mutate(formData);
  };

  const handleEditSubmit = () => {
    if (!editingUser || !validateForm(formData, true)) return;
    const updateData: Partial<UserFormData> = {
      name: formData.name,
      email: formData.email,
      role: formData.role,
    };
    if (formData.password) {
      updateData.password = formData.password;
    }
    editUserMutation.mutate({ id: editingUser.id, data: updateData });
  };

  const toggleUserSelection = (userId: string) => {
    setSelectedUsers((prev) => {
      const next = new Set(prev);
      if (next.has(userId)) next.delete(userId);
      else next.add(userId);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selectedUsers.size === users.length) {
      setSelectedUsers(new Set());
    } else {
      setSelectedUsers(new Set(users.map((u) => u.id)));
    }
  };

  const clearFilters = () => {
    setSearch('');
    setRoleFilter('all');
    setStatusFilter('all');
    setPage(1);
  };

  const hasActiveFilters = roleFilter !== 'all' || statusFilter !== 'all' || debouncedSearch !== '';

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">User Management</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {totalUsers} user{totalUsers !== 1 ? 's' : ''} total
          </p>
        </div>
        <Button onClick={() => { resetForm(); setAddDialogOpen(true); }}>
          <Plus className="mr-1 h-4 w-4" />
          Add User
        </Button>
      </div>

      {/* Search & Filters */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <Input
            placeholder="Search by name or email..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-10"
          />
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowFilters(!showFilters)}
          >
            <Filter className="mr-1 h-4 w-4" />
            Filters
            {hasActiveFilters && (
              <Badge className="ml-1 h-5 w-5 rounded-full p-0 text-xs" variant="destructive">
                !
              </Badge>
            )}
          </Button>
        </div>
      </div>

      {/* Filter Panel */}
      {showFilters && (
        <Card>
          <CardContent className="p-4">
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <div className="space-y-2">
                <Label className="text-xs font-medium">Role</Label>
                <Select value={roleFilter} onValueChange={(v) => { setRoleFilter(v); setPage(1); }}>
                  <SelectTrigger className="h-9">
                    <SelectValue placeholder="All roles" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All roles</SelectItem>
                    {ROLES.map((role) => (
                      <SelectItem key={role} value={role}>
                        {roleConfig[role]?.label || role}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label className="text-xs font-medium">Status</Label>
                <Select value={statusFilter} onValueChange={(v) => { setStatusFilter(v); setPage(1); }}>
                  <SelectTrigger className="h-9">
                    <SelectValue placeholder="All statuses" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All statuses</SelectItem>
                    {STATUSES.map((status) => (
                      <SelectItem key={status} value={status}>
                        {statusConfig[status]?.label || status}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="flex items-end">
                <Button variant="ghost" size="sm" onClick={clearFilters}>
                  <X className="mr-1 h-3 w-3" />
                  Clear filters
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Bulk Actions */}
      {selectedUsers.size > 0 && (
        <div className="flex items-center gap-3 rounded-lg border border-blue-200 bg-blue-50 px-4 py-2 dark:border-blue-800 dark:bg-blue-900/20">
          <span className="text-sm font-medium text-blue-700 dark:text-blue-400">
            {selectedUsers.size} user{selectedUsers.size !== 1 ? 's' : ''} selected
          </span>
          <Button
            size="sm"
            variant="outline"
            onClick={() => bulkDeactivateMutation.mutate(Array.from(selectedUsers))}
            disabled={bulkDeactivateMutation.isPending}
          >
            {bulkDeactivateMutation.isPending ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : (
              <UserX className="mr-1 h-3 w-3" />
            )}
            Deactivate Selected
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setSelectedUsers(new Set())}>
            Cancel
          </Button>
        </div>
      )}

      {/* Users Table */}
      <Card>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="flex h-64 items-center justify-center">
              <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
            </div>
          ) : users.length === 0 ? (
            <div className="flex h-64 flex-col items-center justify-center text-slate-400">
              <Users className="mb-3 h-10 w-10" />
              <p className="text-sm font-medium">No users found</p>
              <p className="mt-1 text-xs">
                {hasActiveFilters ? 'Try adjusting your filters' : 'Add your first user to get started'}
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow className="bg-slate-50/50 dark:bg-slate-800/50">
                    <TableHead className="w-10">
                      <input
                        type="checkbox"
                        checked={users.length > 0 && selectedUsers.size === users.length}
                        onChange={toggleSelectAll}
                        className="h-4 w-4 rounded border-slate-300 accent-primary"
                      />
                    </TableHead>
                    <TableHead>User</TableHead>
                    <TableHead>Role</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Last Login</TableHead>
                    <TableHead>Created</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {users.map((user) => {
                    const role = roleConfig[user.role];
                    const status = statusConfig[user.status];
                    const RoleIcon = role?.icon || Shield;

                    return (
                      <TableRow key={user.id} data-state={selectedUsers.has(user.id) ? 'selected' : undefined}>
                        <TableCell onClick={(e) => e.stopPropagation()}>
                          <input
                            type="checkbox"
                            checked={selectedUsers.has(user.id)}
                            onChange={() => toggleUserSelection(user.id)}
                            className="h-4 w-4 rounded border-slate-300 accent-primary"
                          />
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center gap-3">
                            <Avatar className="h-8 w-8">
                              <AvatarFallback className="bg-blue-100 text-xs font-semibold text-blue-700 dark:bg-blue-900 dark:text-blue-300">
                                {getInitials(user.name)}
                              </AvatarFallback>
                            </Avatar>
                            <div>
                              <p className="font-medium text-slate-900 dark:text-white">
                                {user.name}
                              </p>
                              <p className="text-xs text-slate-500 dark:text-slate-400">
                                {user.email}
                              </p>
                            </div>
                          </div>
                        </TableCell>
                        <TableCell>
                          <Badge className={cn('gap-1 border text-xs', role?.color)}>
                            <RoleIcon className="h-3 w-3" />
                            {role?.label || user.role}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <Badge className={cn('text-xs', status?.color)}>
                            {status?.label || user.status}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-sm text-slate-500 dark:text-slate-400">
                          {user.last_login
                            ? formatDate(user.last_login, 'MMM d, yyyy HH:mm')
                            : 'Never'}
                        </TableCell>
                        <TableCell className="text-sm text-slate-500 dark:text-slate-400">
                          {formatDate(user.created_at, 'MMM d, yyyy')}
                        </TableCell>
                        <TableCell className="text-right">
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <Button variant="ghost" size="icon" className="h-8 w-8">
                                <MoreHorizontal className="h-4 w-4" />
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                              <DropdownMenuItem onClick={() => openEditDialog(user)}>
                                <Pencil className="mr-2 h-4 w-4" />
                                Edit User
                              </DropdownMenuItem>
                              {user.status === 'active' ? (
                                <DropdownMenuItem
                                  onClick={() => toggleStatusMutation.mutate({ id: user.id, status: 'inactive' })}
                                  className="text-red-600 dark:text-red-400"
                                >
                                  <UserX className="mr-2 h-4 w-4" />
                                  Deactivate
                                </DropdownMenuItem>
                              ) : (
                                <DropdownMenuItem
                                  onClick={() => toggleStatusMutation.mutate({ id: user.id, status: 'active' })}
                                  className="text-green-600 dark:text-green-400"
                                >
                                  <UserCheck className="mr-2 h-4 w-4" />
                                  Activate
                                </DropdownMenuItem>
                              )}
                            </DropdownMenuContent>
                          </DropdownMenu>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}

          {/* Pagination */}
          {totalUsers > 0 && (
            <div className="flex items-center justify-between border-t border-slate-200 px-4 py-3 dark:border-slate-700">
              <div className="flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400">
                <span>Rows per page</span>
                <Select
                  value={String(pageSize)}
                  onValueChange={(val) => {
                    setPageSize(Number(val));
                    setPage(1);
                  }}
                >
                  <SelectTrigger className="h-8 w-[70px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {PAGE_SIZE_OPTIONS.map((size) => (
                      <SelectItem key={size} value={String(size)}>
                        {size}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <span>
                  {(page - 1) * pageSize + 1}-{Math.min(page * pageSize, totalUsers)} of {totalUsers}
                </span>
                {isFetching && <Loader2 className="h-3 w-3 animate-spin" />}
              </div>

              <div className="flex items-center gap-1">
                <Button variant="outline" size="icon" className="h-8 w-8" disabled={page <= 1} onClick={() => setPage(1)}>
                  <ChevronsLeft className="h-4 w-4" />
                </Button>
                <Button variant="outline" size="icon" className="h-8 w-8" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
                  <ChevronLeft className="h-4 w-4" />
                </Button>
                <span className="px-2 text-sm text-slate-500 dark:text-slate-400">
                  Page {page} of {totalPages}
                </span>
                <Button variant="outline" size="icon" className="h-8 w-8" disabled={page >= totalPages} onClick={() => setPage((p) => Math.min(totalPages, p + 1))}>
                  <ChevronRight className="h-4 w-4" />
                </Button>
                <Button variant="outline" size="icon" className="h-8 w-8" disabled={page >= totalPages} onClick={() => setPage(totalPages)}>
                  <ChevronsRight className="h-4 w-4" />
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Add User Dialog */}
      <Dialog open={addDialogOpen} onOpenChange={(open) => { setAddDialogOpen(open); if (!open) resetForm(); }}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Add New User</DialogTitle>
            <DialogDescription>
              Create a new user account with the specified role and permissions.
            </DialogDescription>
          </DialogHeader>

          {addUserMutation.isError && (
            <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">
              <AlertCircle className="h-4 w-4 shrink-0" />
              {(addUserMutation.error as any)?.message || 'Failed to create user'}
            </div>
          )}

          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="add-name">Full Name</Label>
              <Input
                id="add-name"
                placeholder="John Doe"
                value={formData.name}
                onChange={(e) => setFormData((prev) => ({ ...prev, name: e.target.value }))}
              />
              {formErrors.name && <p className="text-xs text-red-500">{formErrors.name}</p>}
            </div>

            <div className="space-y-2">
              <Label htmlFor="add-email">Email Address</Label>
              <Input
                id="add-email"
                type="email"
                placeholder="john@example.com"
                value={formData.email}
                onChange={(e) => setFormData((prev) => ({ ...prev, email: e.target.value }))}
              />
              {formErrors.email && <p className="text-xs text-red-500">{formErrors.email}</p>}
            </div>

            <div className="space-y-2">
              <Label htmlFor="add-role">Role</Label>
              <Select value={formData.role} onValueChange={(v) => setFormData((prev) => ({ ...prev, role: v }))}>
                <SelectTrigger id="add-role">
                  <SelectValue placeholder="Select a role" />
                </SelectTrigger>
                <SelectContent>
                  {ROLES.map((role) => (
                    <SelectItem key={role} value={role}>
                      {roleConfig[role]?.label || role}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {formErrors.role && <p className="text-xs text-red-500">{formErrors.role}</p>}
            </div>

            <div className="space-y-2">
              <Label htmlFor="add-password">Password</Label>
              <Input
                id="add-password"
                type="password"
                placeholder="Minimum 8 characters"
                value={formData.password}
                onChange={(e) => setFormData((prev) => ({ ...prev, password: e.target.value }))}
              />
              {formErrors.password && <p className="text-xs text-red-500">{formErrors.password}</p>}
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => { setAddDialogOpen(false); resetForm(); }}>
              Cancel
            </Button>
            <Button onClick={handleAddSubmit} disabled={addUserMutation.isPending}>
              {addUserMutation.isPending ? (
                <>
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                  Creating...
                </>
              ) : (
                <>
                  <Plus className="mr-1 h-4 w-4" />
                  Create User
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Edit User Dialog */}
      <Dialog open={editDialogOpen} onOpenChange={(open) => { setEditDialogOpen(open); if (!open) { setEditingUser(null); resetForm(); } }}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Edit User</DialogTitle>
            <DialogDescription>
              Update user details and permissions. Leave password empty to keep current password.
            </DialogDescription>
          </DialogHeader>

          {editUserMutation.isError && (
            <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">
              <AlertCircle className="h-4 w-4 shrink-0" />
              {(editUserMutation.error as any)?.message || 'Failed to update user'}
            </div>
          )}

          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="edit-name">Full Name</Label>
              <Input
                id="edit-name"
                placeholder="John Doe"
                value={formData.name}
                onChange={(e) => setFormData((prev) => ({ ...prev, name: e.target.value }))}
              />
              {formErrors.name && <p className="text-xs text-red-500">{formErrors.name}</p>}
            </div>

            <div className="space-y-2">
              <Label htmlFor="edit-email">Email Address</Label>
              <Input
                id="edit-email"
                type="email"
                placeholder="john@example.com"
                value={formData.email}
                onChange={(e) => setFormData((prev) => ({ ...prev, email: e.target.value }))}
              />
              {formErrors.email && <p className="text-xs text-red-500">{formErrors.email}</p>}
            </div>

            <div className="space-y-2">
              <Label htmlFor="edit-role">Role</Label>
              <Select value={formData.role} onValueChange={(v) => setFormData((prev) => ({ ...prev, role: v }))}>
                <SelectTrigger id="edit-role">
                  <SelectValue placeholder="Select a role" />
                </SelectTrigger>
                <SelectContent>
                  {ROLES.map((role) => (
                    <SelectItem key={role} value={role}>
                      {roleConfig[role]?.label || role}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {formErrors.role && <p className="text-xs text-red-500">{formErrors.role}</p>}
            </div>

            <div className="space-y-2">
              <Label htmlFor="edit-password">New Password (optional)</Label>
              <Input
                id="edit-password"
                type="password"
                placeholder="Leave empty to keep current"
                value={formData.password}
                onChange={(e) => setFormData((prev) => ({ ...prev, password: e.target.value }))}
              />
              {formErrors.password && <p className="text-xs text-red-500">{formErrors.password}</p>}
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => { setEditDialogOpen(false); setEditingUser(null); resetForm(); }}>
              Cancel
            </Button>
            <Button onClick={handleEditSubmit} disabled={editUserMutation.isPending}>
              {editUserMutation.isPending ? (
                <>
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                  Saving...
                </>
              ) : (
                <>
                  <CheckCircle2 className="mr-1 h-4 w-4" />
                  Save Changes
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
