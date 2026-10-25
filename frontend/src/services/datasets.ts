import type { Candle } from "../types";

/** Immutable dataset metadata; provider identity never changes the instrument identity. */
export interface DatasetVersion {
  id: string;
  asset_id: string;
  provider_id: string;
  interval: string;
  row_count: number;
  first_open_ms: number;
  last_close_ms: number;
  sha256: string;
  observed_at_ms: number;
  quality: Record<string, unknown>;
  provenance: Record<string, unknown>;
}

/** Server-owned collection capability; a catalog entry does not imply a successful integration. */
export interface DatasetCatalogItem {
  asset_id: string;
  name: string;
  provider_id: string;
  interval: string;
  product_type: string;
  currency: string;
  calendar_id: string;
  freshness: string;
  limitations: string[] | string;
  latest_version?: DatasetVersion | null;
}

/** Source-native bars retain their session label and actual availability timestamp. */
export interface DatasetBar extends Candle {
  session_date: string;
  available_at_ms: number;
}

/** Authenticated read-only response for one immutable version, with no inferred bars. */
export interface DatasetBars {
  version: DatasetVersion;
  candles: DatasetBar[];
  research_only: boolean;
}

const PROVIDER_LABELS: Record<string, string> = {
  yahoo_chart: "Yahoo 日线研究源",
  coinbase_exchange: "Coinbase Exchange 现货",
  legacy_binance_snapshot: "已有 Binance 永续快照",
};

/** Resolve the collection adapter label; provenance still identifies the actual underlying source. */
export function datasetProviderLabel(providerId: string): string {
  return PROVIDER_LABELS[providerId] ?? providerId;
}

/** Validate UTC calendar dates before submission; server-side range checks remain authoritative. */
export function datasetDateError(start: string, end: string, today = new Date().toISOString().slice(0, 10)): string | null {
  const dates = [start, end];
  if (dates.some((value) => !/^\d{4}-\d{2}-\d{2}$/.test(value) || !Number.isFinite(Date.parse(value)) || new Date(value).toISOString().slice(0, 10) !== value)) {
    return "请填写有效的开始和结束日期。";
  }
  if (end < start) return "结束日期不能早于开始日期。";
  if ((Date.parse(end) - Date.parse(start)) / 86_400_000 > 3650) return "单次日期范围不能超过 3650 天。";
  if (end > today) return "结束日期不能晚于今天（UTC）。";
  return null;
}

/** Keep the visible selection pinned if it belongs to the current product and still exists. */
export function retainDatasetSelection(versions: DatasetVersion[], currentId: string): string {
  return versions.some((version) => version.id === currentId) ? currentId : versions[0]?.id ?? "";
}
