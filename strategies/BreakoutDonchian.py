"""
BreakoutDonchian
----------------
Donchian-channel breakout strategy on the 1h timeframe. An entry is signalled when
the 1h close breaks above the highest high of the previous N candles (channel upper
band, excluding the current candle), the candle's volume exceeds a multiple of its
20-period volume SMA, and ADX(14) is both above a floor and rising versus the prior
candle. A 4h trend filter (4h close above a 4h EMA) restricts entries to markets that are
already trending up. Positions are exited when the 1h close loses the midline of
a shorter exit Donchian channel (Turtle-style: long entry channel, short exit channel,
optionally with a small buffer), or via the trailing stop / hard stoploss.
"""

# flake8: noqa: F401
import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.strategy import (
    IStrategy,
    informative,
    BooleanParameter,
    CategoricalParameter,
    DecimalParameter,
    IntParameter,
)

import talib.abstract as ta
from technical import qtpylib


class BreakoutDonchian(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short = False
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # Ride breakouts; ROI only acts as a generous cap / time-based cleanup.
    minimal_roi = {
        "0": 0.25,
        "2880": 0.06,   # after 2 days accept 6%
        "5760": 0.02,   # after 4 days accept 2%
    }

    # Real hard stop.
    stoploss = -0.07

    # Trailing stop kicks in once 6% profit is reached, then trails 3% below peak.
    trailing_stop = True
    trailing_stop_positive = 0.03
    trailing_stop_positive_offset = 0.06
    trailing_only_offset_is_reached = True

    use_custom_stoploss = False

    # Longest lookback: 4h EMA(100) -> needs >= ~200 4h candles for stability;
    # startup_candle_count is applied per timeframe (incl. informatives).
    startup_candle_count: int = 220

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

    # ---- Hyperopt parameters (entry) ----
    dc_len_options = [20, 30, 40, 55]
    buy_dc_len = CategoricalParameter(dc_len_options, default=30, space="buy", optimize=True)
    buy_vol_mult = DecimalParameter(1.0, 3.0, decimals=1, default=1.5, space="buy", optimize=True)
    buy_adx_min = IntParameter(15, 35, default=20, space="buy", optimize=True)
    ema4h_options = [50, 100]
    buy_ema4h_len = CategoricalParameter(ema4h_options, default=50, space="buy", optimize=True)

    # ---- Hyperopt parameters (exit) ----
    # Exit when close < midline(exit channel) * (1 - buffer)
    exit_dc_len_options = [10, 15, 20, 30, 40]
    sell_dc_len = CategoricalParameter(exit_dc_len_options, default=30, space="sell", optimize=True)
    sell_mid_buffer = DecimalParameter(0.0, 0.03, decimals=3, default=0.0, space="sell", optimize=True)

    plot_config = {
        "main_plot": {
            "dc_upper_30": {"color": "green"},
            "dc_mid_30": {"color": "orange"},
            "dc_lower_30": {"color": "red"},
        },
        "subplots": {
            "ADX": {"adx": {"color": "blue"}},
        },
    }

    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        for n in self.ema4h_options:
            dataframe[f"ema{n}"] = ta.EMA(dataframe, timeperiod=n)
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Donchian channels for every candidate length. shift(1) so the band is
        # built only from candles that are fully closed before the current one.
        for n in sorted(set(self.dc_len_options) | set(self.exit_dc_len_options)):
            upper = dataframe["high"].rolling(n).max().shift(1)
            lower = dataframe["low"].rolling(n).min().shift(1)
            dataframe[f"dc_upper_{n}"] = upper
            dataframe[f"dc_lower_{n}"] = lower
            dataframe[f"dc_mid_{n}"] = (upper + lower) / 2.0

        dataframe["vol_sma"] = ta.SMA(dataframe["volume"], timeperiod=20)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["adx_prev"] = dataframe["adx"].shift(1)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        n = int(self.buy_dc_len.value)
        ema_col = f"ema{int(self.buy_ema4h_len.value)}_4h"

        conditions = [
            dataframe["close"] > dataframe[f"dc_upper_{n}"],                    # breakout
            dataframe["volume"] > dataframe["vol_sma"] * self.buy_vol_mult.value,  # volume confirm
            dataframe["adx"] > self.buy_adx_min.value,                          # trend strength
            dataframe["adx"] > dataframe["adx_prev"],                           # ADX rising
            dataframe["close_4h"] > dataframe[ema_col],                         # 4h trend filter
            dataframe["volume"] > 0,
        ]

        dataframe.loc[np.logical_and.reduce(conditions), ["enter_long", "enter_tag"]] = (1, "dc_breakout")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        n = int(self.sell_dc_len.value)
        mid = dataframe[f"dc_mid_{n}"] * (1.0 - self.sell_mid_buffer.value)

        dataframe.loc[
            (dataframe["close"] < mid) & (dataframe["volume"] > 0),
            ["exit_long", "exit_tag"],
        ] = (1, "mid_loss")
        return dataframe
