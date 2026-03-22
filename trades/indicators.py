"""Technical indicator calculation for trade analysis.

Calculates indicators on-demand from OHLCV data. No DB storage.
Core indicator: Stochastic Slow (9 sets: 3 timeframes × 3 param sets).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

import numpy as np
import pandas as pd
import pandas_ta as ta


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STOCH_PARAMS = [
    (5, 3, 3),
    (10, 6, 6),
    (20, 12, 12),
]

MA_PERIODS = [5, 20, 60, 120]

# Minimum rows needed for the longest calculation (monthly stoch with 20,12,12)
# Monthly bars: 20 periods + smoothing. With daily data we need ~500+ days.
# But we'll fail gracefully when data is insufficient.
MIN_ROWS_DAILY = 30
MIN_ROWS_WEEKLY = 10
MIN_ROWS_MONTHLY = 5


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class StochSet:
    """One stochastic parameter set result."""
    k: int       # k period
    d: int       # d period
    smooth_k: int
    stoch_k: float | None
    stoch_d: float | None
    cross: Literal["golden", "dead", "none"]
    direction: Literal["rising", "falling", "sideways"]
    zone: Literal["overbought", "oversold", "neutral"]


@dataclass
class IndicatorResult:
    """All computed indicators for a single target date."""
    stochastic: dict  # {daily: [StochSet, ...], weekly: [...], monthly: [...]}
    ma: dict          # {alignment, deviations: {5: pct, 20: pct, ...}}
    bb: dict          # {position, bandwidth, squeeze_expanding}
    volume_ratio: float | None
    candle: dict      # {pattern, body_ratio, upper_shadow, lower_shadow, gap}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resample_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Resample daily OHLCV to weekly bars (first trading day's open, max high, min low, Fri close)."""
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)

    weekly = df.resample("W-FRI").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    ).dropna(subset=["open", "close"])
    return weekly


def _resample_monthly(df: pd.DataFrame) -> pd.DataFrame:
    """Resample daily OHLCV to monthly bars."""
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)

    monthly = df.resample("ME").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    ).dropna(subset=["open", "close"])
    return monthly


def _compute_stoch_set(bars: pd.DataFrame, k: int, d: int, smooth_k: int) -> StochSet:
    """Compute one stochastic parameter set and determine cross/direction/zone."""
    result = ta.stoch(bars["high"], bars["low"], bars["close"], k=k, d=d, smooth_k=smooth_k)

    if result is None or len(result) < 2:
        return StochSet(
            k=k, d=d, smooth_k=smooth_k,
            stoch_k=None, stoch_d=None,
            cross="none", direction="sideways", zone="neutral",
        )

    k_col = f"STOCHk_{k}_{d}_{smooth_k}"
    d_col = f"STOCHd_{k}_{d}_{smooth_k}"

    if k_col not in result.columns or d_col not in result.columns:
        return StochSet(
            k=k, d=d, smooth_k=smooth_k,
            stoch_k=None, stoch_d=None,
            cross="none", direction="sideways", zone="neutral",
        )

    # Last valid row is the target date
    valid = result[[k_col, d_col]].dropna()
    if len(valid) < 2:
        stoch_k_val = float(valid[k_col].iloc[-1]) if len(valid) == 1 else None
        stoch_d_val = float(valid[d_col].iloc[-1]) if len(valid) == 1 else None
        return StochSet(
            k=k, d=d, smooth_k=smooth_k,
            stoch_k=stoch_k_val, stoch_d=stoch_d_val,
            cross="none", direction="sideways",
            zone=_zone(stoch_k_val),
        )

    cur_k = float(valid[k_col].iloc[-1])
    cur_d = float(valid[d_col].iloc[-1])
    prev_k = float(valid[k_col].iloc[-2])
    prev_d = float(valid[d_col].iloc[-2])

    # Cross detection
    if prev_k < prev_d and cur_k > cur_d:
        cross = "golden"
    elif prev_k > prev_d and cur_k < cur_d:
        cross = "dead"
    else:
        cross = "none"

    # Direction: last 3 %K values
    if len(valid) >= 3:
        k3 = valid[k_col].iloc[-3:]
        diffs = np.diff(k3.values)
        rising_count = int((diffs > 0.5).sum())
        falling_count = int((diffs < -0.5).sum())
        if rising_count == 2:
            direction: Literal["rising", "falling", "sideways"] = "rising"
        elif falling_count == 2:
            direction = "falling"
        else:
            direction = "sideways"
    else:
        direction = "sideways"

    return StochSet(
        k=k, d=d, smooth_k=smooth_k,
        stoch_k=round(cur_k, 2),
        stoch_d=round(cur_d, 2),
        cross=cross,
        direction=direction,
        zone=_zone(cur_k),
    )


def _zone(k_val: float | None) -> Literal["overbought", "oversold", "neutral"]:
    if k_val is None:
        return "neutral"
    if k_val > 80:
        return "overbought"
    if k_val < 20:
        return "oversold"
    return "neutral"


def _compute_stoch_timeframe(bars: pd.DataFrame) -> list[StochSet]:
    """Compute all 3 stochastic param sets for the given timeframe bars."""
    results = []
    for k, d, smooth_k in STOCH_PARAMS:
        results.append(_compute_stoch_set(bars, k, d, smooth_k))
    return results


def _compute_ma(bars: pd.DataFrame) -> dict:
    """Compute SMA 5/20/60/120 alignment and close deviations."""
    close = bars["close"]
    mas = {}
    for p in MA_PERIODS:
        if len(close) >= p:
            mas[p] = float(close.rolling(p).mean().iloc[-1])
        else:
            mas[p] = None

    cur_close = float(close.iloc[-1])
    deviations = {}
    for p in MA_PERIODS:
        if mas[p] is not None:
            deviations[p] = round((cur_close - mas[p]) / mas[p] * 100, 4)
        else:
            deviations[p] = None

    # Alignment: check 5>20>60>120 (정배열) or 120>60>20>5 (역배열)
    available = [(p, mas[p]) for p in MA_PERIODS if mas[p] is not None]
    if len(available) == 4:
        vals = [v for _, v in available]
        if vals[0] > vals[1] > vals[2] > vals[3]:
            alignment = "bullish"   # 정배열
        elif vals[0] < vals[1] < vals[2] < vals[3]:
            alignment = "bearish"   # 역배열
        else:
            alignment = "mixed"
    elif len(available) == 0:
        alignment = "unknown"
    else:
        alignment = "partial"

    return {"alignment": alignment, "deviations": deviations, "values": mas}


def _compute_bb(bars: pd.DataFrame) -> dict:
    """Compute Bollinger Bands (20, 2) position and bandwidth."""
    close = bars["close"]
    if len(close) < 20:
        return {"position": None, "bandwidth": None, "squeeze_expanding": "unknown"}

    bb = ta.bbands(close, length=20, std=2)
    if bb is None:
        return {"position": None, "bandwidth": None, "squeeze_expanding": "unknown"}

    # Find column names (pandas_ta uses "BBL_20_2.0_2.0" format)
    lower_col = next((c for c in bb.columns if c.startswith("BBL")), None)
    mid_col = next((c for c in bb.columns if c.startswith("BBM")), None)
    upper_col = next((c for c in bb.columns if c.startswith("BBU")), None)

    if not all([lower_col, mid_col, upper_col]):
        return {"position": None, "bandwidth": None, "squeeze_expanding": "unknown"}

    lower = float(bb[lower_col].iloc[-1])
    mid = float(bb[mid_col].iloc[-1])
    upper = float(bb[upper_col].iloc[-1])
    cur_close = float(close.iloc[-1])

    band_range = upper - lower
    if band_range > 0:
        position = round((cur_close - lower) / band_range, 4)
    else:
        position = None

    bandwidth = round(band_range / mid * 100, 4) if mid > 0 else None

    # Compare bandwidth to 20-day average bandwidth
    if bandwidth is not None and len(close) >= 40:
        lower_series = bb[lower_col].dropna()
        mid_series = bb[mid_col].dropna()
        upper_series = bb[upper_col].dropna()
        bw_series = (upper_series - lower_series) / mid_series * 100
        avg_bw = float(bw_series.rolling(20).mean().iloc[-1])
        if bandwidth > avg_bw * 1.05:
            squeeze_expanding = "expanding"
        elif bandwidth < avg_bw * 0.95:
            squeeze_expanding = "squeeze"
        else:
            squeeze_expanding = "neutral"
    else:
        squeeze_expanding = "unknown"

    return {
        "position": position,
        "bandwidth": bandwidth,
        "squeeze_expanding": squeeze_expanding,
        "lower": round(lower, 4),
        "mid": round(mid, 4),
        "upper": round(upper, 4),
    }


def _compute_volume_ratio(bars: pd.DataFrame) -> float | None:
    """Compute today's volume / 20-day average volume."""
    volume = bars["volume"]
    if len(volume) < 2:
        return None
    today_vol = float(volume.iloc[-1])
    avg_20 = float(volume.iloc[-21:-1].mean()) if len(volume) >= 21 else float(volume.iloc[:-1].mean())
    if avg_20 > 0:
        return round(today_vol / avg_20, 4)
    return None


def _compute_candle(bars: pd.DataFrame) -> dict:
    """Detect candle patterns using ATR-relative thresholds."""
    if len(bars) < 2:
        return {"pattern": "unknown", "body_ratio": None, "upper_shadow": None, "lower_shadow": None, "gap": "none"}

    # ATR(14) or simple TR average
    n = min(14, len(bars) - 1)
    tr_vals = []
    for i in range(-n, 0):
        row = bars.iloc[i]
        prev_row = bars.iloc[i - 1]
        tr = max(
            float(row["high"]) - float(row["low"]),
            abs(float(row["high"]) - float(prev_row["close"])),
            abs(float(row["low"]) - float(prev_row["close"])),
        )
        tr_vals.append(tr)
    atr = np.mean(tr_vals) if tr_vals else 1.0

    cur = bars.iloc[-1]
    prev = bars.iloc[-2]
    o = float(cur["open"])
    h = float(cur["high"])
    l = float(cur["low"])
    c = float(cur["close"])
    prev_h = float(prev["high"])
    prev_l = float(prev["low"])

    body = abs(c - o)
    total_range = h - l if h > l else 1.0
    upper_shadow = h - max(o, c)
    lower_shadow = min(o, c) - l

    body_ratio = round(body / total_range, 4) if total_range > 0 else 0.0
    upper_shadow_ratio = round(upper_shadow / total_range, 4) if total_range > 0 else 0.0
    lower_shadow_ratio = round(lower_shadow / total_range, 4) if total_range > 0 else 0.0

    # Pattern detection
    if body > 1.5 * atr and c > o:
        pattern = "large_bullish"
    elif body > 1.5 * atr and c < o:
        pattern = "large_bearish"
    elif body < 0.1 * atr:
        pattern = "doji"
    elif c > o:
        pattern = "bullish"
    else:
        pattern = "bearish"

    # Gap detection
    if float(cur["low"]) > prev_h:
        gap = "up"
    elif float(cur["high"]) < prev_l:
        gap = "down"
    else:
        gap = "none"

    return {
        "pattern": pattern,
        "body_ratio": body_ratio,
        "upper_shadow": upper_shadow_ratio,
        "lower_shadow": lower_shadow_ratio,
        "gap": gap,
        "atr": round(atr, 4),
    }


def _prepare_df(ohlcv_df: pd.DataFrame, target_date: date) -> pd.DataFrame:
    """Normalize and slice ohlcv_df up to and including target_date."""
    df = ohlcv_df.copy()

    # Normalize columns to lowercase
    df.columns = [c.lower() for c in df.columns]

    # Ensure datetime index
    if not isinstance(df.index, pd.DatetimeIndex):
        if "date" in df.columns:
            df = df.set_index("date")
        df.index = pd.to_datetime(df.index)

    df = df.sort_index()

    # Slice up to target_date
    target_ts = pd.Timestamp(target_date)
    df = df[df.index <= target_ts]

    return df


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calculate_indicators(ohlcv_df: pd.DataFrame, target_date: date) -> IndicatorResult:
    """Calculate all technical indicators for the given target_date.

    Args:
        ohlcv_df: DataFrame with columns open/high/low/close/volume and a date index.
                  Must include enough history before target_date.
        target_date: The date to calculate indicators for (inclusive).

    Returns:
        IndicatorResult with all computed indicators.
    """
    df = _prepare_df(ohlcv_df, target_date)

    if df.empty:
        raise ValueError(f"No OHLCV data available on or before {target_date}")

    # --- Stochastic ---
    daily_sets = _compute_stoch_timeframe(df) if len(df) >= MIN_ROWS_DAILY else []

    weekly_df = _resample_weekly(df)
    weekly_sets = _compute_stoch_timeframe(weekly_df) if len(weekly_df) >= MIN_ROWS_WEEKLY else []

    monthly_df = _resample_monthly(df)
    monthly_sets = _compute_stoch_timeframe(monthly_df) if len(monthly_df) >= MIN_ROWS_MONTHLY else []

    stochastic = {
        "daily": daily_sets,
        "weekly": weekly_sets,
        "monthly": monthly_sets,
    }

    # --- Moving Averages ---
    ma = _compute_ma(df)

    # --- Bollinger Bands ---
    bb = _compute_bb(df)

    # --- Volume Ratio ---
    volume_ratio = _compute_volume_ratio(df)

    # --- Candle Pattern ---
    candle = _compute_candle(df)

    return IndicatorResult(
        stochastic=stochastic,
        ma=ma,
        bb=bb,
        volume_ratio=volume_ratio,
        candle=candle,
    )


def generate_snapshot_text(indicators: IndicatorResult) -> str:
    """Convert IndicatorResult to human-readable Korean summary text."""
    lines = []

    # --- Stochastic line ---
    def _stoch_summary(sets: list[StochSet], label: str) -> str:
        if not sets:
            return f"{label} 데이터부족"
        rising = sum(1 for s in sets if s.direction == "rising")
        total = len(sets)
        golden = any(s.cross == "golden" for s in sets)
        dead = any(s.cross == "dead" for s in sets)
        zones = [s.zone for s in sets]

        if rising == total:
            dir_str = "상승"
        elif rising == 0:
            dir_str = "하락"
        else:
            dir_str = "혼조"

        cross_str = ""
        if golden:
            cross_str = " 골든크로스"
        elif dead:
            cross_str = " 데드크로스"

        zone_str = ""
        if all(z == "overbought" for z in zones):
            zone_str = " (과매수)"
        elif all(z == "oversold" for z in zones):
            zone_str = " (과매도)"

        return f"{label} {dir_str} {rising}/{total}{cross_str}{zone_str}"

    daily_summary = _stoch_summary(indicators.stochastic.get("daily", []), "일봉")
    weekly_summary = _stoch_summary(indicators.stochastic.get("weekly", []), "주봉")
    monthly_summary = _stoch_summary(indicators.stochastic.get("monthly", []), "월봉")

    # Consistency check
    all_sets = (
        indicators.stochastic.get("daily", [])
        + indicators.stochastic.get("weekly", [])
        + indicators.stochastic.get("monthly", [])
    )
    all_rising = all(s.direction == "rising" for s in all_sets if s.stoch_k is not None)
    all_falling = all(s.direction == "falling" for s in all_sets if s.stoch_k is not None)

    consistency = ""
    if all_rising:
        consistency = " → 방향 일치도 높음"
    elif all_falling:
        consistency = " → 하락 일치도 높음"

    lines.append(f"▸ 스토캐스틱 정렬: {daily_summary} | {weekly_summary} | {monthly_summary}{consistency}")

    # --- MA line ---
    ma = indicators.ma
    alignment_map = {
        "bullish": "정배열",
        "bearish": "역배열",
        "mixed": "혼합",
        "partial": "일부",
        "unknown": "알수없음",
    }
    alignment_str = alignment_map.get(ma.get("alignment", "unknown"), "알수없음")
    dev = ma.get("deviations", {})
    dev20 = dev.get(20)
    if dev20 is not None:
        sign = "+" if dev20 >= 0 else ""
        dev_str = f", 20일선 {'지지' if dev20 >= 0 else '저항'} (괴리율 {sign}{dev20:.1f}%)"
    else:
        dev_str = ""
    lines.append(f"▸ 이평선: {alignment_str}{dev_str}")

    # --- BB line ---
    bb = indicators.bb
    pos = bb.get("position")
    se = bb.get("squeeze_expanding", "unknown")
    if pos is not None:
        if pos > 1.0:
            pos_str = "상단 돌파"
        elif pos > 0.8:
            pos_str = "상단 근접"
        elif pos > 0.5:
            pos_str = "중심선 상향"
        elif pos > 0.2:
            pos_str = "중심선 하향"
        elif pos >= 0.0:
            pos_str = "하단 근접"
        else:
            pos_str = "하단 돌파"
    else:
        pos_str = "알수없음"

    se_map = {
        "expanding": "밴드폭 확장",
        "squeeze": "밴드폭 수축",
        "neutral": "밴드폭 중립",
        "unknown": "",
    }
    se_str = se_map.get(se, "")
    if se_str:
        bb_line = f"{pos_str}, {se_str}"
    else:
        bb_line = pos_str
    lines.append(f"▸ 볼린저: {bb_line}")

    # --- Volume ratio line ---
    vr = indicators.volume_ratio
    if vr is not None:
        lines.append(f"▸ 거래량: 20일 평균 대비 {vr * 100:.0f}%")
    else:
        lines.append("▸ 거래량: 데이터부족")

    # --- Candle line ---
    candle = indicators.candle
    pattern = candle.get("pattern", "unknown")
    gap = candle.get("gap", "none")

    pattern_map = {
        "large_bullish": "장대양봉",
        "large_bearish": "장대음봉",
        "doji": "도지",
        "bullish": "양봉",
        "bearish": "음봉",
        "unknown": "알수없음",
    }
    pattern_str = pattern_map.get(pattern, pattern)

    gap_str = ""
    if gap == "up":
        gap_str = "갭 상승 후 "
    elif gap == "down":
        gap_str = "갭 하락 후 "

    upper_shadow = candle.get("upper_shadow")
    if upper_shadow is not None:
        if upper_shadow < 0.1:
            shadow_str = ", 윗꼬리 짧음"
        elif upper_shadow > 0.3:
            shadow_str = ", 윗꼬리 김"
        else:
            shadow_str = ""
    else:
        shadow_str = ""

    lines.append(f"▸ 캔들: {gap_str}{pattern_str} 마감{shadow_str}")

    return "\n".join(lines)


async def calculate_indicators_for_trade(
    symbol: str,
    traded_at: date,
    session,  # AsyncSession
) -> IndicatorResult:
    """Fetch OHLCV from price_cache and calculate indicators.

    Requires at least 250 days of history to be present in price_cache
    for reliable Stochastic(20,12,12) monthly calculation.
    """
    from sqlalchemy import select
    from db.models import PriceCache

    # Fetch ~500 days of daily data before traded_at (enough for monthly stoch)
    from datetime import timedelta
    start_date = traded_at - timedelta(days=600)

    stmt = (
        select(PriceCache)
        .where(PriceCache.symbol == symbol)
        .where(PriceCache.date >= start_date)
        .where(PriceCache.date <= traded_at)
        .order_by(PriceCache.date)
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()

    if not rows:
        raise ValueError(f"No price data found for {symbol} up to {traded_at}")

    records = [
        {
            "date": row.date,
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
            "volume": int(row.volume),
        }
        for row in rows
    ]
    df = pd.DataFrame(records).set_index("date")
    df.index = pd.to_datetime(df.index)

    return calculate_indicators(df, traded_at)
