'use client';

import { useState, useMemo, useCallback, useRef } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { format, subDays } from 'date-fns';
import {
  Search,
  Image as ImageIcon,
  Upload,
  X,
  Loader2,
  Clock,
  Camera,
  CalendarDays,
  SlidersHorizontal,
  History,
  HardDrive,
  Sparkles,
  Video,
  Tag,
  ChevronRight,
  RefreshCw,
  Mic,
  AlertCircle,
  CheckCircle2,
  Database,
} from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { apiClient } from '@/lib/api-client';
import { cn, formatDate, formatRelativeTime } from '@/lib/utils';

// -------------------------------------------------------------------
// Types
// -------------------------------------------------------------------

interface CameraItem {
  id: string;
  name: string;
  is_active: boolean;
}

interface SearchResultItem {
  frame_id: string;
  camera_id: string;
  camera_name: string;
  recording_id: string;
  frame_number: number;
  timestamp: string;
  similarity_score: number;
  thumbnail_url: string | null;
  detected_objects: string[];
  scene_description: string | null;
}

interface SearchResponseData {
  results: SearchResultItem[];
  query: string | null;
  total_matches: number;
  search_duration_ms: number;
}

interface SearchHistoryItemData {
  id: string;
  query: string | null;
  query_type: string;
  timestamp: string;
  result_count: number;
  search_duration_ms: number | null;
}

interface SearchHistoryData {
  queries: SearchHistoryItemData[];
  total: number;
}

interface IndexingCameraStatus {
  camera_id: string;
  camera_name: string;
  frames_indexed: number;
  recordings_indexed: number;
  last_indexed: string | null;
  is_indexing: boolean;
  coverage_hours: number;
}

interface IndexingStatusData {
  cameras: IndexingCameraStatus[];
  total_frames: number;
  total_cameras: number;
}

// -------------------------------------------------------------------
// Suggested Searches
// -------------------------------------------------------------------

const SUGGESTED_SEARCHES = [
  'person with red jacket',
  'delivery truck at gate',
  'crowd gathering near entrance',
  'person carrying a bag',
  'vehicle parked in restricted area',
  'person running',
  'open door at night',
  'group of people standing',
];

// -------------------------------------------------------------------
// Skeleton Loader
// -------------------------------------------------------------------

function ResultSkeleton() {
  return (
    <div className="animate-pulse rounded-lg border bg-card">
      <div className="aspect-video w-full rounded-t-lg bg-muted" />
      <div className="space-y-2 p-3">
        <div className="h-4 w-3/4 rounded bg-muted" />
        <div className="h-3 w-1/2 rounded bg-muted" />
        <div className="flex gap-1.5">
          <div className="h-5 w-12 rounded-full bg-muted" />
          <div className="h-5 w-16 rounded-full bg-muted" />
        </div>
      </div>
    </div>
  );
}

function SkeletonGrid() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {Array.from({ length: 8 }).map((_, i) => (
        <ResultSkeleton key={i} />
      ))}
    </div>
  );
}

// -------------------------------------------------------------------
// Similarity Badge
// -------------------------------------------------------------------

function SimilarityBadge({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  let colorClass = 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400';
  if (pct >= 80) {
    colorClass = 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400';
  } else if (pct >= 60) {
    colorClass = 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400';
  } else if (pct >= 40) {
    colorClass = 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400';
  }

  return (
    <span className={cn('inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold tabular-nums', colorClass)}>
      {pct}%
    </span>
  );
}

// -------------------------------------------------------------------
// Page Component
// -------------------------------------------------------------------

export default function VideoSearchPage() {
  // Search state
  const [searchMode, setSearchMode] = useState<'text' | 'image'>('text');
  const [searchQuery, setSearchQuery] = useState('');
  const [uploadedImage, setUploadedImage] = useState<File | null>(null);
  const [imagePreview, setImagePreview] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Filter state
  const [selectedCameras, setSelectedCameras] = useState<string[]>([]);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [showFilters, setShowFilters] = useState(false);

  // UI state
  const [showHistory, setShowHistory] = useState(false);
  const [showIndexing, setShowIndexing] = useState(false);

  // Results
  const [searchResults, setSearchResults] = useState<SearchResponseData | null>(null);
  const [lastSearchQuery, setLastSearchQuery] = useState<string | null>(null);

  // -------------------------------------------------------------------
  // Data Queries
  // -------------------------------------------------------------------

  const { data: cameras } = useQuery<CameraItem[]>({
    queryKey: ['cameras-list-search'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/cameras', {
        params: { page_size: 200 },
      });
      const data = res.data?.data ?? res.data?.results ?? res.data ?? [];
      return Array.isArray(data) ? data : [];
    },
  });

  const { data: historyData, refetch: refetchHistory } = useQuery<SearchHistoryData>({
    queryKey: ['search-history'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/search/history', {
        params: { limit: 30 },
      });
      return res.data?.data ?? res.data ?? { queries: [], total: 0 };
    },
    enabled: showHistory,
  });

  const { data: indexingData, refetch: refetchIndexing } = useQuery<IndexingStatusData>({
    queryKey: ['indexing-status'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/search/indexing-status');
      return res.data?.data ?? res.data ?? { cameras: [], total_frames: 0, total_cameras: 0 };
    },
    enabled: showIndexing,
  });

  // -------------------------------------------------------------------
  // Search Mutations
  // -------------------------------------------------------------------

  const textSearchMutation = useMutation({
    mutationFn: async (query: string) => {
      const payload: Record<string, unknown> = {
        query,
        top_k: 50,
        min_similarity: 0.15,
      };
      if (selectedCameras.length > 0) {
        payload.cameras = selectedCameras;
      }
      if (dateFrom) payload.date_from = new Date(dateFrom).toISOString();
      if (dateTo) payload.date_to = new Date(dateTo).toISOString();

      const res = await apiClient.post('/api/v1/search/text', payload);
      return (res.data?.data ?? res.data) as SearchResponseData;
    },
    onSuccess: (data) => {
      setSearchResults(data);
      setLastSearchQuery(searchQuery);
      refetchHistory();
    },
  });

  const imageSearchMutation = useMutation({
    mutationFn: async (file: File) => {
      const formData = new FormData();
      formData.append('file', file);
      if (selectedCameras.length > 0) {
        formData.append('cameras', selectedCameras.join(','));
      }
      if (dateFrom) formData.append('date_from', new Date(dateFrom).toISOString());
      if (dateTo) formData.append('date_to', new Date(dateTo).toISOString());
      formData.append('top_k', '50');
      formData.append('min_similarity', '0.15');

      const res = await apiClient.post('/api/v1/search/image', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      return (res.data?.data ?? res.data) as SearchResponseData;
    },
    onSuccess: (data) => {
      setSearchResults(data);
      setLastSearchQuery('[Image Search]');
      refetchHistory();
    },
  });

  const isSearching = textSearchMutation.isPending || imageSearchMutation.isPending;
  const searchError = textSearchMutation.error || imageSearchMutation.error;

  // -------------------------------------------------------------------
  // Handlers
  // -------------------------------------------------------------------

  const handleSearch = useCallback(() => {
    if (searchMode === 'text' && searchQuery.trim()) {
      textSearchMutation.mutate(searchQuery.trim());
    } else if (searchMode === 'image' && uploadedImage) {
      imageSearchMutation.mutate(uploadedImage);
    }
  }, [searchMode, searchQuery, uploadedImage, textSearchMutation, imageSearchMutation]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !isSearching) {
        handleSearch();
      }
    },
    [handleSearch, isSearching]
  );

  const handleSuggestionClick = useCallback(
    (suggestion: string) => {
      setSearchQuery(suggestion);
      setSearchMode('text');
      textSearchMutation.mutate(suggestion);
    },
    [textSearchMutation]
  );

  const handleHistoryClick = useCallback(
    (item: SearchHistoryItemData) => {
      if (item.query && item.query_type === 'text') {
        setSearchQuery(item.query);
        setSearchMode('text');
        textSearchMutation.mutate(item.query);
      }
    },
    [textSearchMutation]
  );

  const handleImageUpload = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setUploadedImage(file);
    const reader = new FileReader();
    reader.onload = (ev) => {
      setImagePreview(ev.target?.result as string);
    };
    reader.readAsDataURL(file);
  }, []);

  const clearImage = useCallback(() => {
    setUploadedImage(null);
    setImagePreview(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  }, []);

  const handleCameraToggle = useCallback((cameraId: string) => {
    setSelectedCameras((prev) =>
      prev.includes(cameraId)
        ? prev.filter((id) => id !== cameraId)
        : [...prev, cameraId]
    );
  }, []);

  const handleResultClick = useCallback((result: SearchResultItem) => {
    const recordingUrl = `/dashboard/recordings?id=${result.recording_id}&t=${result.frame_number}`;
    window.open(recordingUrl, '_blank');
  }, []);

  // -------------------------------------------------------------------
  // Derived data
  // -------------------------------------------------------------------

  const results = searchResults?.results ?? [];
  const hasResults = results.length > 0;
  const hasSearched = searchResults !== null;

  const history = historyData?.queries ?? [];
  const indexingCameras = indexingData?.cameras ?? [];

  // -------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">
            Video Search
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Search across all recorded video using natural language or images
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant={showHistory ? 'default' : 'outline'}
            size="sm"
            onClick={() => {
              setShowHistory(!showHistory);
              setShowIndexing(false);
            }}
          >
            <History className="mr-1.5 h-4 w-4" />
            History
          </Button>
          <Button
            variant={showIndexing ? 'default' : 'outline'}
            size="sm"
            onClick={() => {
              setShowIndexing(!showIndexing);
              setShowHistory(false);
            }}
          >
            <Database className="mr-1.5 h-4 w-4" />
            Index Status
          </Button>
        </div>
      </div>

      {/* Search Bar */}
      <Card className="overflow-hidden">
        <CardContent className="p-4">
          {/* Mode Toggle */}
          <Tabs
            value={searchMode}
            onValueChange={(v) => setSearchMode(v as 'text' | 'image')}
            className="mb-4"
          >
            <TabsList className="grid w-full max-w-xs grid-cols-2">
              <TabsTrigger value="text" className="gap-1.5">
                <Search className="h-3.5 w-3.5" />
                Text Search
              </TabsTrigger>
              <TabsTrigger value="image" className="gap-1.5">
                <ImageIcon className="h-3.5 w-3.5" />
                Image Search
              </TabsTrigger>
            </TabsList>

            {/* Text Search Input */}
            <TabsContent value="text" className="mt-3">
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <Search className="absolute left-3 top-1/2 h-4.5 w-4.5 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    type="text"
                    placeholder="Search videos... e.g. 'person with red jacket near entrance'"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    onKeyDown={handleKeyDown}
                    className="pl-10 pr-10 text-base h-12"
                    autoFocus
                  />
                  <Mic className="absolute right-3 top-1/2 h-4.5 w-4.5 -translate-y-1/2 text-muted-foreground cursor-pointer hover:text-foreground transition-colors" />
                </div>
                <Button
                  onClick={handleSearch}
                  disabled={isSearching || !searchQuery.trim()}
                  className="h-12 px-6"
                >
                  {isSearching ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <Search className="mr-2 h-4 w-4" />
                  )}
                  Search
                </Button>
                <Button
                  variant="outline"
                  size="icon"
                  className="h-12 w-12"
                  onClick={() => setShowFilters(!showFilters)}
                >
                  <SlidersHorizontal className="h-4 w-4" />
                </Button>
              </div>
            </TabsContent>

            {/* Image Search Upload */}
            <TabsContent value="image" className="mt-3">
              <div className="flex gap-2">
                <div className="relative flex-1">
                  {imagePreview ? (
                    <div className="flex items-center gap-3 rounded-lg border border-input bg-background px-3 py-2 h-12">
                      <img
                        src={imagePreview}
                        alt="Query"
                        className="h-8 w-8 rounded object-cover"
                      />
                      <span className="flex-1 truncate text-sm">
                        {uploadedImage?.name}
                      </span>
                      <button
                        onClick={clearImage}
                        className="rounded-full p-1 hover:bg-muted"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ) : (
                    <label className="flex h-12 cursor-pointer items-center gap-2 rounded-lg border border-dashed border-input bg-background px-4 hover:border-primary hover:bg-muted/50 transition-colors">
                      <Upload className="h-4.5 w-4.5 text-muted-foreground" />
                      <span className="text-sm text-muted-foreground">
                        Click to upload a reference image (JPEG, PNG)
                      </span>
                      <input
                        ref={fileInputRef}
                        type="file"
                        accept="image/jpeg,image/png,image/webp"
                        onChange={handleImageUpload}
                        className="hidden"
                      />
                    </label>
                  )}
                </div>
                <Button
                  onClick={handleSearch}
                  disabled={isSearching || !uploadedImage}
                  className="h-12 px-6"
                >
                  {isSearching ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <Search className="mr-2 h-4 w-4" />
                  )}
                  Search
                </Button>
                <Button
                  variant="outline"
                  size="icon"
                  className="h-12 w-12"
                  onClick={() => setShowFilters(!showFilters)}
                >
                  <SlidersHorizontal className="h-4 w-4" />
                </Button>
              </div>
            </TabsContent>
          </Tabs>

          {/* Filters (collapsible) */}
          {showFilters && (
            <div className="mt-4 rounded-lg border bg-muted/30 p-4">
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {/* Camera Multi-select */}
                <div className="space-y-1.5">
                  <Label className="text-xs font-medium">Cameras</Label>
                  <div className="max-h-40 space-y-1 overflow-y-auto rounded-md border bg-background p-2">
                    {(cameras ?? []).length === 0 ? (
                      <p className="py-2 text-center text-xs text-muted-foreground">
                        No cameras available
                      </p>
                    ) : (
                      (cameras ?? []).map((cam) => (
                        <label
                          key={cam.id}
                          className="flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1 hover:bg-muted"
                        >
                          <input
                            type="checkbox"
                            checked={selectedCameras.includes(cam.id)}
                            onChange={() => handleCameraToggle(cam.id)}
                            className="h-3.5 w-3.5 rounded border-input"
                          />
                          <Camera className="h-3 w-3 text-muted-foreground" />
                          <span className="text-xs">{cam.name}</span>
                        </label>
                      ))
                    )}
                  </div>
                  {selectedCameras.length > 0 && (
                    <button
                      onClick={() => setSelectedCameras([])}
                      className="text-xs text-primary hover:underline"
                    >
                      Clear selection ({selectedCameras.length})
                    </button>
                  )}
                </div>

                {/* Date From */}
                <div className="space-y-1.5">
                  <Label className="text-xs font-medium">Date From</Label>
                  <div className="relative">
                    <CalendarDays className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      type="date"
                      value={dateFrom}
                      onChange={(e) => setDateFrom(e.target.value)}
                      className="pl-9"
                    />
                  </div>
                </div>

                {/* Date To */}
                <div className="space-y-1.5">
                  <Label className="text-xs font-medium">Date To</Label>
                  <div className="relative">
                    <CalendarDays className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      type="date"
                      value={dateTo}
                      onChange={(e) => setDateTo(e.target.value)}
                      className="pl-9"
                    />
                  </div>
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Sidebar Panels */}
      <div className="flex flex-col gap-6 lg:flex-row">
        {/* Main Content Area */}
        <div className="flex-1 space-y-4">
          {/* Search Error */}
          {searchError && (
            <Card className="border-red-200 bg-red-50 dark:border-red-800 dark:bg-red-950/30">
              <CardContent className="flex items-center gap-3 p-4">
                <AlertCircle className="h-5 w-5 text-red-500" />
                <div className="flex-1">
                  <p className="text-sm font-medium text-red-700 dark:text-red-400">
                    Search failed
                  </p>
                  <p className="text-xs text-red-600 dark:text-red-500">
                    {(searchError as { message?: string })?.message ?? 'An unexpected error occurred.'}
                  </p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleSearch}
                >
                  Retry
                </Button>
              </CardContent>
            </Card>
          )}

          {/* Loading Skeletons */}
          {isSearching && <SkeletonGrid />}

          {/* Results Grid */}
          {!isSearching && hasResults && (
            <>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-300">
                    {searchResults!.total_matches} result{searchResults!.total_matches !== 1 ? 's' : ''}
                  </h2>
                  {lastSearchQuery && (
                    <span className="text-xs text-muted-foreground">
                      for &ldquo;{lastSearchQuery}&rdquo;
                    </span>
                  )}
                </div>
                <Badge variant="secondary" className="tabular-nums text-xs">
                  {searchResults!.search_duration_ms.toFixed(0)}ms
                </Badge>
              </div>

              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {results.map((result) => (
                  <div
                    key={result.frame_id}
                    className="group cursor-pointer rounded-lg border bg-card transition-all hover:shadow-lg hover:border-primary/50"
                    onClick={() => handleResultClick(result)}
                  >
                    {/* Thumbnail */}
                    <div className="relative aspect-video w-full overflow-hidden rounded-t-lg bg-muted">
                      {result.thumbnail_url ? (
                        <img
                          src={result.thumbnail_url}
                          alt={`Frame from ${result.camera_name}`}
                          className="h-full w-full object-cover transition-transform group-hover:scale-105"
                          loading="lazy"
                        />
                      ) : (
                        <div className="flex h-full items-center justify-center">
                          <Video className="h-8 w-8 text-muted-foreground/50" />
                        </div>
                      )}
                      {/* Similarity badge overlay */}
                      <div className="absolute right-2 top-2">
                        <SimilarityBadge score={result.similarity_score} />
                      </div>
                      {/* Play icon on hover */}
                      <div className="absolute inset-0 flex items-center justify-center bg-black/0 transition-colors group-hover:bg-black/20">
                        <ChevronRight className="h-8 w-8 text-white opacity-0 transition-opacity group-hover:opacity-100" />
                      </div>
                    </div>

                    {/* Info */}
                    <div className="space-y-1.5 p-3">
                      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                        <Camera className="h-3 w-3" />
                        <span className="font-medium text-foreground">{result.camera_name}</span>
                      </div>
                      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                        <Clock className="h-3 w-3" />
                        <span>{formatDate(result.timestamp, 'MMM d, yyyy HH:mm:ss')}</span>
                      </div>
                      {/* Detected objects tags */}
                      {result.detected_objects.length > 0 && (
                        <div className="flex flex-wrap gap-1 pt-0.5">
                          {result.detected_objects.slice(0, 4).map((obj) => (
                            <Badge
                              key={obj}
                              variant="secondary"
                              className="text-[10px] px-1.5 py-0"
                            >
                              {obj}
                            </Badge>
                          ))}
                          {result.detected_objects.length > 4 && (
                            <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
                              +{result.detected_objects.length - 4}
                            </Badge>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}

          {/* Empty state after search */}
          {!isSearching && hasSearched && !hasResults && (
            <Card>
              <CardContent className="flex flex-col items-center justify-center py-16">
                <Search className="mb-4 h-12 w-12 text-muted-foreground/40" />
                <h3 className="text-lg font-semibold text-slate-700 dark:text-slate-300">
                  No matching frames found
                </h3>
                <p className="mt-1 max-w-sm text-center text-sm text-muted-foreground">
                  Try different search terms, adjust your filters, or ensure the cameras
                  have been indexed.
                </p>
                <div className="mt-4 flex gap-2">
                  <Button variant="outline" size="sm" onClick={() => setShowFilters(true)}>
                    <SlidersHorizontal className="mr-1.5 h-3.5 w-3.5" />
                    Adjust Filters
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => setShowIndexing(true)}>
                    <Database className="mr-1.5 h-3.5 w-3.5" />
                    Check Index Status
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Welcome / Empty state before first search */}
          {!isSearching && !hasSearched && (
            <div className="space-y-6">
              {/* Suggested Searches */}
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Sparkles className="h-4 w-4 text-amber-500" />
                    Suggested Searches
                  </CardTitle>
                  <CardDescription>
                    Click a suggestion or type your own query above
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="flex flex-wrap gap-2">
                    {SUGGESTED_SEARCHES.map((suggestion) => (
                      <button
                        key={suggestion}
                        onClick={() => handleSuggestionClick(suggestion)}
                        className="rounded-full border bg-background px-3 py-1.5 text-sm transition-colors hover:bg-primary hover:text-primary-foreground hover:border-primary"
                      >
                        {suggestion}
                      </button>
                    ))}
                  </div>
                </CardContent>
              </Card>

              {/* Search Tips */}
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-base">Search Tips</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div className="flex items-start gap-3 rounded-lg border p-3">
                      <div className="rounded-lg bg-blue-50 p-2 dark:bg-blue-900/30">
                        <Search className="h-4 w-4 text-blue-600 dark:text-blue-400" />
                      </div>
                      <div>
                        <p className="text-sm font-medium">Be Descriptive</p>
                        <p className="text-xs text-muted-foreground">
                          Describe what you see: &ldquo;person wearing blue shirt&rdquo; works better than just &ldquo;person&rdquo;
                        </p>
                      </div>
                    </div>
                    <div className="flex items-start gap-3 rounded-lg border p-3">
                      <div className="rounded-lg bg-green-50 p-2 dark:bg-green-900/30">
                        <Camera className="h-4 w-4 text-green-600 dark:text-green-400" />
                      </div>
                      <div>
                        <p className="text-sm font-medium">Filter by Camera</p>
                        <p className="text-xs text-muted-foreground">
                          Narrow results by selecting specific cameras in the filters panel
                        </p>
                      </div>
                    </div>
                    <div className="flex items-start gap-3 rounded-lg border p-3">
                      <div className="rounded-lg bg-purple-50 p-2 dark:bg-purple-900/30">
                        <ImageIcon className="h-4 w-4 text-purple-600 dark:text-purple-400" />
                      </div>
                      <div>
                        <p className="text-sm font-medium">Image Search</p>
                        <p className="text-xs text-muted-foreground">
                          Upload a reference photo to find visually similar frames across all cameras
                        </p>
                      </div>
                    </div>
                    <div className="flex items-start gap-3 rounded-lg border p-3">
                      <div className="rounded-lg bg-amber-50 p-2 dark:bg-amber-900/30">
                        <CalendarDays className="h-4 w-4 text-amber-600 dark:text-amber-400" />
                      </div>
                      <div>
                        <p className="text-sm font-medium">Date Range</p>
                        <p className="text-xs text-muted-foreground">
                          Limit your search to a specific time window using the date filters
                        </p>
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </div>
          )}
        </div>

        {/* History Sidebar */}
        {showHistory && (
          <div className="w-full lg:w-80 shrink-0">
            <Card>
              <CardHeader className="flex flex-row items-center justify-between pb-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <History className="h-4 w-4" />
                  Search History
                </CardTitle>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  onClick={() => setShowHistory(false)}
                >
                  <X className="h-3.5 w-3.5" />
                </Button>
              </CardHeader>
              <CardContent>
                {history.length === 0 ? (
                  <div className="flex flex-col items-center py-8 text-center">
                    <History className="mb-2 h-8 w-8 text-muted-foreground/40" />
                    <p className="text-sm text-muted-foreground">No search history yet</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Your searches will appear here
                    </p>
                  </div>
                ) : (
                  <div className="space-y-1 max-h-[500px] overflow-y-auto">
                    {history.map((item) => (
                      <button
                        key={item.id}
                        onClick={() => handleHistoryClick(item)}
                        className="flex w-full items-start gap-2 rounded-md px-2 py-2 text-left hover:bg-muted transition-colors"
                      >
                        {item.query_type === 'image' ? (
                          <ImageIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                        ) : (
                          <Search className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                        )}
                        <div className="flex-1 min-w-0">
                          <p className="truncate text-sm">
                            {item.query || '[Image search]'}
                          </p>
                          <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                            <span>{formatRelativeTime(item.timestamp)}</span>
                            <span>&middot;</span>
                            <span>{item.result_count} results</span>
                          </div>
                        </div>
                      </button>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        )}

        {/* Indexing Status Sidebar */}
        {showIndexing && (
          <div className="w-full lg:w-80 shrink-0">
            <Card>
              <CardHeader className="flex flex-row items-center justify-between pb-3">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Database className="h-4 w-4" />
                  Index Status
                </CardTitle>
                <div className="flex gap-1">
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7"
                    onClick={() => refetchIndexing()}
                  >
                    <RefreshCw className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7"
                    onClick={() => setShowIndexing(false)}
                  >
                    <X className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </CardHeader>
              <CardContent>
                {/* Summary */}
                {indexingData && (
                  <div className="mb-3 flex gap-3">
                    <div className="flex-1 rounded-lg bg-muted/50 p-2 text-center">
                      <p className="text-lg font-bold tabular-nums">
                        {indexingData.total_frames.toLocaleString()}
                      </p>
                      <p className="text-[10px] text-muted-foreground">Total Frames</p>
                    </div>
                    <div className="flex-1 rounded-lg bg-muted/50 p-2 text-center">
                      <p className="text-lg font-bold tabular-nums">
                        {indexingData.total_cameras}
                      </p>
                      <p className="text-[10px] text-muted-foreground">Cameras</p>
                    </div>
                  </div>
                )}

                {/* Per-camera status */}
                <div className="space-y-2 max-h-[400px] overflow-y-auto">
                  {indexingCameras.length === 0 ? (
                    <div className="flex flex-col items-center py-8 text-center">
                      <HardDrive className="mb-2 h-8 w-8 text-muted-foreground/40" />
                      <p className="text-sm text-muted-foreground">No indexing data</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        Index recordings to enable search
                      </p>
                    </div>
                  ) : (
                    indexingCameras.map((cam) => (
                      <div
                        key={cam.camera_id}
                        className="flex items-center gap-2 rounded-md border p-2"
                      >
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-1.5">
                            {cam.is_indexing ? (
                              <Loader2 className="h-3 w-3 animate-spin text-blue-500" />
                            ) : cam.frames_indexed > 0 ? (
                              <CheckCircle2 className="h-3 w-3 text-green-500" />
                            ) : (
                              <AlertCircle className="h-3 w-3 text-muted-foreground" />
                            )}
                            <span className="truncate text-xs font-medium">
                              {cam.camera_name}
                            </span>
                          </div>
                          <div className="mt-0.5 flex items-center gap-2 text-[10px] text-muted-foreground">
                            <span>{cam.frames_indexed.toLocaleString()} frames</span>
                            {cam.coverage_hours > 0 && (
                              <>
                                <span>&middot;</span>
                                <span>{cam.coverage_hours}h coverage</span>
                              </>
                            )}
                          </div>
                          {cam.last_indexed && (
                            <p className="text-[10px] text-muted-foreground">
                              Last: {formatRelativeTime(cam.last_indexed)}
                            </p>
                          )}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}
