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
  market_category: "crypto" | "stocks" | "etf" | "other";
  product_type: "spot" | "perpetual" | "future" | "reference" | "other";
  venue: string;
  priority: number;
  verification: string;
  verified_at: string | null;
  themes: string[];
  datasets: Dataset[];
  series?: { metric: string; rows: number; as_of_ms: number; unit: string; sha256: string }[];
  market_source?: {
    provider_id: string; kind: "candles" | "observations"; intervals: string[];
    provider_status: string; credential_configured: boolean; latest_data_at_ms: number | null;
  } | null;
  specification: Record<string, unknown>;
  live_tradable: boolean;
}
/** A daily or slower published scalar value; it has no fabricated OHLC fields. */
export interface MarketObservationPoint {
  observed_at_ms: number;
  value: string;
  unit: string;
}
/** Authenticated local provider-reference series and Parquet provenance. */
export interface MarketObservationSeries {
  asset_id: string;
  symbol?: string;
  metric: string;
  unit: string;
  available: boolean;
  observations: MarketObservationPoint[];
  available_count?: number;
  first_observed_at_ms?: number;
  last_observed_at_ms?: number;
  source?: Record<string, unknown>;
  sha256?: string;
  research_only: boolean;
}
export interface Candle {
  open_time: number;
  close_time: number;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  is_closed?: boolean;
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
  source_interval?: string;
  candles?: Candle[];
  available_count?: number;
  first_open_ms?: number | null;
  last_close_ms?: number | null;
  requested_start_ms?: number;
  updated_at_ms?: number;
  status?: string;
  source?: string;
  source_freshness?: string;
  source_event_at_ms?: number | null;
  segment_sha256?: string;
  research_only: boolean;
  available?: boolean;
}
/** Authenticated public refresh result; open candles remain display-only until close. */
export interface LiveMarketResult {
  asset_id: string;
  interval: string;
  closed_candles: Candle[];
  live_candle: Candle | null;
  last_closed_at_ms?: number | null;
  updated_at_ms?: number;
  source_freshness?: string;
  archive_sha256?: string;
  dataset_sha256?: string;
  live_snapshot_sha256?: string | null;
  source?: string;
  research_only: boolean;
  stale: boolean;
  refresh_error?: string;
  new_segments?: { sha256: string }[];
}
/** Transparent structural cycle summary; the signed score is not a probability. */
export interface MarketSentiment {
  schema_version: number;
  algorithm: string;
  as_of_ms: number;
  regime: "UPTREND" | "DOWNTREND" | "MIXED" | "TRANSITIONING" | "UNKNOWN" | string;
  signed_score: number | null;
  score_meaning: string;
  known_asset_count: number;
  unknown_asset_count: number;
  research_only: boolean;
  assets: {
    asset_id: string;
    symbol: string;
    name: string;
    status: string;
    regime: string;
    signed_score: number | null;
    missing_archives: string[];
    timeframes: {
      timeframe: string;
      score: number;
      trend_component: number;
      dow123_component: number;
      recent_divergence_component: number;
      trendline_break_state: string;
      volatility_20_bar_pct: number | null;
      last_closed_at: number;
      source_sha256: string | null;
      support_resistance: Record<string, { state: string; price: string | null }>;
      recent_events: { kind: string; occurred_at: number; confirmed_at: number }[];
      wave_candidate: string;
    }[];
  }[];
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
  wave?: { status: string; direction: number; interpretation: string; invalidation: string | null; labels: string[]; points?: { time: number; price: string; confirmed_at: number }[] };
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
  parameters: Record<string, unknown>;
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
/** Persisted display-only chart overlays; these preferences never change model decisions. */
export interface ChartIndicators {
  show_ma: boolean;
  ma_periods: number[];
  show_bollinger: boolean;
  show_macd: boolean;
  show_td: boolean;
  show_trendlines: boolean;
  show_dow123: boolean;
  show_divergence: boolean;
  show_wave: boolean;
}
/** Compact chart defaults inspired by common exchange chart workspaces. */
export const DEFAULT_CHART_INDICATORS: ChartIndicators = {
  show_ma: true,
  ma_periods: [5, 10, 20],
  show_bollinger: false,
  show_macd: false,
  show_td: false,
  show_trendlines: false,
  show_dow123: false,
  show_divergence: false,
  show_wave: false,
};
export interface Settings {
  display: Display;
  chart_indicators: ChartIndicators;
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
/** Persistent user-owned backtest configuration; contains no balances or fills. */
export interface SimulationAccount {
  id: string;
  name: string;
  initial_equity: string;
  base_currency: "USDT";
  configuration: {
    asset_ids: string[];
    intervals: string[];
    timeframe_weights: Record<string, string>;
    direction_mode: "trend_following" | "countertrend_rebound" | "balanced";
    decision_horizon_hours: 72 | 168 | 720;
    horizon_consensus_weight: string;
    weights: Record<"trend" | "dow123" | "wave" | "divergence" | "reversal_patterns" | "deep_model", string>;
    entry_threshold: string;
    max_leverage: string;
    max_margin_fraction: string;
    min_holding_hours: number;
    max_holding_hours: number;
    exit_mode: "three_bar_trailing" | "atr" | "fixed_rr" | "time";
    deep_model_version: string | null;
  };
  configuration_sha256: string;
  revision: number;
  created_at: string;
  updated_at: string;
  research_only: true;
  backtest_implemented: boolean;
}
/** Durable status and result summary for one local account strategy backtest. */
export interface SimulationBacktestRun {
  id: string;
  status: "QUEUED" | "RUNNING" | "RETRYING" | "FAILED" | "SUCCEEDED";
  parameters: {
    start_ms: number;
    end_ms: number;
    account_revision: number;
    configuration_sha256: string;
  };
  result: {
    status?: "COMPLETED" | "NO_DATA";
    initial_equity?: string;
    final_equity?: string;
    net_return_pct?: string;
    max_drawdown_pct?: string;
    trade_count?: number;
    algorithm_sha256?: string;
    multi_timeframe_policy?: {
      direction_mode: string;
      timeframe_weights: Record<string, string>;
      decision_horizon_hours: number;
      horizon_consensus_weight: string;
    };
    forecast_definition?: { status: string; horizons_hours: number[]; scenario_band: string };
    skipped_assets?: { asset_id: string; status: string; reason: string }[];
    per_asset?: { asset_id: string; metrics: Record<string, string | number>; dataset_hashes: Record<string, unknown>; forecast_evaluation?: Record<string, { observations: number; directional_accuracy_pct: number | null; mean_directional_return_pct: number | null }> }[];
  };
  created_at: string;
  updated_at: string;
  research_only: true;
}
/** One combined-account equity point; amount fields remain decimal strings for display. */
export interface SimulationEquityPoint {
  close_time: number;
  equity: string;
  drawdown: string;
}
export type PageId =
  | "dashboard"
  | "assets"
  | "charts"
  | "jobs"
  | "roadmap"
  | "settings"
  | "risk"
  | "simulation";
