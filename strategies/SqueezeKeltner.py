"""
SqueezeKeltner - TTM volatility squeeze breakout strategy (1h, 4h trend filter).

Idea: when Bollinger Bands (20, 2.0) contract inside the Keltner Channel (20 EMA +/- k*ATR)
volatility is compressed ("squeeze on"). When the bands expand back outside the channel
("squeeze release") after a minimum number of squeezed candles, price closes above the upper
Keltner band on expanding volume, and the TTM momentum oscillator (linear regression of price
minus its mid-range, normalised by ATR) is positive and rising, we enter long - but only while
the 4h close sits above a rising 4h EMA (trend filter) and 1h RSI is not already overbought.
The position is closed when momentum fades (N consecutive declining bars while momentum has
decayed below a fraction of its recent peak, or momentum crossing below zero), or by the
trailing stop / fixed stoploss / ROI table. Spot only, no lookahead.
"""

import numpy as np
import pandas as pd
from pandas import DataFrame

import talib.abstract as ta
from technical import qtpylib

from freqtrade.strategy import (
    IStrategy,
    informative,
    CategoricalParameter,
    DecimalParameter,
    IntParameter,
)


class SqueezeKeltner(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short: bool = False
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # ROI keys are minutes
    minimal_roi = {
        "0": 0.12,
        "1440": 0.06,
        "2880": 0.03,
    }

    stoploss = -0.07

    trailing_stop = True
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.05
    trailing_only_offset_is_reached = True

    use_custom_stoploss = False

    # Longest lookback on 1h is small (20-period BB/KC/linreg). startup_candle_count is also
    # applied in 4h units to the informative frame; recursive-analysis showed ema100_4h still
    # off by ~0.05% with 200 startup candles and fully converged from 399+, so use 400.
    startup_candle_count: int = 400

    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }
    order_time_in_force = {
        "entry": "GTC",
        "exit": "GTC",
    }

    # ---- Hyperopt parameters (buy space) ----
    # Keltner channel width multiplier (x ATR). Wider KC => tighter squeeze definition.
    buy_kc_mult = DecimalParameter(1.0, 2.5, default=1.5, decimals=1, space="buy", optimize=True)
    # Minimum number of consecutive squeezed candles before a release is considered valid.
    buy_sq_min_bars = IntParameter(3, 12, default=6, space="buy", optimize=True)
    # Minimum TTM momentum (normalised by ATR) at entry.
    buy_mom_min = DecimalParameter(0.0, 1.0, default=0.2, decimals=2, space="buy", optimize=True)
    # Volume confirmation: candle volume must exceed this multiple of its 20-bar average.
    buy_vol_mult = DecimalParameter(1.0, 2.5, default=1.3, decimals=1, space="buy", optimize=True)
    # 4h EMA length used as trend filter (close_4h must be above it).
    buy_trend_ema_4h = CategoricalParameter([21, 50, 100], default=50, space="buy", optimize=True)
    # Do not chase: 1h RSI must be below this at entry.
    buy_rsi_max = IntParameter(60, 85, default=75, space="buy", optimize=True)

    # ---- Hyperopt parameters (sell space) ----
    # Momentum fade: exit when momentum has declined for this many consecutive bars ...
    sell_fade_bars = IntParameter(1, 4, default=2, space="sell", optimize=True)
    # ... AND has decayed below this fraction of its recent (24-bar) peak.
    sell_fade_ratio = DecimalParameter(0.2, 0.8, default=0.5, decimals=2, space="sell", optimize=True)
    # Fixed (non-optimised) structural settings
    release_window: int = 2  # entry allowed within this many candles after the release candle

    @property
    def plot_config(self):
        return {
            "main_plot": {
                "bb_upper": {"color": "grey"},
                "bb_lower": {"color": "grey"},
                "kc_mid": {"color": "orange"},
            },
            "subplots": {
                "MOM": {"mom_atr": {"color": "blue"}},
                "RSI": {"rsi": {"color": "red"}},
            },
        }

    # ------------------------------------------------------------------ informative 4h
    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        for length in (21, 50, 100):
            ema = ta.EMA(dataframe, timeperiod=length)
            dataframe[f"ema{length}"] = ema
            # slope computed on the 4h frame itself (previous 4h candle -> no lookahead)
            dataframe[f"ema{length}_up"] = (ema > ema.shift(1)).astype(int)
        return dataframe

    # ------------------------------------------------------------------ indicators 1h
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        length = 20

        # Bollinger Bands (20, 2.0)
        bb = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=length, stds=2.0)
        dataframe["bb_lower"] = bb["lower"]
        dataframe["bb_mid"] = bb["mid"]
        dataframe["bb_upper"] = bb["upper"]

        # Keltner channel base: EMA(20) and ATR(20); multiplier applied at signal time
        dataframe["kc_mid"] = ta.EMA(dataframe, timeperiod=length)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=length)

        # TTM momentum (LazyBear): linreg(close - avg(avg(HH, LL), SMA(close)), 20)
        hh = dataframe["high"].rolling(length).max()
        ll = dataframe["low"].rolling(length).min()
        sma = ta.SMA(dataframe, timeperiod=length)
        base = dataframe["close"] - ((hh + ll) / 2.0 + sma) / 2.0
        dataframe["mom"] = ta.LINEARREG(base.astype(float), timeperiod=length)
        dataframe["mom_atr"] = dataframe["mom"] / dataframe["atr"].replace(0, np.nan)

        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["vol_sma"] = dataframe["volume"].rolling(length).mean()

        return dataframe

    # ------------------------------------------------------------------ helpers
    def _squeeze_state(self, dataframe: DataFrame, kc_mult: float):
        kc_upper = dataframe["kc_mid"] + kc_mult * dataframe["atr"]
        kc_lower = dataframe["kc_mid"] - kc_mult * dataframe["atr"]
        squeeze_on = (dataframe["bb_upper"] < kc_upper) & (dataframe["bb_lower"] > kc_lower)
        squeeze_on = squeeze_on.fillna(False).astype(int)
        # consecutive squeeze-on bar count
        grp = (squeeze_on != squeeze_on.shift(1)).cumsum()
        sq_count = squeeze_on.groupby(grp).cumsum()
        return squeeze_on, sq_count

    # ------------------------------------------------------------------ entry
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        squeeze_on, sq_count = self._squeeze_state(dataframe, float(self.buy_kc_mult.value))

        # Release candle: not in squeeze now, but the previous candle ended a run of >= N squeezed bars
        release = (squeeze_on == 0) & (sq_count.shift(1) >= self.buy_sq_min_bars.value)
        release_recent = release.astype(int).rolling(self.release_window, min_periods=1).max() > 0

        kc_upper = dataframe["kc_mid"] + float(self.buy_kc_mult.value) * dataframe["atr"]
        ema_col = f"ema{self.buy_trend_ema_4h.value}_4h"

        conditions = [
            release_recent,
            # directional breakout: price closed above the upper Keltner band
            (dataframe["close"] > kc_upper),
            (dataframe["mom_atr"] > self.buy_mom_min.value),
            (dataframe["mom_atr"] > dataframe["mom_atr"].shift(1)),
            # volume expansion confirms the release
            (dataframe["volume"] > self.buy_vol_mult.value * dataframe["vol_sma"]),
            # 4h trend filter: price above a rising 4h EMA
            (dataframe["close_4h"] > dataframe[ema_col]),
            (dataframe[f"{ema_col[:-3]}_up_4h"] > 0),
            (dataframe["rsi"] < self.buy_rsi_max.value),
            (dataframe["volume"] > 0),
        ]

        dataframe.loc[np.logical_and.reduce(conditions), ["enter_long", "enter_tag"]] = (1, "squeeze_release")
        return dataframe

    # ------------------------------------------------------------------ exit
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        mom = dataframe["mom_atr"]
        declining = (mom < mom.shift(1)).fillna(False).astype(int)
        n = self.sell_fade_bars.value
        fade = declining.rolling(n, min_periods=n).sum() >= n
        # momentum must have decayed materially from its recent peak (relative fade), and still be
        # positive (a negative cross is handled separately below)
        peak = mom.rolling(24, min_periods=1).max()
        fade = fade & (mom > 0) & (peak > 0) & (mom < self.sell_fade_ratio.value * peak)

        # momentum crossing below zero always closes the trade
        mom_neg = (mom < 0) & (mom.shift(1) >= 0)
        exit_cond = fade | mom_neg

        dataframe.loc[exit_cond & (dataframe["volume"] > 0), ["exit_long", "exit_tag"]] = (1, "mom_fade")
        return dataframe
