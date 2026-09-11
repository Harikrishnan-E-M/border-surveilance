'use client';

import { useState, useRef, useEffect, useCallback, useMemo, Fragment } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Send,
  Plus,
  Trash2,
  MessageSquare,
  Bot,
  User,
  Loader2,
  Copy,
  Check,
  ChevronRight,
  Sparkles,
  Camera,
  Bell,
  BarChart2,
  Users,
  Car,
  Activity,
  Film,
  AlertTriangle,
  BellRing,
  CameraOff,
  X,
  Menu,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
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

// -------------------------------------------------------------------
// Types
// -------------------------------------------------------------------

interface DataCard {
  type: 'table' | 'chart' | 'stat' | 'alert_list' | 'camera_grid';
  title: string;
  data: any;
}

interface ConversationMessage {
  role: 'user' | 'assistant';
  content: string;
  data_cards: DataCard[];
  timestamp: string;
}

interface ConversationSummary {
  id: string;
  title: string;
  last_message: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

interface ChatResponse {
  response_text: string;
  conversation_id: string;
  data_cards: DataCard[];
  suggested_followups: string[];
}

interface SuggestedQuestion {
  question: string;
  category: string;
  icon: string;
}

// -------------------------------------------------------------------
// Icon map for suggested questions
// -------------------------------------------------------------------

const iconMap: Record<string, React.ElementType> = {
  camera: Camera,
  'camera-off': CameraOff,
  bell: Bell,
  'bell-ring': BellRing,
  'alert-triangle': AlertTriangle,
  'bar-chart-2': BarChart2,
  activity: Activity,
  users: Users,
  car: Car,
  film: Film,
};

function QuestionIcon({ name }: { name: string }) {
  const Icon = iconMap[name] || Sparkles;
  return <Icon className="h-4 w-4 shrink-0" />;
}

// -------------------------------------------------------------------
// Markdown-like rendering (bold, lists, inline code, tables)
// -------------------------------------------------------------------

function renderMarkdown(text: string): React.ReactNode {
  if (!text) return null;

  const lines = text.split('\n');
  const elements: React.ReactNode[] = [];
  let inCodeBlock = false;
  let codeLines: string[] = [];
  let tableLines: string[] = [];

  const flushTable = (key: string) => {
    if (tableLines.length < 2) {
      tableLines.forEach((line, i) =>
        elements.push(<p key={`${key}-tl-${i}`}>{renderInline(line)}</p>)
      );
      tableLines = [];
      return;
    }

    const headerLine = tableLines[0];
    const dataLines = tableLines.slice(2); // skip separator row
    const headers = headerLine
      .split('|')
      .map((h) => h.trim())
      .filter(Boolean);

    elements.push(
      <div key={key} className="my-2 overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800">
              {headers.map((h, i) => (
                <th key={i} className="px-3 py-2 font-semibold text-slate-700 dark:text-slate-300">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {dataLines.map((row, ri) => {
              const cells = row
                .split('|')
                .map((c) => c.trim())
                .filter(Boolean);
              return (
                <tr key={ri} className="border-b border-slate-100 dark:border-slate-800">
                  {cells.map((c, ci) => (
                    <td key={ci} className="px-3 py-1.5 text-slate-600 dark:text-slate-400">
                      {c}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
    tableLines = [];
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Code block toggle
    if (line.trim().startsWith('```')) {
      if (inCodeBlock) {
        elements.push(
          <pre
            key={`code-${i}`}
            className="my-2 overflow-x-auto rounded-lg bg-slate-900 p-3 text-xs text-slate-100"
          >
            <code>{codeLines.join('\n')}</code>
          </pre>
        );
        codeLines = [];
        inCodeBlock = false;
      } else {
        if (tableLines.length > 0) flushTable(`tbl-pre-${i}`);
        inCodeBlock = true;
      }
      continue;
    }

    if (inCodeBlock) {
      codeLines.push(line);
      continue;
    }

    // Table row detection
    if (line.trim().startsWith('|') && line.trim().endsWith('|')) {
      tableLines.push(line.trim());
      continue;
    } else if (tableLines.length > 0) {
      flushTable(`tbl-${i}`);
    }

    // Headings
    if (line.startsWith('### ')) {
      elements.push(
        <h4 key={i} className="mt-3 mb-1 text-sm font-bold text-slate-900 dark:text-white">
          {renderInline(line.slice(4))}
        </h4>
      );
      continue;
    }
    if (line.startsWith('## ')) {
      elements.push(
        <h3 key={i} className="mt-3 mb-1 text-base font-bold text-slate-900 dark:text-white">
          {renderInline(line.slice(3))}
        </h3>
      );
      continue;
    }

    // Unordered list item
    if (/^[-*]\s+/.test(line.trim())) {
      elements.push(
        <div key={i} className="flex gap-2 py-0.5 pl-2">
          <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-slate-400" />
          <span className="text-sm">{renderInline(line.trim().replace(/^[-*]\s+/, ''))}</span>
        </div>
      );
      continue;
    }

    // Numbered list item
    if (/^\d+\.\s+/.test(line.trim())) {
      const match = line.trim().match(/^(\d+)\.\s+(.*)/);
      if (match) {
        elements.push(
          <div key={i} className="flex gap-2 py-0.5 pl-2">
            <span className="shrink-0 text-sm font-medium text-slate-500">{match[1]}.</span>
            <span className="text-sm">{renderInline(match[2])}</span>
          </div>
        );
        continue;
      }
    }

    // Empty line
    if (line.trim() === '') {
      elements.push(<div key={i} className="h-2" />);
      continue;
    }

    // Normal paragraph
    elements.push(
      <p key={i} className="text-sm leading-relaxed">
        {renderInline(line)}
      </p>
    );
  }

  if (tableLines.length > 0) flushTable('tbl-final');

  return <>{elements}</>;
}

function renderInline(text: string): React.ReactNode {
  // Handle bold, inline code, and links
  const parts: React.ReactNode[] = [];
  const regex = /(\*\*(.+?)\*\*|`(.+?)`|\[(.+?)\]\((.+?)\))/g;
  let lastIndex = 0;
  let match;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    if (match[2]) {
      // Bold
      parts.push(
        <strong key={match.index} className="font-semibold text-slate-900 dark:text-white">
          {match[2]}
        </strong>
      );
    } else if (match[3]) {
      // Inline code
      parts.push(
        <code
          key={match.index}
          className="rounded bg-slate-100 px-1 py-0.5 text-xs font-mono text-slate-800 dark:bg-slate-700 dark:text-slate-200"
        >
          {match[3]}
        </code>
      );
    } else if (match[4] && match[5]) {
      // Link
      parts.push(
        <a
          key={match.index}
          href={match[5]}
          className="text-blue-600 underline dark:text-blue-400"
          target="_blank"
          rel="noopener noreferrer"
        >
          {match[4]}
        </a>
      );
    }
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  return parts.length === 1 ? parts[0] : <>{parts}</>;
}

// -------------------------------------------------------------------
// Data Card Components
// -------------------------------------------------------------------

function DataCardRenderer({ card }: { card: DataCard }) {
  switch (card.type) {
    case 'table':
      return <TableCard card={card} />;
    case 'stat':
      return <StatCard card={card} />;
    case 'alert_list':
      return <AlertListCard card={card} />;
    case 'camera_grid':
      return <CameraGridCard card={card} />;
    case 'chart':
      return <ChartCard card={card} />;
    default:
      return null;
  }
}

function TableCard({ card }: { card: DataCard }) {
  const { headers, rows } = card.data || { headers: [], rows: [] };
  if (!headers?.length || !rows?.length) return null;

  return (
    <Card className="my-2">
      <CardHeader className="pb-2 pt-3 px-4">
        <CardTitle className="text-xs font-semibold text-slate-700 dark:text-slate-300">
          {card.title}
        </CardTitle>
      </CardHeader>
      <CardContent className="px-0 pb-2">
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                {headers.map((h: string, i: number) => (
                  <TableHead key={i} className="text-xs">
                    {h}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row: any[], ri: number) => (
                <TableRow key={ri}>
                  {row.map((cell: any, ci: number) => (
                    <TableCell key={ci} className="text-xs py-1.5">
                      {cell === null || cell === undefined ? 'N/A' : String(cell)}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
}

function StatCard({ card }: { card: DataCard }) {
  const { label, value, trend } = card.data || {};
  return (
    <Card className="my-2">
      <CardContent className="flex items-center justify-between p-4">
        <div>
          <p className="text-xs font-medium text-slate-500 dark:text-slate-400">{card.title}</p>
          <p className="mt-1 text-lg font-bold text-slate-900 dark:text-white">{value}</p>
          {label && (
            <p className="text-xs text-slate-400 dark:text-slate-500">{label}</p>
          )}
        </div>
        {trend && (
          <div
            className={`rounded-full p-2 ${
              trend === 'up'
                ? 'bg-green-50 text-green-600 dark:bg-green-900/30 dark:text-green-400'
                : trend === 'down'
                ? 'bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-400'
                : 'bg-slate-50 text-slate-500 dark:bg-slate-800 dark:text-slate-400'
            }`}
          >
            <Activity className="h-5 w-5" />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function AlertListCard({ card }: { card: DataCard }) {
  const alerts = card.data || [];
  if (!alerts.length) return null;

  const severityColor: Record<string, string> = {
    critical: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
    high: 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400',
    medium: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
    low: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
  };

  return (
    <Card className="my-2">
      <CardHeader className="pb-2 pt-3 px-4">
        <CardTitle className="text-xs font-semibold text-slate-700 dark:text-slate-300">
          {card.title}
        </CardTitle>
      </CardHeader>
      <CardContent className="px-3 pb-3">
        <div className="space-y-1.5">
          {alerts.map((alert: any, i: number) => (
            <div
              key={alert.id || i}
              className="flex items-start gap-2 rounded-lg border border-slate-100 p-2 dark:border-slate-700"
            >
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="truncate text-xs font-medium text-slate-800 dark:text-slate-200">
                    {alert.title}
                  </span>
                  <Badge className={`text-[10px] px-1.5 py-0 ${severityColor[alert.severity] || ''}`}>
                    {alert.severity}
                  </Badge>
                </div>
                <div className="mt-0.5 flex items-center gap-2 text-[10px] text-slate-400">
                  <span>{alert.camera}</span>
                  <span>{alert.type}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function CameraGridCard({ card }: { card: DataCard }) {
  const cameras = card.data || [];
  if (!cameras.length) return null;

  return (
    <Card className="my-2">
      <CardHeader className="pb-2 pt-3 px-4">
        <CardTitle className="text-xs font-semibold text-slate-700 dark:text-slate-300">
          {card.title}
        </CardTitle>
      </CardHeader>
      <CardContent className="px-3 pb-3">
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
          {cameras.map((cam: any, i: number) => (
            <div
              key={cam.id || i}
              className="flex flex-col items-center gap-1 rounded-lg border border-slate-100 p-2 dark:border-slate-700"
            >
              <div
                className={`h-3 w-3 rounded-full ${
                  cam.status === 'online' ? 'bg-green-500' : 'bg-red-500'
                }`}
              />
              <span className="truncate text-[10px] font-medium text-slate-600 dark:text-slate-400 max-w-full">
                {cam.name}
              </span>
              <span className="text-[9px] text-slate-400">{cam.location || ''}</span>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function ChartCard({ card }: { card: DataCard }) {
  const { labels, datasets } = card.data || { labels: [], datasets: [] };
  if (!labels?.length || !datasets?.length) return null;

  // Simple bar chart rendered with divs
  const dataset = datasets[0];
  const maxValue = Math.max(...(dataset.data || [1]));

  return (
    <Card className="my-2">
      <CardHeader className="pb-2 pt-3 px-4">
        <CardTitle className="text-xs font-semibold text-slate-700 dark:text-slate-300">
          {card.title}
        </CardTitle>
      </CardHeader>
      <CardContent className="px-4 pb-3">
        <div className="flex items-end gap-2 h-32">
          {labels.map((label: string, i: number) => {
            const value = dataset.data?.[i] || 0;
            const pct = maxValue > 0 ? (value / maxValue) * 100 : 0;
            return (
              <div key={i} className="flex flex-1 flex-col items-center gap-1">
                <span className="text-[10px] font-medium text-slate-600 dark:text-slate-400">
                  {value}
                </span>
                <div
                  className="w-full rounded-t bg-blue-500 dark:bg-blue-400 transition-all"
                  style={{ height: `${Math.max(pct, 4)}%` }}
                />
                <span className="truncate text-[9px] text-slate-400 max-w-full">{label}</span>
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}

// -------------------------------------------------------------------
// Copy Button
// -------------------------------------------------------------------

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [text]);

  return (
    <button
      onClick={handleCopy}
      className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-700 dark:hover:text-slate-300 transition-colors"
      title="Copy response"
    >
      {copied ? <Check className="h-3.5 w-3.5 text-green-500" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  );
}

// -------------------------------------------------------------------
// Main Copilot Page
// -------------------------------------------------------------------

export default function CopilotPage() {
  const queryClient = useQueryClient();

  // State
  const [input, setInput] = useState('');
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [suggestedFollowups, setSuggestedFollowups] = useState<string[]>([]);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Focus input on mount and conversation changes
  useEffect(() => {
    inputRef.current?.focus();
  }, [conversationId]);

  // ── API Queries ──────────────────────────────────────────────────

  const { data: conversations, isLoading: conversationsLoading } = useQuery<
    ConversationSummary[]
  >({
    queryKey: ['copilot', 'conversations'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/copilot/conversations');
      return res.data?.data || [];
    },
    refetchInterval: 30000,
  });

  const { data: suggestedQuestions } = useQuery<SuggestedQuestion[]>({
    queryKey: ['copilot', 'suggestions'],
    queryFn: async () => {
      const res = await apiClient.get('/api/v1/copilot/suggestions');
      return res.data?.data || [];
    },
    staleTime: 60000,
  });

  // ── Chat Mutation ────────────────────────────────────────────────

  const chatMutation = useMutation<ChatResponse, Error, { message: string }>({
    mutationFn: async ({ message }) => {
      const res = await apiClient.post('/api/v1/copilot/chat', {
        message,
        conversation_id: conversationId,
      });
      return res.data?.data;
    },
    onSuccess: (data) => {
      if (data) {
        setConversationId(data.conversation_id);

        setMessages((prev) => {
          // Remove the loading indicator
          const filtered = prev.filter((m) => m.content !== '__loading__');
          return [
            ...filtered,
            {
              role: 'assistant',
              content: data.response_text,
              data_cards: data.data_cards || [],
              timestamp: new Date().toISOString(),
            },
          ];
        });

        setSuggestedFollowups(data.suggested_followups || []);
        queryClient.invalidateQueries({ queryKey: ['copilot', 'conversations'] });
      }
    },
    onError: () => {
      setMessages((prev) => {
        const filtered = prev.filter((m) => m.content !== '__loading__');
        return [
          ...filtered,
          {
            role: 'assistant',
            content:
              'I apologize, but I encountered an error processing your request. Please try again.',
            data_cards: [],
            timestamp: new Date().toISOString(),
          },
        ];
      });
    },
  });

  // ── Delete Conversation Mutation ─────────────────────────────────

  const deleteMutation = useMutation<void, Error, string>({
    mutationFn: async (convId) => {
      await apiClient.delete(`/api/v1/copilot/conversations/${convId}`);
    },
    onSuccess: (_, deletedId) => {
      queryClient.invalidateQueries({ queryKey: ['copilot', 'conversations'] });
      if (conversationId === deletedId) {
        handleNewConversation();
      }
    },
  });

  // ── Handlers ─────────────────────────────────────────────────────

  const handleSend = useCallback(() => {
    const trimmed = input.trim();
    if (!trimmed || chatMutation.isPending) return;

    const userMessage: ConversationMessage = {
      role: 'user',
      content: trimmed,
      data_cards: [],
      timestamp: new Date().toISOString(),
    };

    // Add user message and loading indicator
    setMessages((prev) => [
      ...prev,
      userMessage,
      {
        role: 'assistant',
        content: '__loading__',
        data_cards: [],
        timestamp: new Date().toISOString(),
      },
    ]);

    setInput('');
    setSuggestedFollowups([]);
    chatMutation.mutate({ message: trimmed });
  }, [input, chatMutation, conversationId]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault();
        handleSend();
      }
      // Also send on plain Enter (without shift)
      if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  const handleNewConversation = useCallback(() => {
    setConversationId(null);
    setMessages([]);
    setSuggestedFollowups([]);
    setMobileSidebarOpen(false);
    inputRef.current?.focus();
  }, []);

  const handleSelectConversation = useCallback(
    async (convId: string) => {
      setConversationId(convId);
      setMobileSidebarOpen(false);
      setSuggestedFollowups([]);

      try {
        const res = await apiClient.get(`/api/v1/copilot/conversations/${convId}`);
        const detail = res.data?.data;
        if (detail?.messages) {
          setMessages(detail.messages);
        }
      } catch {
        setMessages([]);
      }
    },
    []
  );

  const handleSuggestionClick = useCallback(
    (question: string) => {
      setInput(question);
      // Auto-send
      setTimeout(() => {
        const userMessage: ConversationMessage = {
          role: 'user',
          content: question,
          data_cards: [],
          timestamp: new Date().toISOString(),
        };
        setMessages((prev) => [
          ...prev,
          userMessage,
          {
            role: 'assistant',
            content: '__loading__',
            data_cards: [],
            timestamp: new Date().toISOString(),
          },
        ]);
        setInput('');
        setSuggestedFollowups([]);
        chatMutation.mutate({ message: question });
      }, 50);
    },
    [chatMutation]
  );

  // ── Derived State ────────────────────────────────────────────────

  const isEmptyConversation = messages.length === 0;
  const isLoading = chatMutation.isPending;

  // ── Render ───────────────────────────────────────────────────────

  return (
    <div className="flex h-[calc(100vh-7rem)] overflow-hidden rounded-xl border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-800">
      {/* ── Sidebar ──────────────────────────────────────────────── */}

      {/* Desktop Sidebar */}
      {sidebarOpen && (
        <div className="hidden w-72 flex-col border-r border-slate-200 dark:border-slate-700 lg:flex">
          <SidebarContent
            conversations={conversations || []}
            conversationsLoading={conversationsLoading}
            conversationId={conversationId}
            onNew={handleNewConversation}
            onSelect={handleSelectConversation}
            onDelete={(id) => deleteMutation.mutate(id)}
          />
        </div>
      )}

      {/* Mobile Sidebar */}
      {mobileSidebarOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div
            className="absolute inset-0 bg-black/50"
            onClick={() => setMobileSidebarOpen(false)}
          />
          <div className="relative z-10 flex h-full w-72 flex-col bg-white shadow-xl dark:bg-slate-800">
            <div className="absolute right-2 top-3 z-20">
              <button
                onClick={() => setMobileSidebarOpen(false)}
                className="rounded-lg p-1 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            <SidebarContent
              conversations={conversations || []}
              conversationsLoading={conversationsLoading}
              conversationId={conversationId}
              onNew={handleNewConversation}
              onSelect={handleSelectConversation}
              onDelete={(id) => deleteMutation.mutate(id)}
            />
          </div>
        </div>
      )}

      {/* ── Main Chat Area ───────────────────────────────────────── */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Chat Header */}
        <div className="flex h-14 items-center justify-between border-b border-slate-200 px-4 dark:border-slate-700">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setMobileSidebarOpen(true)}
              className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-700 dark:hover:text-slate-300 lg:hidden"
            >
              <Menu className="h-5 w-5" />
            </button>
            <button
              onClick={() => setSidebarOpen(!sidebarOpen)}
              className="hidden rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-700 dark:hover:text-slate-300 lg:block"
              title={sidebarOpen ? 'Hide sidebar' : 'Show sidebar'}
            >
              <MessageSquare className="h-5 w-5" />
            </button>
            <div>
              <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
                AI Surveillance Copilot
              </h2>
              <p className="text-xs text-slate-400">
                Ask anything about your surveillance system
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant="secondary" className="text-xs">
              <Sparkles className="mr-1 h-3 w-3" />
              Claude
            </Badge>
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto">
          {isEmptyConversation ? (
            <EmptyState
              suggestedQuestions={suggestedQuestions || []}
              onQuestionClick={handleSuggestionClick}
            />
          ) : (
            <div className="mx-auto max-w-3xl px-4 py-4 space-y-4">
              {messages.map((msg, i) => (
                <MessageBubble key={i} message={msg} />
              ))}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>

        {/* Suggested Follow-ups */}
        {suggestedFollowups.length > 0 && !isLoading && (
          <div className="border-t border-slate-100 px-4 py-2 dark:border-slate-700">
            <div className="mx-auto flex max-w-3xl flex-wrap gap-2">
              {suggestedFollowups.map((q, i) => (
                <button
                  key={i}
                  onClick={() => handleSuggestionClick(q)}
                  className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-50 hover:text-slate-900 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-400 dark:hover:bg-slate-700 dark:hover:text-slate-200"
                >
                  <ChevronRight className="h-3 w-3" />
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Input Area */}
        <div className="border-t border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-800">
          <div className="mx-auto max-w-3xl">
            <div className="flex items-end gap-2 rounded-xl border border-slate-200 bg-slate-50 p-2 focus-within:border-blue-400 focus-within:ring-2 focus-within:ring-blue-100 dark:border-slate-600 dark:bg-slate-900 dark:focus-within:border-blue-500 dark:focus-within:ring-blue-900/30">
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask about alerts, cameras, analytics, faces, vehicles..."
                className="flex-1 resize-none bg-transparent px-2 py-1.5 text-sm text-slate-900 placeholder-slate-400 outline-none dark:text-slate-100 dark:placeholder-slate-500"
                rows={1}
                style={{
                  minHeight: '36px',
                  maxHeight: '120px',
                  height: 'auto',
                }}
                onInput={(e) => {
                  const target = e.target as HTMLTextAreaElement;
                  target.style.height = 'auto';
                  target.style.height = `${Math.min(target.scrollHeight, 120)}px`;
                }}
                disabled={isLoading}
              />
              <Button
                onClick={handleSend}
                disabled={!input.trim() || isLoading}
                size="sm"
                className="shrink-0 rounded-lg"
              >
                {isLoading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Send className="h-4 w-4" />
                )}
              </Button>
            </div>
            <p className="mt-1.5 text-center text-[10px] text-slate-400">
              Press Enter to send, Shift+Enter for new line
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

// -------------------------------------------------------------------
// Sidebar Content Component
// -------------------------------------------------------------------

function SidebarContent({
  conversations,
  conversationsLoading,
  conversationId,
  onNew,
  onSelect,
  onDelete,
}: {
  conversations: ConversationSummary[];
  conversationsLoading: boolean;
  conversationId: string | null;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <>
      {/* New Conversation Button */}
      <div className="p-3">
        <Button onClick={onNew} variant="outline" className="w-full justify-start gap-2 text-sm">
          <Plus className="h-4 w-4" />
          New Conversation
        </Button>
      </div>

      <Separator />

      {/* Conversation List */}
      <ScrollArea className="flex-1">
        <div className="p-2 space-y-1">
          {conversationsLoading ? (
            <div className="space-y-2 p-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="space-y-1.5">
                  <Skeleton className="h-4 w-3/4" />
                  <Skeleton className="h-3 w-1/2" />
                </div>
              ))}
            </div>
          ) : conversations.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-8 text-center">
              <MessageSquare className="h-8 w-8 text-slate-300 dark:text-slate-600" />
              <p className="text-xs text-slate-400">No conversations yet</p>
              <p className="text-[10px] text-slate-400">
                Start by asking a question
              </p>
            </div>
          ) : (
            conversations.map((conv) => (
              <div
                key={conv.id}
                className={`group relative flex cursor-pointer flex-col gap-0.5 rounded-lg px-3 py-2 transition-colors ${
                  conversationId === conv.id
                    ? 'bg-blue-50 dark:bg-blue-900/20'
                    : 'hover:bg-slate-50 dark:hover:bg-slate-700/50'
                }`}
                onClick={() => onSelect(conv.id)}
              >
                <p
                  className={`truncate text-xs font-medium ${
                    conversationId === conv.id
                      ? 'text-blue-700 dark:text-blue-400'
                      : 'text-slate-700 dark:text-slate-300'
                  }`}
                >
                  {conv.title}
                </p>
                <p className="truncate text-[10px] text-slate-400">
                  {conv.message_count} messages
                </p>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onDelete(conv.id);
                  }}
                  className="absolute right-2 top-2 hidden rounded p-1 text-slate-400 hover:bg-red-50 hover:text-red-500 group-hover:block dark:hover:bg-red-900/20"
                  title="Delete conversation"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            ))
          )}
        </div>
      </ScrollArea>
    </>
  );
}

// -------------------------------------------------------------------
// Empty State Component
// -------------------------------------------------------------------

function EmptyState({
  suggestedQuestions,
  onQuestionClick,
}: {
  suggestedQuestions: SuggestedQuestion[];
  onQuestionClick: (question: string) => void;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center px-4">
      <div className="mb-6 flex h-16 w-16 items-center justify-center rounded-2xl bg-blue-50 dark:bg-blue-900/30">
        <Bot className="h-8 w-8 text-blue-600 dark:text-blue-400" />
      </div>
      <h3 className="mb-2 text-lg font-semibold text-slate-900 dark:text-white">
        How can I help you monitor your facility?
      </h3>
      <p className="mb-8 max-w-md text-center text-sm text-slate-500 dark:text-slate-400">
        I can help you query alerts, check camera status, analyze footfall trends,
        search face and vehicle databases, and more.
      </p>

      {suggestedQuestions.length > 0 && (
        <div className="grid w-full max-w-2xl gap-2 sm:grid-cols-2">
          {suggestedQuestions.map((sq, i) => (
            <button
              key={i}
              onClick={() => onQuestionClick(sq.question)}
              className="flex items-start gap-3 rounded-xl border border-slate-200 bg-white p-3 text-left transition-all hover:border-blue-300 hover:shadow-sm dark:border-slate-700 dark:bg-slate-800 dark:hover:border-blue-600"
            >
              <div className="mt-0.5 rounded-lg bg-slate-100 p-1.5 dark:bg-slate-700">
                <QuestionIcon name={sq.icon} />
              </div>
              <span className="text-xs font-medium leading-relaxed text-slate-700 dark:text-slate-300">
                {sq.question}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// -------------------------------------------------------------------
// Message Bubble Component
// -------------------------------------------------------------------

function MessageBubble({ message }: { message: ConversationMessage }) {
  // Loading indicator
  if (message.role === 'assistant' && message.content === '__loading__') {
    return (
      <div className="flex items-start gap-3">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-blue-50 dark:bg-blue-900/30">
          <Bot className="h-4 w-4 text-blue-600 dark:text-blue-400" />
        </div>
        <div className="flex items-center gap-2 rounded-2xl rounded-tl-md bg-slate-100 px-4 py-3 dark:bg-slate-700">
          <div className="flex gap-1">
            <span className="h-2 w-2 animate-bounce rounded-full bg-slate-400 [animation-delay:0ms]" />
            <span className="h-2 w-2 animate-bounce rounded-full bg-slate-400 [animation-delay:150ms]" />
            <span className="h-2 w-2 animate-bounce rounded-full bg-slate-400 [animation-delay:300ms]" />
          </div>
          <span className="text-xs text-slate-400">Analyzing your data...</span>
        </div>
      </div>
    );
  }

  if (message.role === 'user') {
    return (
      <div className="flex items-start justify-end gap-3">
        <div className="max-w-[80%] rounded-2xl rounded-tr-md bg-blue-600 px-4 py-2.5 text-sm text-white">
          {message.content}
        </div>
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-blue-100 dark:bg-blue-900/50">
          <User className="h-4 w-4 text-blue-700 dark:text-blue-300" />
        </div>
      </div>
    );
  }

  // Assistant message
  return (
    <div className="flex items-start gap-3">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-blue-50 dark:bg-blue-900/30">
        <Bot className="h-4 w-4 text-blue-600 dark:text-blue-400" />
      </div>
      <div className="max-w-[85%] space-y-1">
        <div className="rounded-2xl rounded-tl-md bg-slate-100 px-4 py-3 dark:bg-slate-700">
          <div className="text-slate-700 dark:text-slate-200">
            {renderMarkdown(message.content)}
          </div>
        </div>

        {/* Data Cards */}
        {message.data_cards && message.data_cards.length > 0 && (
          <div className="space-y-1">
            {message.data_cards.map((card, i) => (
              <DataCardRenderer key={i} card={card} />
            ))}
          </div>
        )}

        {/* Copy button */}
        <div className="flex items-center gap-1 pl-1">
          <CopyButton text={message.content} />
          <span className="text-[10px] text-slate-300 dark:text-slate-600">
            {message.timestamp
              ? new Date(message.timestamp).toLocaleTimeString([], {
                  hour: '2-digit',
                  minute: '2-digit',
                })
              : ''}
          </span>
        </div>
      </div>
    </div>
  );
}
