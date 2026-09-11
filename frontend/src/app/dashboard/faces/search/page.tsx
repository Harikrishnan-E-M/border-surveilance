'use client';

import { useState, useRef, useCallback } from 'react';
import Link from 'next/link';
import { useMutation } from '@tanstack/react-query';
import {
  ArrowLeft,
  Upload,
  ScanFace,
  Loader2,
  Camera,
  Clock,
  MapPin,
  Percent,
  Image as ImageIcon,
  X,
  Search,
} from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { apiClient } from '@/lib/api-client';

interface SearchResult {
  person_id: string;
  person_name: string;
  group: string;
  confidence: number;
  thumbnail_url: string | null;
  sightings: {
    camera_name: string;
    location: string;
    timestamp: string;
    snapshot_url: string | null;
  }[];
}

export default function FaceSearchPage() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedImage, setSelectedImage] = useState<{ file: File; preview: string } | null>(null);
  const [threshold, setThreshold] = useState(0.7);
  const [maxResults, setMaxResults] = useState(10);

  const searchMutation = useMutation({
    mutationFn: async () => {
      if (!selectedImage) throw new Error('No image selected');
      const formData = new FormData();
      formData.append('file', selectedImage.file);
      formData.append('threshold', threshold.toString());
      formData.append('max_results', maxResults.toString());
      const res = await apiClient.post('/api/v1/faces/search', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      return res.data as SearchResult[];
    },
  });

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      if (selectedImage) URL.revokeObjectURL(selectedImage.preview);
      setSelectedImage({
        file,
        preview: URL.createObjectURL(file),
      });
      searchMutation.reset();
    }
    if (fileInputRef.current) fileInputRef.current.value = '';
  }, [selectedImage, searchMutation]);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const file = Array.from(e.dataTransfer.files).find((f) =>
        f.type.startsWith('image/')
      );
      if (file) {
        if (selectedImage) URL.revokeObjectURL(selectedImage.preview);
        setSelectedImage({
          file,
          preview: URL.createObjectURL(file),
        });
        searchMutation.reset();
      }
    },
    [selectedImage, searchMutation]
  );

  const clearImage = () => {
    if (selectedImage) URL.revokeObjectURL(selectedImage.preview);
    setSelectedImage(null);
    searchMutation.reset();
  };

  const results = searchMutation.data || [];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Link href="/dashboard/faces">
          <Button variant="ghost" size="sm">
            <ArrowLeft className="h-4 w-4" />
          </Button>
        </Link>
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Face Search</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Upload an image to search for matching persons in the database
          </p>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Upload + Controls */}
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Search Image</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {selectedImage ? (
                <div className="relative">
                  <img
                    src={selectedImage.preview}
                    alt="Search"
                    className="w-full rounded-lg border border-slate-200 dark:border-slate-700"
                  />
                  <button
                    onClick={clearImage}
                    className="absolute right-2 top-2 rounded-full bg-black/50 p-1 text-white hover:bg-black/70"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
              ) : (
                <div
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={handleDrop}
                  onClick={() => fileInputRef.current?.click()}
                  className="flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-slate-300 bg-slate-50 p-10 transition-colors hover:border-blue-400 hover:bg-blue-50/50 dark:border-slate-600 dark:bg-slate-800 dark:hover:border-blue-500"
                >
                  <Upload className="mb-2 h-10 w-10 text-slate-400" />
                  <p className="text-sm font-medium text-slate-600 dark:text-slate-300">
                    Drop an image or click to browse
                  </p>
                  <p className="mt-1 text-xs text-slate-400">JPG, PNG, WebP</p>
                </div>
              )}
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                onChange={handleFileSelect}
                className="hidden"
              />

              <div className="space-y-3">
                <div className="space-y-2">
                  <Label className="text-xs">Confidence Threshold: {Math.round(threshold * 100)}%</Label>
                  <input
                    type="range"
                    min="0.3"
                    max="0.99"
                    step="0.01"
                    value={threshold}
                    onChange={(e) => setThreshold(parseFloat(e.target.value))}
                    className="w-full accent-blue-600"
                  />
                </div>
                <div className="space-y-2">
                  <Label className="text-xs">Max Results</Label>
                  <Input
                    type="number"
                    min={1}
                    max={50}
                    value={maxResults}
                    onChange={(e) => setMaxResults(parseInt(e.target.value) || 10)}
                    className="text-sm"
                  />
                </div>
              </div>

              <Button
                className="w-full"
                onClick={() => searchMutation.mutate()}
                disabled={!selectedImage || searchMutation.isPending}
              >
                {searchMutation.isPending ? (
                  <>
                    <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                    Searching...
                  </>
                ) : (
                  <>
                    <ScanFace className="mr-1 h-4 w-4" />
                    Search Faces
                  </>
                )}
              </Button>

              {searchMutation.isError && (
                <p className="text-xs text-red-500">
                  {(searchMutation.error as any)?.response?.data?.detail || 'Search failed'}
                </p>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Results */}
        <div className="lg:col-span-2">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                Results
                {results.length > 0 && (
                  <span className="ml-2 text-sm font-normal text-slate-500">
                    ({results.length} match{results.length !== 1 ? 'es' : ''})
                  </span>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent>
              {searchMutation.isPending ? (
                <div className="flex h-64 items-center justify-center">
                  <div className="flex flex-col items-center gap-2">
                    <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
                    <p className="text-sm text-slate-500">Analyzing face features...</p>
                  </div>
                </div>
              ) : results.length > 0 ? (
                <div className="space-y-4">
                  {results.map((result) => (
                    <div
                      key={result.person_id}
                      className="rounded-lg border border-slate-200 p-4 dark:border-slate-700"
                    >
                      <div className="flex items-start gap-4">
                        <div className="h-16 w-16 shrink-0 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                          {result.thumbnail_url ? (
                            <img
                              src={result.thumbnail_url}
                              alt={result.person_name}
                              className="h-full w-full object-cover"
                            />
                          ) : (
                            <div className="flex h-full w-full items-center justify-center">
                              <ScanFace className="h-8 w-8 text-slate-400" />
                            </div>
                          )}
                        </div>
                        <div className="flex-1">
                          <div className="flex items-center gap-2">
                            <h3 className="font-semibold text-slate-900 dark:text-white">
                              {result.person_name}
                            </h3>
                            <Badge variant="outline" className="text-xs">
                              {result.group}
                            </Badge>
                            <Badge
                              className={`text-xs ${
                                result.confidence >= 0.9
                                  ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                                  : result.confidence >= 0.7
                                  ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400'
                                  : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
                              }`}
                            >
                              <Percent className="mr-0.5 h-3 w-3" />
                              {Math.round(result.confidence * 100)}%
                            </Badge>
                          </div>

                          {/* Sightings Timeline */}
                          {result.sightings.length > 0 && (
                            <div className="mt-3">
                              <p className="mb-2 text-xs font-medium text-slate-500 dark:text-slate-400">
                                Recent Sightings
                              </p>
                              <div className="space-y-2">
                                {result.sightings.map((sighting, idx) => (
                                  <div
                                    key={idx}
                                    className="flex items-center gap-3 rounded-md bg-slate-50 px-3 py-2 dark:bg-slate-800"
                                  >
                                    {sighting.snapshot_url ? (
                                      <img
                                        src={sighting.snapshot_url}
                                        alt=""
                                        className="h-10 w-14 rounded object-cover"
                                      />
                                    ) : (
                                      <div className="flex h-10 w-14 items-center justify-center rounded bg-slate-200 dark:bg-slate-700">
                                        <ImageIcon className="h-4 w-4 text-slate-400" />
                                      </div>
                                    )}
                                    <div className="flex-1 min-w-0">
                                      <div className="flex items-center gap-2 text-xs">
                                        <Camera className="h-3 w-3 text-slate-400" />
                                        <span className="font-medium text-slate-700 dark:text-slate-300">
                                          {sighting.camera_name}
                                        </span>
                                      </div>
                                      <div className="flex items-center gap-3 text-xs text-slate-400">
                                        <span className="flex items-center gap-1">
                                          <MapPin className="h-3 w-3" />
                                          {sighting.location}
                                        </span>
                                        <span className="flex items-center gap-1">
                                          <Clock className="h-3 w-3" />
                                          {new Date(sighting.timestamp).toLocaleString()}
                                        </span>
                                      </div>
                                    </div>
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              ) : searchMutation.isSuccess ? (
                <div className="flex h-64 flex-col items-center justify-center text-slate-400">
                  <Search className="mb-3 h-10 w-10" />
                  <p className="text-sm font-medium">No matches found</p>
                  <p className="mt-1 text-xs">Try lowering the confidence threshold</p>
                </div>
              ) : (
                <div className="flex h-64 flex-col items-center justify-center text-slate-400">
                  <ScanFace className="mb-3 h-10 w-10" />
                  <p className="text-sm font-medium">Upload an image to search</p>
                  <p className="mt-1 text-xs">
                    We will find matching persons and their sighting history
                  </p>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
