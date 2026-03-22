"""Comprehensive tests for trades/indicators.py.

All tests use hard-coded OHLCV DataFrames with known expected values.
No DB connection required — pure unit tests.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from trades.indicators import (
    IndicatorResult,
    StochSet,
    _compute_bb,
    _compute_candle,
    _compute_ma,
    _compute_stoch_set,
    _compute_stoch_timeframe,
    _compute_volume_ratio,
    _prepare_df,
    _resample_monthly,
    _resample_weekly,
    calculate_indicators,
    generate_snapshot_text,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_trending_df(
    n: int = 300,
    start: str = "2023-01-02",
    trend: float = 0.3,
    seed: int = 0,
    noise: float = 0.5,
    base: float = 100.0,
) -> pd.DataFrame:
    """Build a deterministic trending OHLCV DataFrame with business-day index."""
    np.random.seed(seed)
    dates = pd.date_range(start, periods=n, freq="B")
    prices = base + np.arange(n) * trend + np.random.randn(n) * noise
    prices = np.maximum(prices, 1.0)
    close = pd.Series(prices, index=dates)
    high = close + np.abs(np.random.randn(n) * 0.3)
    low = close - np.abs(np.random.randn(n) * 0.3)
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(np.random.randint(1000, 5000, n).astype(float), index=dates)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})


def _make_flat_df(n: int = 200, price: float = 100.0, seed: int = 7) -> pd.DataFrame:
    """Build a flat (sideways) OHLCV DataFrame."""
    np.random.seed(seed)
    dates = pd.date_range("2023-01-02", periods=n, freq="B")
    noise = np.random.randn(n) * 0.2
    close = pd.Series(price + noise, index=dates)
    high = close + 0.5
    low = close - 0.5
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series([2000.0] * n, index=dates)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})


# ---------------------------------------------------------------------------
# Tests: _prepare_df
# ---------------------------------------------------------------------------

class TestPrepareDF:
    def test_filters_to_target_date(self):
        df = _make_trending_df(n=50)
        target = df.index[30].date()
        result = _prepare_df(df, target)
        assert result.index[-1].date() <= target
        assert len(result) == 31  # rows 0..30

    def test_lowercases_columns(self):
        df = _make_trending_df(n=20)
        df.columns = [c.upper() for c in df.columns]
        result = _prepare_df(df, df.index[-1].date())
        assert "close" in result.columns
        assert "CLOSE" not in result.columns

    def test_sorts_index(self):
        df = _make_trending_df(n=30)
        shuffled = df.sample(frac=1, random_state=99)
        target = df.index[-1].date()
        result = _prepare_df(shuffled, target)
        assert result.index.is_monotonic_increasing

    def test_empty_when_no_data_before_target(self):
        df = _make_trending_df(n=10, start="2024-01-02")
        far_past = date(2020, 1, 1)
        result = _prepare_df(df, far_past)
        assert result.empty

    def test_accepts_date_index(self):
        df = _make_trending_df(n=30)
        df.index = [d.date() for d in df.index]
        target = df.index[-1]
        result = _prepare_df(df, target)
        assert isinstance(result.index, pd.DatetimeIndex)


# ---------------------------------------------------------------------------
# Tests: weekly resampling
# ---------------------------------------------------------------------------

class TestResampleWeekly:
    def test_open_is_first_of_week(self):
        """Week's open = first trading day's open."""
        dates = pd.date_range("2024-01-02", periods=5, freq="B")  # Tue-Fri + Mon
        df = pd.DataFrame(
            {
                "open": [10.0, 20.0, 30.0, 40.0, 50.0],
                "high": [11.0, 21.0, 31.0, 41.0, 51.0],
                "low": [9.0, 19.0, 29.0, 39.0, 49.0],
                "close": [10.5, 20.5, 30.5, 40.5, 50.5],
                "volume": [1000.0, 1000.0, 1000.0, 1000.0, 1000.0],
            },
            index=dates,
        )
        weekly = _resample_weekly(df)
        # Jan 2-5 (Tue-Fri) → first open = 10
        assert float(weekly["open"].iloc[0]) == 10.0

    def test_high_is_max(self):
        dates = pd.date_range("2024-01-02", periods=5, freq="B")
        df = pd.DataFrame(
            {
                "open": [10.0] * 5,
                "high": [11.0, 25.0, 12.0, 13.0, 14.0],
                "low": [9.0] * 5,
                "close": [10.5] * 5,
                "volume": [1000.0] * 5,
            },
            index=dates,
        )
        weekly = _resample_weekly(df)
        assert float(weekly["high"].iloc[0]) == 25.0

    def test_low_is_min(self):
        dates = pd.date_range("2024-01-02", periods=5, freq="B")
        df = pd.DataFrame(
            {
                "open": [10.0] * 5,
                "high": [11.0] * 5,
                "low": [9.0, 3.0, 8.0, 9.0, 9.0],
                "close": [10.5] * 5,
                "volume": [1000.0] * 5,
            },
            index=dates,
        )
        weekly = _resample_weekly(df)
        assert float(weekly["low"].iloc[0]) == 3.0

    def test_close_is_last_of_week(self):
        dates = pd.date_range("2024-01-02", periods=5, freq="B")  # Tue-Fri + Mon
        df = pd.DataFrame(
            {
                "open": [10.0] * 5,
                "high": [11.0] * 5,
                "low": [9.0] * 5,
                "close": [10.0, 20.0, 30.0, 99.0, 50.0],  # Jan 5 (Fri) = 99.0 → week 1 close
                "volume": [1000.0] * 5,
            },
            index=dates,
        )
        weekly = _resample_weekly(df)
        # Jan 5 is Friday → last day of week 1
        assert float(weekly["close"].iloc[0]) == 99.0

    def test_volume_is_sum(self):
        dates = pd.date_range("2024-01-02", periods=5, freq="B")
        df = pd.DataFrame(
            {
                "open": [10.0] * 5,
                "high": [11.0] * 5,
                "low": [9.0] * 5,
                "close": [10.5] * 5,
                "volume": [100.0, 200.0, 300.0, 400.0, 500.0],
            },
            index=dates,
        )
        weekly = _resample_weekly(df)
        # Tue Jan 2 – Fri Jan 5 → volumes 100+200+300+400=1000
        assert float(weekly["volume"].iloc[0]) == 1000.0

    def test_15_business_days_gives_3_weeks(self):
        df = pd.DataFrame(
            {
                "open": [100.0 + i for i in range(15)],
                "high": [101.0 + i for i in range(15)],
                "low": [99.0 + i for i in range(15)],
                "close": [100.5 + i for i in range(15)],
                "volume": [1000.0 + i * 100 for i in range(15)],
            },
            index=pd.date_range("2024-01-02", periods=15, freq="B"),
        )
        weekly = _resample_weekly(df)
        # Jan 2-5 (4 days), Jan 8-12 (5 days), Jan 15-19 (5 days), Jan 22 (1 day→partial)
        assert len(weekly) >= 3


# ---------------------------------------------------------------------------
# Tests: monthly resampling
# ---------------------------------------------------------------------------

class TestResampleMonthly:
    def test_open_is_first_of_month(self):
        dates = pd.date_range("2024-01-02", periods=45, freq="B")
        open_vals = [float(i + 1) for i in range(45)]
        df = pd.DataFrame(
            {
                "open": open_vals,
                "high": [v + 1 for v in open_vals],
                "low": [v - 1 for v in open_vals],
                "close": [v + 0.5 for v in open_vals],
                "volume": [1000.0] * 45,
            },
            index=dates,
        )
        monthly = _resample_monthly(df)
        # January's open should be the first trading day (Jan 2) → open=1.0
        assert float(monthly["open"].iloc[0]) == 1.0

    def test_close_is_last_of_month(self):
        dates = pd.date_range("2024-01-02", periods=45, freq="B")
        close_vals = [float(i + 1) for i in range(45)]
        df = pd.DataFrame(
            {
                "open": close_vals,
                "high": [v + 1 for v in close_vals],
                "low": [v - 1 for v in close_vals],
                "close": close_vals,
                "volume": [1000.0] * 45,
            },
            index=dates,
        )
        monthly = _resample_monthly(df)
        # First month has 22 business days in Jan 2024 → close = 22.0
        assert float(monthly["close"].iloc[0]) == 22.0

    def test_volume_is_sum_of_month(self):
        # Jan 2024 has 22 business days (Jan 2 – Jan 31)
        dates = pd.date_range("2024-01-02", periods=22, freq="B")
        assert all(d.month == 1 for d in dates), "All 22 dates should be in January"
        df = pd.DataFrame(
            {
                "open": [100.0] * 22,
                "high": [101.0] * 22,
                "low": [99.0] * 22,
                "close": [100.5] * 22,
                "volume": [500.0] * 22,
            },
            index=dates,
        )
        monthly = _resample_monthly(df)
        assert float(monthly["volume"].iloc[0]) == 22 * 500.0

    def test_60_biz_days_gives_multiple_months(self):
        df = _make_trending_df(n=60)
        monthly = _resample_monthly(df)
        assert len(monthly) >= 2


# ---------------------------------------------------------------------------
# Tests: Stochastic computation
# ---------------------------------------------------------------------------

class TestStochSet:
    def test_golden_cross_detected(self):
        """When %K crosses above %D, cross == 'golden'."""
        # Deterministic sinusoidal prices — no random component.
        import pandas_ta as _ta
        n = 100
        dates = pd.date_range("2023-01-02", periods=n, freq="B")
        prices = 100 + np.sin(np.linspace(0, 4 * np.pi, n)) * 10 + np.linspace(0, 5, n)
        close = pd.Series(prices, index=dates)
        high = close + 0.5
        low = close - 0.5
        open_ = close.shift(1).fillna(close.iloc[0])
        volume = pd.Series([1000.0] * n, index=dates)
        df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})

        # Programmatically find the first golden cross in this deterministic dataset.
        stoch = _ta.stoch(high, low, close, k=5, d=3, smooth_k=3)
        k_col, d_col = "STOCHk_5_3_3", "STOCHd_5_3_3"
        valid = stoch[[k_col, d_col]].dropna()
        golden_idx = None
        for i in range(1, len(valid)):
            if valid[k_col].iloc[i - 1] < valid[d_col].iloc[i - 1] and valid[k_col].iloc[i] > valid[d_col].iloc[i]:
                golden_idx = i
                break
        assert golden_idx is not None, "No golden cross found in test dataset"
        cutoff = valid.index[golden_idx]
        result = _compute_stoch_set(df.loc[:cutoff], 5, 3, 3)
        assert result.cross == "golden"

    def test_dead_cross_detected(self):
        """When %K crosses below %D, cross == 'dead'."""
        # Deterministic sinusoidal prices — no random component.
        n = 100
        dates = pd.date_range("2023-01-02", periods=n, freq="B")
        prices = 100 + np.sin(np.linspace(0, 4 * np.pi, n)) * 10 + np.linspace(0, 5, n)
        close = pd.Series(prices, index=dates)
        high = close + 0.5
        low = close - 0.5
        open_ = close.shift(1).fillna(close.iloc[0])
        volume = pd.Series([1000.0] * n, index=dates)
        df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})

        import pandas_ta as ta
        stoch = ta.stoch(high, low, close, k=5, d=3, smooth_k=3)
        k_col = "STOCHk_5_3_3"
        d_col = "STOCHd_5_3_3"
        valid = stoch[[k_col, d_col]].dropna()

        # Find a dead cross index
        dead_idx = None
        for i in range(1, len(valid)):
            if valid[k_col].iloc[i - 1] > valid[d_col].iloc[i - 1] and valid[k_col].iloc[i] < valid[d_col].iloc[i]:
                dead_idx = i
                break

        if dead_idx is None:
            pytest.skip("No dead cross found in this dataset")

        cutoff = valid.index[dead_idx]
        subset = df.loc[:cutoff]
        result = _compute_stoch_set(subset, 5, 3, 3)
        assert result.cross == "dead"

    def test_direction_rising(self):
        """Price in the rising portion of a fast oscillation → stoch direction == 'rising'."""
        # Deterministic sinusoidal prices (period=10) that end during a rising phase.
        # n=76 verified to produce direction='rising' for stoch(5,3,3).
        n = 76
        dates = pd.date_range("2023-01-02", periods=n, freq="B")
        cycles = np.arange(n)
        prices = 100 + 40 * np.sin(cycles * 2 * np.pi / 10 + np.pi)
        close = pd.Series(prices, index=dates)
        high = close + 1
        low = close - 1
        open_ = close.shift(1).fillna(close.iloc[0])
        volume = pd.Series([1000.0] * n, index=dates)
        df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})
        result = _compute_stoch_set(df, 5, 3, 3)
        assert result.direction == "rising"

    def test_direction_falling(self):
        """Price in the falling portion of a fast oscillation → stoch direction == 'falling'."""
        # Deterministic sinusoidal prices (period=10) that end during a falling phase.
        # n=71 verified to produce direction='falling' for stoch(5,3,3).
        n = 71
        dates = pd.date_range("2023-01-02", periods=n, freq="B")
        cycles = np.arange(n)
        prices = 100 + 40 * np.sin(cycles * 2 * np.pi / 10 + np.pi)
        close = pd.Series(prices, index=dates)
        high = close + 1
        low = close - 1
        open_ = close.shift(1).fillna(close.iloc[0])
        volume = pd.Series([1000.0] * n, index=dates)
        df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})
        result = _compute_stoch_set(df, 5, 3, 3)
        assert result.direction == "falling"

    def test_overbought_zone_high_price(self):
        """When price is near 52-week high consistently, stoch %K > 80."""
        np.random.seed(5)
        n = 60
        dates = pd.date_range("2023-01-02", periods=n, freq="B")
        # Very strong uptrend → stoch near 100
        prices = np.linspace(100, 200, n)
        close = pd.Series(prices, index=dates)
        high = close + 0.2
        low = close - 0.2
        open_ = close.shift(1).fillna(close.iloc[0])
        volume = pd.Series([1000.0] * n, index=dates)
        df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})
        result = _compute_stoch_set(df, 5, 3, 3)
        assert result.zone == "overbought"
        assert result.stoch_k is not None and result.stoch_k > 80

    def test_oversold_zone_low_price(self):
        """When price is near 52-week low consistently, stoch %K < 20."""
        np.random.seed(6)
        n = 60
        dates = pd.date_range("2023-01-02", periods=n, freq="B")
        # Very strong downtrend
        prices = np.linspace(200, 100, n)
        close = pd.Series(prices, index=dates)
        high = close + 0.2
        low = close - 0.2
        open_ = close.shift(1).fillna(close.iloc[0])
        volume = pd.Series([1000.0] * n, index=dates)
        df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})
        result = _compute_stoch_set(df, 5, 3, 3)
        assert result.zone == "oversold"
        assert result.stoch_k is not None and result.stoch_k < 20

    def test_returns_none_values_when_too_few_bars(self):
        """With only 3 bars, stoch cannot be computed → returns None values."""
        dates = pd.date_range("2023-01-02", periods=3, freq="B")
        df = pd.DataFrame(
            {
                "open": [100.0, 101.0, 102.0],
                "high": [101.0, 102.0, 103.0],
                "low": [99.0, 100.0, 101.0],
                "close": [100.5, 101.5, 102.5],
                "volume": [1000.0, 1000.0, 1000.0],
            },
            index=dates,
        )
        result = _compute_stoch_set(df, 5, 3, 3)
        assert result.stoch_k is None or result.stoch_d is None


class TestStochTimeframe:
    def test_returns_3_sets(self):
        df = _make_trending_df(n=300)
        sets = _compute_stoch_timeframe(df)
        assert len(sets) == 3

    def test_param_sets_are_5_3_3__10_6_6__20_12_12(self):
        df = _make_trending_df(n=300)
        sets = _compute_stoch_timeframe(df)
        assert (sets[0].k, sets[0].d, sets[0].smooth_k) == (5, 3, 3)
        assert (sets[1].k, sets[1].d, sets[1].smooth_k) == (10, 6, 6)
        assert (sets[2].k, sets[2].d, sets[2].smooth_k) == (20, 12, 12)

    def test_all_9_sets_via_calculate_indicators(self):
        df = _make_trending_df(n=300)
        target = df.index[-1].date()
        result = calculate_indicators(df, target)
        assert len(result.stochastic["daily"]) == 3
        assert len(result.stochastic["weekly"]) == 3
        assert len(result.stochastic["monthly"]) == 3


# ---------------------------------------------------------------------------
# Tests: Moving Averages
# ---------------------------------------------------------------------------

class TestMovingAverages:
    def test_bullish_alignment(self):
        """Strongly trending up → 5>20>60>120 → alignment=='bullish'."""
        df = _make_trending_df(n=300, trend=0.3, seed=0)
        ma = _compute_ma(df)
        assert ma["alignment"] == "bullish"

    def test_bearish_alignment(self):
        """Strongly trending down → 5<20<60<120 → alignment=='bearish'."""
        df = _make_trending_df(n=200, trend=-0.4, seed=1, base=200.0)
        ma = _compute_ma(df)
        assert ma["alignment"] == "bearish"

    def test_deviations_all_present_with_300_bars(self):
        df = _make_trending_df(n=300)
        ma = _compute_ma(df)
        devs = ma["deviations"]
        for p in [5, 20, 60, 120]:
            assert p in devs
            assert devs[p] is not None

    def test_positive_deviation_when_above_ma(self):
        """Rising trend → close is above all MAs → positive deviations."""
        df = _make_trending_df(n=300, trend=0.3, seed=0)
        ma = _compute_ma(df)
        assert ma["deviations"][20] > 0
        assert ma["deviations"][60] > 0
        assert ma["deviations"][120] > 0

    def test_negative_deviation_when_below_ma(self):
        """Falling trend → close is below all MAs → negative deviations."""
        df = _make_trending_df(n=200, trend=-0.4, seed=1, base=200.0)
        ma = _compute_ma(df)
        assert ma["deviations"][20] < 0
        assert ma["deviations"][60] < 0

    def test_deviation_formula(self):
        """Verify (close - MA) / MA * 100 formula."""
        n = 30
        dates = pd.date_range("2024-01-02", periods=n, freq="B")
        # All prices identical → MA = close → deviation = 0
        df = pd.DataFrame(
            {
                "open": [100.0] * n,
                "high": [101.0] * n,
                "low": [99.0] * n,
                "close": [100.0] * n,
                "volume": [1000.0] * n,
            },
            index=dates,
        )
        ma = _compute_ma(df)
        assert abs(ma["deviations"][5]) < 0.01
        assert abs(ma["deviations"][20]) < 0.01

    def test_ma120_none_when_fewer_than_120_bars(self):
        df = _make_trending_df(n=50)
        ma = _compute_ma(df)
        assert ma["deviations"][120] is None

    def test_partial_alignment_when_some_mas_missing(self):
        df = _make_trending_df(n=50)
        ma = _compute_ma(df)
        assert ma["alignment"] in ("partial", "bullish", "bearish", "mixed")


# ---------------------------------------------------------------------------
# Tests: Bollinger Bands
# ---------------------------------------------------------------------------

class TestBollingerBands:
    def test_position_between_0_and_1_at_middle(self):
        """Flat price → close near midband → position ~0.5."""
        df = _make_flat_df(n=100, price=100.0, seed=7)
        bb = _compute_bb(df)
        assert bb["position"] is not None
        # Near middle for flat series
        assert 0.2 <= bb["position"] <= 0.8

    def test_position_above_1_when_price_breaks_upper(self):
        """If close > upper band, position > 1.0 on the exact breakout bar."""
        n = 21  # 20 flat bars + 1 sudden jump
        dates = pd.date_range("2024-01-02", periods=n, freq="B")
        # All prices flat at 100 for 20 days, then one huge jump to 500
        closes = [100.0] * 20 + [500.0]
        df = pd.DataFrame(
            {
                "open": closes,
                "high": [c + 0.5 for c in closes],
                "low": [c - 0.5 for c in closes],
                "close": closes,
                "volume": [1000.0] * n,
            },
            index=dates,
        )
        bb = _compute_bb(df)
        assert bb["position"] is not None
        # On the breakout bar, close (500) far exceeds upper band (~101)
        assert bb["position"] > 1.0

    def test_bandwidth_positive(self):
        df = _make_trending_df(n=100)
        bb = _compute_bb(df)
        assert bb["bandwidth"] is not None and bb["bandwidth"] > 0

    def test_returns_none_when_too_few_bars(self):
        df = _make_trending_df(n=10)
        bb = _compute_bb(df)
        assert bb["position"] is None

    def test_squeeze_expanding_field_present(self):
        df = _make_trending_df(n=200)
        bb = _compute_bb(df)
        assert "squeeze_expanding" in bb
        assert bb["squeeze_expanding"] in ("squeeze", "expanding", "neutral", "unknown")

    def test_band_values_present(self):
        df = _make_trending_df(n=100)
        bb = _compute_bb(df)
        assert "lower" in bb
        assert "mid" in bb
        assert "upper" in bb
        assert bb["upper"] > bb["mid"] > bb["lower"]


# ---------------------------------------------------------------------------
# Tests: Volume Ratio
# ---------------------------------------------------------------------------

class TestVolumeRatio:
    def test_ratio_double_when_volume_doubled(self):
        """If today's volume = 2× the prior 20-day average, ratio ≈ 2.0."""
        n = 30
        dates = pd.date_range("2024-01-02", periods=n, freq="B")
        volumes = [1000.0] * (n - 1) + [2000.0]  # last day = 2×
        df = pd.DataFrame(
            {
                "open": [100.0] * n,
                "high": [101.0] * n,
                "low": [99.0] * n,
                "close": [100.5] * n,
                "volume": volumes,
            },
            index=dates,
        )
        vr = _compute_volume_ratio(df)
        assert vr is not None
        assert abs(vr - 2.0) < 0.1

    def test_ratio_about_1_when_same_as_average(self):
        n = 30
        dates = pd.date_range("2024-01-02", periods=n, freq="B")
        df = pd.DataFrame(
            {
                "open": [100.0] * n,
                "high": [101.0] * n,
                "low": [99.0] * n,
                "close": [100.5] * n,
                "volume": [1000.0] * n,
            },
            index=dates,
        )
        vr = _compute_volume_ratio(df)
        assert vr is not None
        assert abs(vr - 1.0) < 0.05

    def test_none_with_single_bar(self):
        dates = pd.date_range("2024-01-02", periods=1, freq="B")
        df = pd.DataFrame(
            {"open": [100.0], "high": [101.0], "low": [99.0], "close": [100.5], "volume": [1000.0]},
            index=dates,
        )
        vr = _compute_volume_ratio(df)
        assert vr is None

    def test_positive_ratio(self):
        df = _make_trending_df(n=30)
        vr = _compute_volume_ratio(df)
        assert vr is not None and vr > 0


# ---------------------------------------------------------------------------
# Tests: Candle Pattern
# ---------------------------------------------------------------------------

class TestCandlePattern:
    def _single_candle_df(
        self,
        prev_open=100.0, prev_high=101.0, prev_low=99.0, prev_close=100.5,
        cur_open=100.0, cur_high=105.0, cur_low=99.0, cur_close=104.0,
        prev_volume=1000.0, cur_volume=1000.0,
    ):
        dates = pd.date_range("2024-01-02", periods=2, freq="B")
        return pd.DataFrame(
            {
                "open": [prev_open, cur_open],
                "high": [prev_high, cur_high],
                "low": [prev_low, cur_low],
                "close": [prev_close, cur_close],
                "volume": [prev_volume, cur_volume],
            },
            index=dates,
        )

    def test_large_bullish_pattern(self):
        """Body > 1.5×ATR, close > open → large_bullish."""
        # Use a long series with small ATR, then a huge bullish day
        n = 20
        dates = pd.date_range("2024-01-02", periods=n, freq="B")
        closes = [100.0] * (n - 1) + [120.0]  # big jump last day
        opens = [100.0] * (n - 1) + [100.0]
        highs = [100.5] * (n - 1) + [121.0]
        lows = [99.5] * (n - 1) + [99.5]
        volumes = [1000.0] * n
        df = pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
            index=dates,
        )
        result = _compute_candle(df)
        assert result["pattern"] == "large_bullish"

    def test_large_bearish_pattern(self):
        """Body > 1.5×ATR, close < open → large_bearish."""
        n = 20
        dates = pd.date_range("2024-01-02", periods=n, freq="B")
        closes = [100.0] * (n - 1) + [80.0]
        opens = [100.0] * (n - 1) + [100.0]
        highs = [100.5] * (n - 1) + [100.5]
        lows = [99.5] * (n - 1) + [79.0]
        volumes = [1000.0] * n
        df = pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
            index=dates,
        )
        result = _compute_candle(df)
        assert result["pattern"] == "large_bearish"

    def test_doji_pattern(self):
        """Body < 0.1×ATR → doji."""
        n = 20
        dates = pd.date_range("2024-01-02", periods=n, freq="B")
        closes = [100.0] * (n - 1) + [100.01]  # tiny body
        opens = [100.0] * (n - 1) + [100.0]
        highs = [101.0] * (n - 1) + [103.0]   # wide range but tiny body
        lows = [99.0] * (n - 1) + [97.0]
        volumes = [1000.0] * n
        df = pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
            index=dates,
        )
        result = _compute_candle(df)
        assert result["pattern"] == "doji"

    def test_gap_up_detected(self):
        """Today's low > prev high → gap == 'up'."""
        dates = pd.date_range("2024-01-02", periods=2, freq="B")
        df = pd.DataFrame(
            {
                "open": [100.0, 105.0],
                "high": [101.0, 108.0],
                "low": [99.0, 105.0],   # today low (105) > prev high (101) → gap up
                "close": [100.5, 107.0],
                "volume": [1000.0, 1000.0],
            },
            index=dates,
        )
        result = _compute_candle(df)
        assert result["gap"] == "up"

    def test_gap_down_detected(self):
        """Today's high < prev low → gap == 'down'."""
        dates = pd.date_range("2024-01-02", periods=2, freq="B")
        df = pd.DataFrame(
            {
                "open": [100.0, 90.0],
                "high": [101.0, 93.0],  # today high (93) < prev low (99) → gap down
                "low": [99.0, 89.0],
                "close": [100.5, 91.0],
                "volume": [1000.0, 1000.0],
            },
            index=dates,
        )
        result = _compute_candle(df)
        assert result["gap"] == "down"

    def test_no_gap(self):
        """Normal overlapping bars → gap == 'none'."""
        dates = pd.date_range("2024-01-02", periods=2, freq="B")
        df = pd.DataFrame(
            {
                "open": [100.0, 100.5],
                "high": [101.0, 102.0],
                "low": [99.0, 99.5],
                "close": [100.5, 101.0],
                "volume": [1000.0, 1000.0],
            },
            index=dates,
        )
        result = _compute_candle(df)
        assert result["gap"] == "none"

    def test_body_ratio_and_shadows_sum_to_roughly_1(self):
        """body_ratio + upper_shadow + lower_shadow ≈ 1.0."""
        dates = pd.date_range("2024-01-02", periods=5, freq="B")
        df = pd.DataFrame(
            {
                "open": [100.0, 101.0, 102.0, 103.0, 104.0],
                "high": [102.0, 103.0, 104.0, 105.0, 107.0],
                "low": [99.0, 100.0, 101.0, 102.0, 103.0],
                "close": [101.5, 102.5, 103.5, 104.5, 105.5],
                "volume": [1000.0] * 5,
            },
            index=dates,
        )
        result = _compute_candle(df)
        total = result["body_ratio"] + result["upper_shadow"] + result["lower_shadow"]
        assert abs(total - 1.0) < 0.01

    def test_returns_unknown_with_single_bar(self):
        dates = pd.date_range("2024-01-02", periods=1, freq="B")
        df = pd.DataFrame(
            {"open": [100.0], "high": [101.0], "low": [99.0], "close": [100.5], "volume": [1000.0]},
            index=dates,
        )
        result = _compute_candle(df)
        assert result["pattern"] == "unknown"


# ---------------------------------------------------------------------------
# Tests: calculate_indicators (integration)
# ---------------------------------------------------------------------------

class TestCalculateIndicators:
    def test_raises_for_empty_slice(self):
        df = _make_trending_df(n=10, start="2024-01-02")
        with pytest.raises(ValueError, match="No OHLCV data"):
            calculate_indicators(df, date(2020, 1, 1))

    def test_returns_indicator_result(self):
        df = _make_trending_df(n=300)
        target = df.index[-1].date()
        result = calculate_indicators(df, target)
        assert isinstance(result, IndicatorResult)

    def test_stochastic_has_all_3_timeframes(self):
        df = _make_trending_df(n=300)
        target = df.index[-1].date()
        result = calculate_indicators(df, target)
        assert "daily" in result.stochastic
        assert "weekly" in result.stochastic
        assert "monthly" in result.stochastic

    def test_daily_stoch_has_3_sets(self):
        df = _make_trending_df(n=300)
        target = df.index[-1].date()
        result = calculate_indicators(df, target)
        assert len(result.stochastic["daily"]) == 3

    def test_weekly_stoch_has_3_sets(self):
        df = _make_trending_df(n=300)
        target = df.index[-1].date()
        result = calculate_indicators(df, target)
        assert len(result.stochastic["weekly"]) == 3

    def test_monthly_stoch_has_3_sets(self):
        df = _make_trending_df(n=300)
        target = df.index[-1].date()
        result = calculate_indicators(df, target)
        assert len(result.stochastic["monthly"]) == 3

    def test_ma_alignment_bullish_for_uptrend(self):
        df = _make_trending_df(n=300, trend=0.3, seed=0)
        target = df.index[-1].date()
        result = calculate_indicators(df, target)
        assert result.ma["alignment"] == "bullish"

    def test_ma_alignment_bearish_for_downtrend(self):
        df = _make_trending_df(n=200, trend=-0.4, seed=1, base=200.0)
        target = df.index[-1].date()
        result = calculate_indicators(df, target)
        assert result.ma["alignment"] == "bearish"

    def test_volume_ratio_positive(self):
        df = _make_trending_df(n=100)
        target = df.index[-1].date()
        result = calculate_indicators(df, target)
        assert result.volume_ratio is not None and result.volume_ratio > 0

    def test_bb_position_present(self):
        df = _make_trending_df(n=100)
        target = df.index[-1].date()
        result = calculate_indicators(df, target)
        assert result.bb["position"] is not None

    def test_candle_pattern_present(self):
        df = _make_trending_df(n=100)
        target = df.index[-1].date()
        result = calculate_indicators(df, target)
        assert result.candle["pattern"] in (
            "large_bullish", "large_bearish", "doji", "bullish", "bearish"
        )

    def test_slices_at_target_date(self):
        """Indicators for an earlier date use only data up to that date."""
        df = _make_trending_df(n=300)
        early_target = df.index[150].date()
        result = calculate_indicators(df, early_target)
        # MA20 with only 151 bars should still be computable
        assert result.ma["deviations"][20] is not None

    def test_stoch_cross_attribute_valid_value(self):
        df = _make_trending_df(n=300)
        target = df.index[-1].date()
        result = calculate_indicators(df, target)
        for s in result.stochastic["daily"]:
            assert s.cross in ("golden", "dead", "none")

    def test_stoch_direction_valid_value(self):
        df = _make_trending_df(n=300)
        target = df.index[-1].date()
        result = calculate_indicators(df, target)
        for s in result.stochastic["daily"]:
            assert s.direction in ("rising", "falling", "sideways")

    def test_stoch_zone_valid_value(self):
        df = _make_trending_df(n=300)
        target = df.index[-1].date()
        result = calculate_indicators(df, target)
        for s in result.stochastic["daily"]:
            assert s.zone in ("overbought", "oversold", "neutral")

    def test_daily_stoch_empty_when_fewer_than_min_rows_daily(self):
        """With fewer than MIN_ROWS_DAILY rows, daily stochastic sets must be []."""
        df = _make_trending_df(n=10)
        target = df.index[-1].date()
        result = calculate_indicators(df, target)
        assert result.stochastic["daily"] == []


# ---------------------------------------------------------------------------
# Tests: generate_snapshot_text
# ---------------------------------------------------------------------------

class TestGenerateSnapshotText:
    def _get_snapshot(self, n: int = 300, trend: float = 0.3, seed: int = 0) -> str:
        df = _make_trending_df(n=n, trend=trend, seed=seed)
        target = df.index[-1].date()
        result = calculate_indicators(df, target)
        return generate_snapshot_text(result)

    def test_contains_stochastic_line(self):
        text = self._get_snapshot()
        assert "스토캐스틱" in text

    def test_contains_ma_line(self):
        text = self._get_snapshot()
        assert "이평선" in text

    def test_contains_bollinger_line(self):
        text = self._get_snapshot()
        assert "볼린저" in text

    def test_contains_volume_line(self):
        text = self._get_snapshot()
        assert "거래량" in text

    def test_contains_candle_line(self):
        text = self._get_snapshot()
        assert "캔들" in text

    def test_has_5_lines(self):
        text = self._get_snapshot()
        lines = [l for l in text.split("\n") if l.strip()]
        assert len(lines) == 5

    def test_all_lines_start_with_bullet(self):
        text = self._get_snapshot()
        for line in text.split("\n"):
            if line.strip():
                assert line.startswith("▸"), f"Line doesn't start with ▸: {line!r}"

    def test_bullish_stoch_shows_daily_jeonbaeyeol(self):
        """Rising trend → alignment text includes '정배열'."""
        text = self._get_snapshot(n=300, trend=0.3)
        assert "정배열" in text

    def test_bearish_stoch_shows_yeokbaeyeol(self):
        """Falling trend → alignment text includes '역배열'."""
        text = self._get_snapshot(n=200, trend=-0.4, seed=1)
        assert "역배열" in text

    def test_volume_ratio_shown_as_percentage(self):
        text = self._get_snapshot()
        assert "%" in text

    def test_stochastic_shows_daily_weekly_monthly(self):
        text = self._get_snapshot()
        stoch_line = [l for l in text.split("\n") if "스토캐스틱" in l][0]
        assert "일봉" in stoch_line
        assert "주봉" in stoch_line
        assert "월봉" in stoch_line

    def test_gap_up_shown_in_candle_line(self):
        """Force a gap-up candle in last bar."""
        n = 100
        dates = pd.date_range("2024-01-02", periods=n, freq="B")
        np.random.seed(0)
        prices = 100 + np.arange(n) * 0.3
        closes = list(prices)
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        opens = closes[:]
        # Make last bar a gap up: low > prev high
        highs[-1] = closes[-2] + 10.0
        lows[-1] = closes[-2] + 5.0   # low > prev high (prev high = closes[-2]+0.5)
        closes[-1] = closes[-2] + 8.0
        opens[-1] = closes[-2] + 5.5
        volumes = [1000.0] * n

        df = pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
            index=dates,
        )
        target = df.index[-1].date()
        result = calculate_indicators(df, target)
        text = generate_snapshot_text(result)
        candle_line = [l for l in text.split("\n") if "캔들" in l][0]
        assert "갭 상승" in candle_line

    def test_snapshot_returns_string(self):
        text = self._get_snapshot()
        assert isinstance(text, str)
        assert len(text) > 50
