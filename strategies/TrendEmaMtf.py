"""
TrendEmaMtf - multi-timeframe EMA trend-following strategy (spot, long only).

Idea: on the 1h chart we buy when a fast EMA crosses above a slow EMA (fresh trend), or in
"recent_cross" mode on the first candle within 24h of that cross where the whole bullish
set-up (EMA stack, higher-timeframe trend, ADX) is true, so a trend that starts while a
filter is still off is not missed (still at most one entry per cross).
Entries are only allowed while the higher timeframes agree: the 4h close must be above a
rising 4h EMA and, optionally, the daily close above a daily EMA; ADX confirms that a trend
exists and an RSI cap avoids chasing overextended moves. Exits are trend-loss based (fast EMA
crossing back under the slow EMA, optionally also the 4h trend flipping), backed by an
4h-ATR-multiple chandelier trailing stop in custom_stoploss and a hard catastrophic stoploss.
"""

# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
# flake8: noqa: F401
# isort: skip_file
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import (
    IStrategy,
    informative,
    BooleanParameter,
    CategoricalParameter,
    DecimalParameter,
    IntParameter,
    stoploss_from_absolute,
)

import talib.abstract as ta
from technical import qtpylib


class TrendEmaMtf(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short: bool = False
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # Trend following: let winners run; ROI only harvests large or very old gains.
    minimal_roi = {
        "0": 0.35,
        "2880": 0.15,
        "7200": 0.06,
    }

    # Hard catastrophic stop; the ATR stop in custom_stoploss is the working stop.
    stoploss = -0.10
    use_custom_stoploss = True

    # Native trailing disabled - ATR trailing handled by custom_stoploss.
    trailing_stop = False
    trailing_stop_positive = None
    trailing_stop_positive_offset = 0.0
    trailing_only_offset_is_reached = False

    # Longest lookback on the primary timeframe: slow EMA 200 (x2 for EMA warm-up).
    # Informative timeframes receive the same candle count each (400 x 4h / 400 x 1d),
    # which comfortably covers the 4h EMA 50 and the 1d EMA 20.
    startup_candle_count: int = 400

    # Do not re-enter a pair right after a trade closed (trend churn / whipsaw guard).
    @property
    def protections(self):
        return [
            {"method": "CooldownPeriod", "stop_duration_candles": 12},
        ]

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

    # ------------------------------------------------------------------ parameters
    # Entry (buy space)
    ema_fast_len = CategoricalParameter([9, 13, 21, 34], default=21, space="buy", optimize=True)
    ema_slow_len = CategoricalParameter([50, 100, 200], default=100, space="buy", optimize=True)
    adx_min = IntParameter(15, 35, default=20, space="buy", optimize=True)
    rsi_max = IntParameter(60, 85, default=75, space="buy", optimize=True)
    use_1d_filter = BooleanParameter(default=True, space="buy", optimize=True)
    entry_mode = CategoricalParameter(
        ["cross", "recent_cross"], default="recent_cross", space="buy", optimize=True
    )

    # Exit (sell space)
    # Chandelier stop distance in multiples of the 4h ATR.
    atr_stop_mult = DecimalParameter(1.5, 4.0, default=2.5, decimals=1, space="sell", optimize=True)
    exit_on_htf_loss = BooleanParameter(default=True, space="sell", optimize=True)

    plot_config = {
        "main_plot": {
            "ema_21": {"color": "green"},
            "ema_100": {"color": "purple"},
        },
        "subplots": {
            "ADX": {"adx": {"color": "red"}},
            "RSI": {"rsi": {"color": "blue"}},
        },
    }

    # ------------------------------------------------------------------ informatives
    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["ema50_rising"] = (dataframe["ema50"] > dataframe["ema50"].shift(1)).astype(int)
        dataframe["trend_up"] = (
            (dataframe["close"] > dataframe["ema50"]) & (dataframe["ema50_rising"] == 1)
        ).astype(int)
        return dataframe

    @informative("1d")
    def populate_indicators_1d(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["trend_up"] = (
            (dataframe["close"] > dataframe["ema20"])
            & (dataframe["ema20"] > dataframe["ema20"].shift(1))
        ).astype(int)
        return dataframe

    # ------------------------------------------------------------------ indicators
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Pre-compute every EMA length the hyperopt space can pick.
        for length in set(list(self.ema_fast_len.range) + list(self.ema_slow_len.range)):
            dataframe[f"ema_{length}"] = ta.EMA(dataframe, timeperiod=length)

        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        return dataframe

    # ------------------------------------------------------------------ entries
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        fast = dataframe[f"ema_{self.ema_fast_len.value}"]
        slow = dataframe[f"ema_{self.ema_slow_len.value}"]

        htf_ok = dataframe["trend_up_4h"] == 1
        if self.use_1d_filter.value:
            htf_ok = htf_ok & (dataframe["trend_up_1d"] == 1)

        # Full bullish set-up on the current candle.
        setup = (
            (fast > slow)
            & (dataframe["close"] > fast)
            & htf_ok
            & (dataframe["adx"] > self.adx_min.value)
        )

        cross_up = qtpylib.crossed_above(fast, slow)
        if self.entry_mode.value == "cross":
            # Only the candle on which the fast EMA crosses above the slow EMA.
            trigger = cross_up
        else:
            # A cross happened within the last 24 candles and the full set-up is true;
            # take only the first such candle (at most one entry per cross).
            recent = cross_up.astype(int).rolling(24, min_periods=1).max() > 0
            armed = setup & recent
            trigger = armed & ~armed.shift(1).fillna(False).astype(bool)

        conditions = (
            trigger
            & setup
            & (dataframe["rsi"] < self.rsi_max.value)
            & (dataframe["volume"] > 0)
        )

        dataframe.loc[conditions, ["enter_long", "enter_tag"]] = (1, f"ema_{self.entry_mode.value}")
        return dataframe

    # ------------------------------------------------------------------ exits
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        fast = dataframe[f"ema_{self.ema_fast_len.value}"]
        slow = dataframe[f"ema_{self.ema_slow_len.value}"]

        trend_lost = qtpylib.crossed_below(fast, slow)
        dataframe.loc[trend_lost & (dataframe["volume"] > 0), ["exit_long", "exit_tag"]] = (
            1,
            "ema_cross_down",
        )

        if self.exit_on_htf_loss.value:
            htf_lost = (dataframe["trend_up_4h"] == 0) & (dataframe["trend_up_4h"].shift(1) == 1)
            dataframe.loc[htf_lost & (dataframe["volume"] > 0), ["exit_long", "exit_tag"]] = (
                1,
                "htf_trend_loss",
            )
        return dataframe

    # ------------------------------------------------------------------ ATR trailing stop
    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> Optional[float]:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair=pair, timeframe=self.timeframe)
        if dataframe is None or dataframe.empty:
            return None
        last = dataframe.iloc[-1]
        atr = last["atr_4h"]
        if not np.isfinite(atr) or atr <= 0:
            return None

        stop_price = current_rate - self.atr_stop_mult.value * atr
        # Freqtrade only ever tightens the stop, so this behaves as an ATR trailing stop.
        return stoploss_from_absolute(stop_price, current_rate, is_short=trade.is_short)
