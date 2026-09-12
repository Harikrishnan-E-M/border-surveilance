'use client';

import { useState, useCallback, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { format, formatDistanceToNow } from 'date-fns';
import {
  Loader2,
  Users,
  Camera,
  Clock,
  Search,
  CheckCircle2,
  XCircle,
  GitMerge,
  Scissors,
  Activity,
  ArrowRight,
  RefreshCw,
  Eye,
  Route,
  BarChart3,
  AlertCircle,
  ImageIcon,
} from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Skeleton } from '@/components/ui/skeleton';
import { apiClient } from '@/lib/api-client';
import { cn, formatDuration } from '@/lib/utils';
import { useWebSocket } from '@/hooks/use-websocket';

// -------------------------------------------------------------------
// Types
// -------------------------------------------------------------------

interface ActivePerson {
  global_person_id: string;
  current_camera_id: string;
  current_camera_name: string;
  thumbnail_path: string | null;
  first_seen: string;
  last_seen: string;
  cameras_visited: number;
  total_duration_seconds: number;
}

interface JourneyEvent {
  camera_id: string;
  camera_name: string;
  timestamp: string;
  thumbnail_path: string | null;
  zone_id: string | null;
}

interface JourneyData {
  id: string;
  org_id: string;
  global_person_id: string;
  events: JourneyEvent[];
  first_camera_id: string | null;
  last_camera_id: string | null;
  first_seen: string | null;
  last_seen: string | null;
  total_cameras_visited: number;
  total_duration_seconds: number | null;
  tracks: TrackInfo[];
}

interface TrackInfo {
  id: string;
  camera_id: string;
  camera_name: string;
  track_id: number;
  first_seen: string | null;
  last_seen: string | null;
  thumbnail_path: string | null;
  is_active: boolean;
}

interface ReIDMatch {
  id: string;
  org_id: string;
  track_a_id: string;
  track_b_id: string;
  similarity_score: number;
  is_confirmed: boolean | null;
  confirmed_by: string | null;
  matched_at: string;
  created_at: string;
  track_a_camera_name: string | null;
  track_a_thumbnail: string | null;
  track_a_first_seen: string | null;
  track_b_camera_name: string | null;
  track_b_thumbnail: string | null;
  track_b_first_seen: string | null;
}

interface SearchResult {
  global_person_id: string;
  similarity: number;
  camera_id: string;
  camera_name: string;
  thumbnail_path: string | null;
  first_seen: string | null;
  last_seen: string | null;
  cameras_visited: number;
}

interface ReIDStats {
  total_persons: number;
  active_persons: number;
  total_tracks: number;
  total_matches: number;
  pending_review: number;
  avg_cameras_visited: number;
  most_traversed_path: string | null;
  peak_crossing_hour: string | null;
}

interface PaginatedResponse<T> {
  data: T;
  meta?: {
    page: number;
    page_size: number;
    total: number;
    total_pages: number;
  };
}

// -------------------------------------------------------------------
// Thumbnail component
// -------------------------------------------------------------------

function PersonThumbnail({
  path,
  size = 'md',
  className,
}: {
  path: string | null;
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}) {
  const sizeClasses = {
    sm: 'h-10 w-10',
    md: 'h-16 w-16',
    lg: 'h-24 w-24',
  };

  if (!path) {
    return (
      <div
        className={cn(
          'flex items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-800',
          sizeClasses[size],
          className
        )}
      >
        <ImageIcon className="h-1/2 w-1/2 text-slate-400" />
      </div>
    );
  }

  return (
    <div
      className={cn(
        'overflow-hidden rounded-lg bg-slate-100 dark:bg-slate-800',
        sizeClasses[size],
        className
      )}
    >
      <img
        src={`/api/v1/storage/${encodeURIComponent(path)}`}
        alt="Person"
        className="h-full w-full object-cover"
        onError={(e) => {
          (e.target as HTMLImageElement).style.display = 'none';
        }}
      />
    </div>
  );
}

// -------------------------------------------------------------------
// Similarity badge
// -------------------------------------------------------------------

function SimilarityBadge({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  let variant: 'default' | 'secondary' | 'destructive' | 'outline' = 'default';
  if (pct >= 80) variant = 'default';
  else if (pct >= 60) variant = 'secondary';
  else variant = 'outline';

  return <Badge variant={variant}>{pct}% match</Badge>;
}

// -------------------------------------------------------------------
// Active Persons Panel
// -------------------------------------------------------------------

function ActivePersonsPanel({
  onSelectPerson,
}: {
  onSelectPerson: (id: string) => void;
}) {
  const { data: activeData, isLoading } = useQuery<ActivePerson[]>({
    queryKey: ['reid-active-persons'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/reid/persons/active');
      return res.data?.data ?? res.data ?? [];
    },
    refetchInterval: 10000,
  });

  // WebSocket for real-time updates
  useWebSocket('/reid', { enabled: true });

  const persons = activeData ?? [];

  if (isLoading) {
    return (
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {Array.from({ length: 8 }).map((_, i) => (
          <Card key={i}>
            <CardContent className="flex items-center gap-3 p-4">
              <Skeleton className="h-16 w-16 rounded-lg" />
              <div className="flex-1 space-y-2">
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-3 w-32" />
                <Skeleton className="h-3 w-20" />
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  if (persons.length === 0) {
    return (
      <Card>
        <CardContent className="flex flex-col items-center justify-center py-12">
          <Users className="mb-3 h-12 w-12 text-slate-300 dark:text-slate-600" />
          <p className="text-sm text-slate-500">No active persons detected</p>
          <p className="text-xs text-slate-400">
            Persons will appear here when detected across cameras
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {persons.map((person) => (
        <Card
          key={person.global_person_id}
          className="cursor-pointer transition-shadow hover:shadow-md"
          onClick={() => onSelectPerson(person.global_person_id)}
        >
          <CardContent className="flex items-center gap-3 p-4">
            <PersonThumbnail path={person.thumbnail_path} size="md" />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5">
                <span className="relative flex h-2 w-2">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-400 opacity-75" />
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-green-500" />
                </span>
                <span className="truncate text-sm font-medium text-slate-900 dark:text-white">
                  {person.current_camera_name}
                </span>
              </div>
              <p className="mt-0.5 text-xs text-slate-500">
                {formatDistanceToNow(new Date(person.first_seen), {
                  addSuffix: false,
                })}{' '}
                tracked
              </p>
              <div className="mt-1 flex items-center gap-2">
                <Badge variant="outline" className="text-[10px]">
                  <Camera className="mr-1 h-2.5 w-2.5" />
                  {person.cameras_visited} camera{person.cameras_visited !== 1 ? 's' : ''}
                </Badge>
                {person.total_duration_seconds > 0 && (
                  <span className="text-[10px] text-slate-400">
                    {formatDuration(person.total_duration_seconds)}
                  </span>
                )}
              </div>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

// -------------------------------------------------------------------
// Person Journey View
// -------------------------------------------------------------------

function PersonJourneyView({
  globalPersonId,
  onClose,
}: {
  globalPersonId: string;
  onClose: () => void;
}) {
  const { data: journeyData, isLoading } = useQuery<JourneyData>({
    queryKey: ['reid-journey', globalPersonId],
    queryFn: async () => {
      const res = await apiClient.get(
        `/api/v1/reid/persons/${globalPersonId}/journey`
      );
      return res.data?.data ?? res.data;
    },
    enabled: !!globalPersonId,
  });

  if (isLoading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center py-16">
          <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
        </CardContent>
      </Card>
    );
  }

  if (!journeyData) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-sm text-slate-500">
          No journey data available for this person.
        </CardContent>
      </Card>
    );
  }

  const events = journeyData.events || [];

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-lg">Person Journey</CardTitle>
            <CardDescription>
              ID: {globalPersonId.slice(0, 8)}... --{' '}
              {journeyData.total_cameras_visited} camera
              {journeyData.total_cameras_visited !== 1 ? 's' : ''} visited
              {journeyData.total_duration_seconds
                ? ` -- ${formatDuration(journeyData.total_duration_seconds)} total`
                : ''}
            </CardDescription>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose}>
            Close
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {/* Horizontal Timeline */}
        {events.length > 0 ? (
          <div className="relative">
            <div className="overflow-x-auto pb-4">
              <div className="flex items-start gap-0" style={{ minWidth: events.length * 180 }}>
                {events.map((event, idx) => (
                  <div key={idx} className="flex items-start">
                    <div className="flex flex-col items-center" style={{ width: 160 }}>
                      <PersonThumbnail path={event.thumbnail_path} size="md" />
                      <div className="mt-2 flex h-8 w-8 items-center justify-center rounded-full bg-blue-100 text-xs font-bold text-blue-700 dark:bg-blue-900 dark:text-blue-300">
                        {idx + 1}
                      </div>
                      <p className="mt-1.5 text-center text-xs font-medium text-slate-900 dark:text-white">
                        {event.camera_name}
                      </p>
                      <p className="text-center text-[10px] text-slate-500">
                        {format(new Date(event.timestamp), 'HH:mm:ss')}
                      </p>
                      <p className="text-center text-[10px] text-slate-400">
                        {format(new Date(event.timestamp), 'MMM d')}
                      </p>
                    </div>
                    {idx < events.length - 1 && (
                      <div className="flex items-center self-center pt-4">
                        <div className="h-0.5 w-4 bg-slate-300 dark:bg-slate-600" />
                        <ArrowRight className="h-4 w-4 text-slate-400" />
                        <div className="h-0.5 w-4 bg-slate-300 dark:bg-slate-600" />
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <p className="py-4 text-center text-sm text-slate-500">
            No journey events recorded yet.
          </p>
        )}

        {/* Track details table */}
        {journeyData.tracks && journeyData.tracks.length > 0 && (
          <div className="mt-6">
            <h4 className="mb-2 text-sm font-medium text-slate-700 dark:text-slate-300">
              Track Segments
            </h4>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Camera</TableHead>
                  <TableHead>Track ID</TableHead>
                  <TableHead>First Seen</TableHead>
                  <TableHead>Last Seen</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {journeyData.tracks.map((track) => (
                  <TableRow key={track.id}>
                    <TableCell className="font-medium">{track.camera_name}</TableCell>
                    <TableCell>#{track.track_id}</TableCell>
                    <TableCell className="text-xs">
                      {track.first_seen
                        ? format(new Date(track.first_seen), 'MMM d, HH:mm:ss')
                        : '--'}
                    </TableCell>
                    <TableCell className="text-xs">
                      {track.last_seen
                        ? format(new Date(track.last_seen), 'MMM d, HH:mm:ss')
                        : '--'}
                    </TableCell>
                    <TableCell>
                      {track.is_active ? (
                        <Badge variant="default" className="bg-green-600 text-[10px]">
                          Active
                        </Badge>
                      ) : (
                        <Badge variant="secondary" className="text-[10px]">
                          Ended
                        </Badge>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// -------------------------------------------------------------------
// Search by Image
// -------------------------------------------------------------------

function SearchByImage() {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [threshold, setThreshold] = useState(0.4);
  const fileInputRef = useRef<HTMLInputElement>(null);


  const searchMutation = useMutation<
    { matches: SearchResult[]; total: number },
    Error,
    FormData
  >({
    mutationFn: async (formData: FormData) => {
      const res = await apiClient.post(
        `/api/v1/reid/search?threshold=${threshold}&top_k=20`,
        formData,
        { headers: { 'Content-Type': 'multipart/form-data' } }
      );
      return res.data?.data ?? res.data;
    },
  });

  const handleFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      setSelectedFile(file);
      const url = URL.createObjectURL(file);
      setPreviewUrl(url);
      searchMutation.reset();
    },
    [searchMutation]
  );

  const handleSearch = useCallback(() => {
    if (!selectedFile) return;
    const formData = new FormData();
    formData.append('image', selectedFile);
    searchMutation.mutate(formData);
  }, [selectedFile, searchMutation]);

  const results = searchMutation.data?.matches ?? [];

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="p-4">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-end">
            <div className="flex-1 space-y-1.5">
              <Label className="text-xs">Upload Person Image</Label>
              <div className="flex gap-2">
                <Input
                  ref={fileInputRef}
                  type="file"
                  accept="image/*"
                  onChange={handleFileSelect}
                  className="flex-1"
                />
              </div>
            </div>
            <div className="w-32 space-y-1.5">
              <Label className="text-xs">Min Similarity</Label>
              <Input
                type="number"
                min={0}
                max={1}
                step={0.05}
                value={threshold}
                onChange={(e) => setThreshold(parseFloat(e.target.value) || 0.4)}
              />
            </div>
            <Button
              onClick={handleSearch}
              disabled={!selectedFile || searchMutation.isPending}
            >
              {searchMutation.isPending ? (
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
              ) : (
                <Search className="mr-1.5 h-4 w-4" />
              )}
              Search
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Preview & Results */}
      <div className="grid gap-4 lg:grid-cols-4">
        {previewUrl && (
          <Card className="lg:col-span-1">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Query Image</CardTitle>
            </CardHeader>
            <CardContent>
              <img
                src={previewUrl}
                alt="Query"
                className="w-full rounded-lg object-cover"
              />
            </CardContent>
          </Card>
        )}

        <div className={previewUrl ? 'lg:col-span-3' : 'lg:col-span-4'}>
          {searchMutation.isPending && (
            <Card>
              <CardContent className="flex items-center justify-center py-12">
                <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
                <span className="ml-2 text-sm text-slate-500">
                  Searching across all cameras...
                </span>
              </CardContent>
            </Card>
          )}

          {searchMutation.isSuccess && results.length === 0 && (
            <Card>
              <CardContent className="flex flex-col items-center py-12">
                <AlertCircle className="mb-2 h-8 w-8 text-slate-400" />
                <p className="text-sm text-slate-500">
                  No matching persons found across cameras.
                </p>
              </CardContent>
            </Card>
          )}

          {results.length > 0 && (
            <div className="space-y-3">
              <p className="text-sm text-slate-600 dark:text-slate-400">
                Found {results.length} match{results.length !== 1 ? 'es' : ''} across
                cameras
              </p>
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {results.map((r) => (
                  <Card key={r.global_person_id}>
                    <CardContent className="flex items-center gap-3 p-4">
                      <PersonThumbnail path={r.thumbnail_path} size="md" />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <SimilarityBadge score={r.similarity} />
                        </div>
                        <p className="mt-1 text-sm font-medium">{r.camera_name}</p>
                        <p className="text-xs text-slate-500">
                          {r.cameras_visited} camera
                          {r.cameras_visited !== 1 ? 's' : ''} visited
                        </p>
                        {r.last_seen && (
                          <p className="text-[10px] text-slate-400">
                            Last seen:{' '}
                            {formatDistanceToNow(new Date(r.last_seen), {
                              addSuffix: true,
                            })}
                          </p>
                        )}
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// -------------------------------------------------------------------
// Match Review Panel
// -------------------------------------------------------------------

function MatchReviewPanel() {
  const [filterStatus, setFilterStatus] = useState<string>('pending');
  const [page, setPage] = useState(1);
  const queryClient = useQueryClient();

  const isConfirmedParam =
    filterStatus === 'pending'
      ? undefined
      : filterStatus === 'confirmed'
        ? true
        : false;

  const {
    data: matchResponse,
    isLoading,
    refetch,
  } = useQuery<PaginatedResponse<ReIDMatch[]>>({
    queryKey: ['reid-matches', filterStatus, page],
    queryFn: async () => {
      const params: Record<string, string | number | boolean> = {
        page,
        page_size: 10,
      };
      if (isConfirmedParam !== undefined) {
        params.is_confirmed = isConfirmedParam;
      }
      const res = await apiClient.get('/api/v1/reid/matches', { params });
      return res.data;
    },
  });

  const matches = matchResponse?.data ?? [];
  const meta = matchResponse?.meta;

  const confirmMutation = useMutation({
    mutationFn: async (matchId: string) => {
      await apiClient.post(`/api/v1/reid/matches/${matchId}/confirm`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['reid-matches'] });
      queryClient.invalidateQueries({ queryKey: ['reid-stats'] });
    },
  });

  const rejectMutation = useMutation({
    mutationFn: async (matchId: string) => {
      await apiClient.post(`/api/v1/reid/matches/${matchId}/reject`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['reid-matches'] });
      queryClient.invalidateQueries({ queryKey: ['reid-stats'] });
    },
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <Select value={filterStatus} onValueChange={(v) => { setFilterStatus(v); setPage(1); }}>
          <SelectTrigger className="w-40">
            <SelectValue placeholder="Filter status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="pending">Pending Review</SelectItem>
            <SelectItem value="confirmed">Confirmed</SelectItem>
            <SelectItem value="rejected">Rejected</SelectItem>
            <SelectItem value="all">All</SelectItem>
          </SelectContent>
        </Select>
        <Button variant="outline" size="sm" onClick={() => refetch()}>
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
          Refresh
        </Button>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Card key={i}>
              <CardContent className="flex items-center gap-4 p-4">
                <Skeleton className="h-20 w-20 rounded-lg" />
                <Skeleton className="h-6 w-16" />
                <Skeleton className="h-20 w-20 rounded-lg" />
                <div className="flex-1" />
                <Skeleton className="h-8 w-20" />
                <Skeleton className="h-8 w-20" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : matches.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center py-12">
            <CheckCircle2 className="mb-2 h-10 w-10 text-green-400" />
            <p className="text-sm text-slate-500">
              {filterStatus === 'pending'
                ? 'No matches pending review'
                : 'No matches found with current filters'}
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {matches.map((match) => (
            <Card key={match.id}>
              <CardContent className="p-4">
                <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
                  {/* Track A */}
                  <div className="flex items-center gap-3">
                    <PersonThumbnail path={match.track_a_thumbnail} size="lg" />
                    <div>
                      <p className="text-sm font-medium">
                        {match.track_a_camera_name ?? 'Camera A'}
                      </p>
                      <p className="text-xs text-slate-500">
                        {match.track_a_first_seen
                          ? format(new Date(match.track_a_first_seen), 'MMM d, HH:mm')
                          : '--'}
                      </p>
                    </div>
                  </div>

                  {/* Similarity */}
                  <div className="flex flex-col items-center gap-1">
                    <SimilarityBadge score={match.similarity_score} />
                    <ArrowRight className="h-4 w-4 text-slate-400 sm:rotate-0" />
                  </div>

                  {/* Track B */}
                  <div className="flex items-center gap-3">
                    <PersonThumbnail path={match.track_b_thumbnail} size="lg" />
                    <div>
                      <p className="text-sm font-medium">
                        {match.track_b_camera_name ?? 'Camera B'}
                      </p>
                      <p className="text-xs text-slate-500">
                        {match.track_b_first_seen
                          ? format(new Date(match.track_b_first_seen), 'MMM d, HH:mm')
                          : '--'}
                      </p>
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex flex-1 items-center justify-end gap-2">
                    {match.is_confirmed === null && (
                      <>
                        <Button
                          size="sm"
                          variant="default"
                          className="bg-green-600 hover:bg-green-700"
                          disabled={confirmMutation.isPending}
                          onClick={() => confirmMutation.mutate(match.id)}
                        >
                          <CheckCircle2 className="mr-1 h-3.5 w-3.5" />
                          Confirm
                        </Button>
                        <Button
                          size="sm"
                          variant="destructive"
                          disabled={rejectMutation.isPending}
                          onClick={() => rejectMutation.mutate(match.id)}
                        >
                          <XCircle className="mr-1 h-3.5 w-3.5" />
                          Reject
                        </Button>
                      </>
                    )}
                    {match.is_confirmed === true && (
                      <Badge className="bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200">
                        <CheckCircle2 className="mr-1 h-3 w-3" />
                        Confirmed
                      </Badge>
                    )}
                    {match.is_confirmed === false && (
                      <Badge variant="destructive">
                        <XCircle className="mr-1 h-3 w-3" />
                        Rejected
                      </Badge>
                    )}
                  </div>
                </div>
                <p className="mt-2 text-[10px] text-slate-400">
                  Matched {match.matched_at ? formatDistanceToNow(new Date(match.matched_at), { addSuffix: true }) : ''}
                </p>
              </CardContent>
            </Card>
          ))}

          {/* Pagination */}
          {meta && meta.total_pages > 1 && (
            <div className="flex items-center justify-center gap-2 pt-2">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                Previous
              </Button>
              <span className="text-sm text-slate-500">
                Page {meta.page} of {meta.total_pages}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= meta.total_pages}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// -------------------------------------------------------------------
// Merge / Split Controls
// -------------------------------------------------------------------

function MergeSplitPanel() {
  const queryClient = useQueryClient();

  // Merge state
  const [mergeTarget, setMergeTarget] = useState('');
  const [mergeSource, setMergeSource] = useState('');

  // Split state
  const [splitPerson, setSplitPerson] = useState('');
  const [splitTrackIds, setSplitTrackIds] = useState('');

  const mergeMutation = useMutation({
    mutationFn: async () => {
      await apiClient.post('/api/v1/reid/merge', {
        target_global_person_id: mergeTarget,
        source_global_person_id: mergeSource,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['reid'] });
      setMergeTarget('');
      setMergeSource('');
    },
  });

  const splitMutation = useMutation({
    mutationFn: async () => {
      const trackIds = splitTrackIds
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean);
      await apiClient.post('/api/v1/reid/split', {
        global_person_id: splitPerson,
        track_ids: trackIds,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['reid'] });
      setSplitPerson('');
      setSplitTrackIds('');
    },
  });

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      {/* Merge */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <GitMerge className="h-4 w-4" />
            Merge Identities
          </CardTitle>
          <CardDescription>
            Combine two person identities that belong to the same individual.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-1.5">
            <Label className="text-xs">Target Person ID (keep)</Label>
            <Input
              placeholder="UUID of identity to keep"
              value={mergeTarget}
              onChange={(e) => setMergeTarget(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">Source Person ID (dissolve)</Label>
            <Input
              placeholder="UUID of identity to merge in"
              value={mergeSource}
              onChange={(e) => setMergeSource(e.target.value)}
            />
          </div>
          <Button
            size="sm"
            disabled={!mergeTarget || !mergeSource || mergeMutation.isPending}
            onClick={() => mergeMutation.mutate()}
          >
            {mergeMutation.isPending ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <GitMerge className="mr-1.5 h-3.5 w-3.5" />
            )}
            Merge
          </Button>
          {mergeMutation.isSuccess && (
            <p className="text-xs text-green-600">Identities merged successfully.</p>
          )}
          {mergeMutation.isError && (
            <p className="text-xs text-red-600">
              Merge failed. Please check the IDs and try again.
            </p>
          )}
        </CardContent>
      </Card>

      {/* Split */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Scissors className="h-4 w-4" />
            Split Identity
          </CardTitle>
          <CardDescription>
            Separate incorrectly merged tracks into a new identity.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-1.5">
            <Label className="text-xs">Person ID</Label>
            <Input
              placeholder="UUID of identity to split from"
              value={splitPerson}
              onChange={(e) => setSplitPerson(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">Track IDs (comma-separated)</Label>
            <Input
              placeholder="track-uuid-1, track-uuid-2"
              value={splitTrackIds}
              onChange={(e) => setSplitTrackIds(e.target.value)}
            />
          </div>
          <Button
            size="sm"
            disabled={!splitPerson || !splitTrackIds || splitMutation.isPending}
            onClick={() => splitMutation.mutate()}
          >
            {splitMutation.isPending ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Scissors className="mr-1.5 h-3.5 w-3.5" />
            )}
            Split
          </Button>
          {splitMutation.isSuccess && (
            <p className="text-xs text-green-600">Identity split successfully.</p>
          )}
          {splitMutation.isError && (
            <p className="text-xs text-red-600">
              Split failed. Please check the IDs and try again.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// -------------------------------------------------------------------
// Statistics Panel
// -------------------------------------------------------------------

function StatsPanel({ stats }: { stats: ReIDStats | null }) {
  if (!stats) return null;

  const statCards = [
    {
      title: 'Unique Persons Today',
      value: stats.total_persons,
      icon: Users,
      color: 'bg-blue-50 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400',
    },
    {
      title: 'Active Now',
      value: stats.active_persons,
      icon: Activity,
      color: 'bg-green-50 text-green-600 dark:bg-green-900/30 dark:text-green-400',
    },
    {
      title: 'Avg Cameras Visited',
      value: stats.avg_cameras_visited.toFixed(1),
      icon: Camera,
      color: 'bg-purple-50 text-purple-600 dark:bg-purple-900/30 dark:text-purple-400',
    },
    {
      title: 'Pending Reviews',
      value: stats.pending_review,
      icon: Eye,
      color: 'bg-amber-50 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400',
    },
  ];

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {statCards.map((stat) => {
          const Icon = stat.icon;
          return (
            <Card key={stat.title}>
              <CardContent className="flex items-center gap-4 p-4">
                <div className={cn('flex h-10 w-10 items-center justify-center rounded-lg', stat.color)}>
                  <Icon className="h-5 w-5" />
                </div>
                <div>
                  <p className="text-2xl font-bold text-slate-900 dark:text-white">
                    {typeof stat.value === 'number' ? stat.value.toLocaleString() : stat.value}
                  </p>
                  <p className="text-xs text-slate-500">{stat.title}</p>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      {/* Additional stats */}
      <div className="grid gap-4 sm:grid-cols-2">
        <Card>
          <CardContent className="flex items-center gap-4 p-4">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600 dark:bg-indigo-900/30 dark:text-indigo-400">
              <Route className="h-5 w-5" />
            </div>
            <div>
              <p className="text-sm font-semibold text-slate-900 dark:text-white">
                {stats.most_traversed_path ?? 'N/A'}
              </p>
              <p className="text-xs text-slate-500">Most Traversed Path</p>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center gap-4 p-4">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-rose-50 text-rose-600 dark:bg-rose-900/30 dark:text-rose-400">
              <Clock className="h-5 w-5" />
            </div>
            <div>
              <p className="text-sm font-semibold text-slate-900 dark:text-white">
                {stats.peak_crossing_hour ?? 'N/A'}
              </p>
              <p className="text-xs text-slate-500">Peak Crossing Hour</p>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Summary table */}
      <Card>
        <CardContent className="p-4">
          <Table>
            <TableBody>
              <TableRow>
                <TableCell className="font-medium">Total Track Segments</TableCell>
                <TableCell className="text-right">{stats.total_tracks.toLocaleString()}</TableCell>
              </TableRow>
              <TableRow>
                <TableCell className="font-medium">Cross-Camera Matches</TableCell>
                <TableCell className="text-right">{stats.total_matches.toLocaleString()}</TableCell>
              </TableRow>
              <TableRow>
                <TableCell className="font-medium">Unique Persons</TableCell>
                <TableCell className="text-right">{stats.total_persons.toLocaleString()}</TableCell>
              </TableRow>
              <TableRow>
                <TableCell className="font-medium">Currently Active</TableCell>
                <TableCell className="text-right">{stats.active_persons.toLocaleString()}</TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}

// -------------------------------------------------------------------
// Main Page
// -------------------------------------------------------------------

export default function ReIDDashboardPage() {
  const [selectedPersonId, setSelectedPersonId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState('active');

  // Stats query
  const { data: statsResponse } = useQuery<{ data: ReIDStats }>({
    queryKey: ['reid-stats'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/reid/stats');
      return res.data;
    },
    refetchInterval: 30000,
  });

  const stats: ReIDStats | null = statsResponse?.data ?? null;

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">
            Cross-Camera Re-Identification
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Track persons across cameras, review matches, and analyse movement patterns
          </p>
        </div>
        {stats && (
          <div className="flex gap-3">
            <Badge variant="outline" className="gap-1.5 px-3 py-1.5">
              <Users className="h-3.5 w-3.5" />
              {stats.active_persons} active
            </Badge>
            <Badge variant="outline" className="gap-1.5 px-3 py-1.5">
              <Eye className="h-3.5 w-3.5" />
              {stats.pending_review} pending
            </Badge>
          </div>
        )}
      </div>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="grid w-full grid-cols-5">
          <TabsTrigger value="active" className="gap-1.5">
            <Activity className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Active Persons</span>
            <span className="sm:hidden">Active</span>
          </TabsTrigger>
          <TabsTrigger value="search" className="gap-1.5">
            <Search className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Search</span>
            <span className="sm:hidden">Search</span>
          </TabsTrigger>
          <TabsTrigger value="matches" className="gap-1.5">
            <Eye className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Match Review</span>
            <span className="sm:hidden">Review</span>
          </TabsTrigger>
          <TabsTrigger value="stats" className="gap-1.5">
            <BarChart3 className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Statistics</span>
            <span className="sm:hidden">Stats</span>
          </TabsTrigger>
          <TabsTrigger value="manage" className="gap-1.5">
            <GitMerge className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Merge/Split</span>
            <span className="sm:hidden">Manage</span>
          </TabsTrigger>
        </TabsList>

        {/* Active Persons Tab */}
        <TabsContent value="active" className="space-y-4">
          {selectedPersonId ? (
            <PersonJourneyView
              globalPersonId={selectedPersonId}
              onClose={() => setSelectedPersonId(null)}
            />
          ) : (
            <ActivePersonsPanel onSelectPerson={setSelectedPersonId} />
          )}
        </TabsContent>

        {/* Search Tab */}
        <TabsContent value="search">
          <SearchByImage />
        </TabsContent>

        {/* Match Review Tab */}
        <TabsContent value="matches">
          <MatchReviewPanel />
        </TabsContent>

        {/* Statistics Tab */}
        <TabsContent value="stats">
          <StatsPanel stats={stats} />
        </TabsContent>

        {/* Merge/Split Tab */}
        <TabsContent value="manage">
          <MergeSplitPanel />
        </TabsContent>
      </Tabs>
    </div>
  );
}
