/** A native specification keeps unknown units null and settlement distinct from resale restrictions. */
export interface InstrumentSpec {
  effective_from: string;
  effective_to: string | null;
  currency: string;
  quote_currency: string;
  base_currency: string | null;
  settlement_currency: string | null;
  lot_size: string | null;
  tick_size: string | null;
  multiplier: string | null;
  min_notional: string | null;
  tick_rule: string;
  cash_settlement: string;
  security_settlement: string;
  resale_restriction: string;
  short_selling: string;
  margin: string;
  note: string;
}
/** Complete source timeline; listing date does not establish historical rule coverage. */
export interface InstrumentTimeline {
  mic: string | null;
  isin: string | null;
  timezone: string;
  calendar_id: string;
  calendar_status: string;
  listing_date: string | null;
  note: string;
  periods: InstrumentSpec[];
  aliases: { provider_id: string; alias: string; effective_from: string; effective_to: string | null }[];
  evidence: { url: string; title: string; published_on: string | null; checked_on: string; scope: string }[];
}
/** Resolution never implies data acquisition, account permission or executable rules. */
export interface InstrumentResolution {
  status: string;
  version_id: string | null;
  recorded_at: string | null;
  specification: InstrumentSpec | null;
}
/** Each entry is a frozen complete timeline with a server receipt timestamp and content hash. */
export interface InstrumentHistory {
  versions: { id: string; recorded_at: string; timeline: InstrumentTimeline }[];
}

/** Build explicit UTC queries from optional datetime-local controls; never infer the browser timezone. */
export function instrumentQuery(assetId: string, effectiveAt: string, knownAt: string): string {
  const query = new URLSearchParams({ asset_id: assetId });
  for (const [key, value] of [["effective_at", effectiveAt], ["known_at", knownAt]]) {
    if (value) {
      if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(value)) throw new Error("请填写有效的 UTC 日期时间");
      query.set(key, `${value}Z`);
    }
  }
  return query.toString();
}

/** Display unknown rules explicitly; zero-like decimal strings are not treated as absent. */
export function specificationValue(value: string | null | undefined): string {
  return value == null || value === "UNKNOWN" ? "未核验" : value;
}
