"""Validated complete instrument timelines; unknown rules never acquire trading defaults."""
from datetime import date, timezone
from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, field_serializer, field_validator, model_validator

Amount = Annotated[Decimal, Field(max_digits=38, decimal_places=18, ge=0)]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3,10}$")]


class StrictModel(BaseModel):
    """Reject undocumented inputs so a future execution flag cannot slip into research metadata."""
    model_config = ConfigDict(extra="forbid")


class EffectivePeriod(StrictModel):
    """UTC-normalized half-open interval; absence of coverage is not permission to extrapolate."""
    effective_from: AwareDatetime
    effective_to: AwareDatetime | None = None

    @field_validator("effective_from", "effective_to")
    @classmethod
    def utc_times(cls, value):
        """Normalize explicitly zoned inputs to UTC; never infer the host timezone."""
        return value.astimezone(timezone.utc) if value else None

    @model_validator(mode="after")
    def ordered_bounds(self):
        """Require a positive interval when an end is known; the end is exclusive."""
        if self.effective_to and self.effective_to <= self.effective_from:
            raise ValueError("规格有效期结束必须晚于开始")
        return self


class SpecPeriod(EffectivePeriod):
    """Exact product units/currencies, with settlement and resale restrictions kept separate."""
    currency: Currency
    quote_currency: Currency
    base_currency: Currency | None = None
    settlement_currency: Currency | None = None
    lot_size: Amount | None = None
    tick_size: Amount | None = None
    multiplier: Amount | None = None
    min_notional: Amount | None = None
    tick_rule: str = Field(default="UNKNOWN", max_length=100)
    trading_status: Literal["UNKNOWN", "LISTED", "DELISTED", "SUSPENDED"] = "UNKNOWN"
    short_selling: Literal["UNKNOWN", "PROHIBITED", "CONDITIONAL"] = "UNKNOWN"
    margin: Literal["UNKNOWN", "PROHIBITED", "CONDITIONAL"] = "UNKNOWN"
    resale_restriction: str = Field(default="UNKNOWN", max_length=200)
    cash_settlement: str = Field(default="UNKNOWN", max_length=200)
    security_settlement: str = Field(default="UNKNOWN", max_length=200)
    note: str = Field(max_length=1000)

    @field_validator("lot_size", "tick_size", "multiplier")
    @classmethod
    def positive_units(cls, value):
        """Unknown is null; a known unit must be strictly positive in product native units."""
        if value is not None and value <= 0:
            raise ValueError("已知数量/价位单位必须大于0")
        return value

    @field_serializer("lot_size", "tick_size", "multiplier", "min_notional")
    def exact_decimal(self, value):
        """Serialize NUMERIC without float conversion or Decimal context rounding."""
        if value is None:
            return None
        result = format(value, "f")
        return result.rstrip("0").rstrip(".") if "." in result else result


class AliasPeriod(EffectivePeriod):
    """Exact source-native code validity, separate from the canonical asset ID."""
    provider_id: str = Field(min_length=1, max_length=50)
    alias: str = Field(min_length=1, max_length=100)


class Evidence(StrictModel):
    """Public verification citation; published date is not this system's observation time."""
    url: HttpUrl
    title: str = Field(min_length=1, max_length=200)
    published_on: date | None = None
    checked_on: date
    scope: str = Field(min_length=1, max_length=1000)

    @field_validator("url")
    @classmethod
    def public_https(cls, value):
        """Allow HTTPS citations without embedded credentials; no endpoint fetches these URLs."""
        if value.scheme != "https" or value.username or value.password:
            raise ValueError("证据地址必须为无凭据的HTTPS链接")
        return value


class InstrumentTimeline(StrictModel):
    """One complete immutable timeline; later versions replace the whole view, not past records."""
    schema_version: Literal[1] = 1
    asset_id: str = Field(min_length=1, max_length=100)
    instrument_type: Literal["stock", "etf", "index", "spot", "perpetual", "future", "reference"]
    asset_class: str = Field(min_length=1, max_length=50)
    mic: str | None = Field(default=None, pattern=r"^[A-Z0-9]{4}$")
    isin: str | None = Field(default=None, pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
    timezone: str
    calendar_id: str
    calendar_status: Literal["PLANNED", "VERIFIED"] = "PLANNED"
    listing_date: date | None = None
    delisting_date: date | None = None
    identity_status: Literal["UNVERIFIED", "SOURCE_VERIFIED"]
    note: str = Field(max_length=2000)
    evidence: list[Evidence] = Field(min_length=1, max_length=20)
    periods: list[SpecPeriod] = Field(min_length=1, max_length=100)
    aliases: list[AliasPeriod] = Field(max_length=100)

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        """Check an IANA zone name without claiming its exchange session calendar is implemented."""
        try:
            ZoneInfo(value)
        except (KeyError, ValueError):
            raise ValueError("无效交易所时区") from None
        return value

    @model_validator(mode="after")
    def nonoverlapping_timeline(self):
        """Reject ambiguous spec/alias intervals; gaps remain legitimate UNKNOWN coverage."""
        groups = [self.periods]
        for provider in {item.provider_id for item in self.aliases}:
            groups.append([item for item in self.aliases if item.provider_id == provider])
        for group in groups:
            ordered = sorted(group, key=lambda item: item.effective_from)
            for previous, current in zip(ordered, ordered[1:]):
                if previous.effective_to is None or current.effective_from < previous.effective_to:
                    raise ValueError("同一时间线的规格或同供应商别名有效期重叠")
        if self.listing_date and self.delisting_date and self.delisting_date < self.listing_date:
            raise ValueError("退市日期早于上市日期")
        self.periods.sort(key=lambda item: item.effective_from)
        self.aliases.sort(key=lambda item: (item.provider_id, item.effective_from))
        return self
