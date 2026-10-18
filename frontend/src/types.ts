export interface Dataset {
  interval: string;
  rows: number;
  as_of_ms: number;
  first_open_ms: number;
  sha256: string;
}
export interface Asset {
  id: string;
  symbol: string;
  name: string;
  underlying_id: string;
  asset_type: string;
  venue: string;
  priority: number;
  verification: string;
  verified_at: string | null;
  themes: string[];
  datasets: Dataset[];
  specification: Record<string, unknown>;
  live_tradable: boolean;
}
export interface Candle {
  open_time: number;
  close_time: number;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
}
export interface CandleResult {
  asset_id: string;
  interval: string;
  source_interval: string;
  candles: Candle[];
  available_count: number;
  excluded_incomplete_buckets: number;
  as_of_ms: number;
  dataset_sha256: string;
  source: Record<string, unknown>;
}
export interface ArchiveResult {
  asset_id: string;
  symbol?: string;
  interval: string;
  candles?: Candle[];
  available_count?: number;
  first_open_ms?: number | null;
  last_close_ms?: number | null;
  requested_start_ms?: number;
  updated_at_ms?: number;
  status?: string;
  source?: string;
  segment_sha256?: string;
  research_only: boolean;
  available?: boolean;
}
/** Per-cycle structure output; all price values cross the API as Decimal strings. */
export interface StructureLine {
  state: string;
  value: string | null;
  slope_per_bar: string | null;
  anchors: { time: number; price: string; confirmed_at: number }[];
  confirmed_at?: number;
}
/** One closed-bar structure event with separate occurrence and known-at times. */
export interface StructureEvent {
  kind: string;
  timeframe: string;
  occurred_at: number;
  confirmed_at: number;
  evidence: Record<string, unknown>;
  source_sha256?: string | null;
}
/** Summary for one native candle timeframe. */
export interface StructureTimeframe {
  status: string;
  bars?: number;
  last_closed_at?: number;
  close?: string;
  trend?: number;
  trendline_break_state?: string;
  trend_lines?: Record<string, StructureLine>;
  dow123_state?: number;
  wave?: { status: string; direction: number; interpretation: string; invalidation: string | null; labels: string[] };
  latest_divergence?: StructureEvent | null;
}
/** Authenticated local-cache analysis for the selected asset and cycle set. */
export interface StructureResult {
  schema_version: number;
  algorithm: string;
  as_of_ms: number;
  timeframes: Record<string, StructureTimeframe>;
  events: StructureEvent[];
  missing_archives: string[];
  research_only: boolean;
}
export interface Interval {
  interval: string;
  available: boolean;
  reason: string | null;
}
export interface Job {
  id: string;
  kind: string;
  status: string;
  parameters: Record<string, string>;
  result: Record<string, unknown>;
  attempts: number;
  created_at: string;
  updated_at: string;
}
export interface Provider {
  id: string;
  name: string;
  status: string;
  detail: Record<string, unknown>;
}
export interface Capability {
  id: string;
  name: string;
  phase: number;
  status: string;
  detail: string;
}
export interface Display {
  timezone: "Asia/Shanghai" | "UTC";
  default_interval: string;
  default_asset: string;
}
export interface Settings {
  display: Display;
  execution: { mode: string; live_enabled: boolean; implemented: boolean };
  notifications: Record<string, unknown>;
  credentials: Record<string, unknown>;
}
export interface Overview {
  asset_count: number;
  datasets: number;
  candle_count: number;
  successful_tasks: number;
  market_regime: string;
  regime_reason: string;
  execution_mode: string;
  as_of_ms: number | null;
  priority_assets: Asset[];
}

export interface User {
  username: string;
  role: string;
  csrf_token: string;
}
export type PageId =
  | "dashboard"
  | "assets"
  | "charts"
  | "jobs"
  | "roadmap"
  | "settings"
  | "risk";
