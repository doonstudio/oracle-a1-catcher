"""
MomentumMacdAdx
---------------
Trend-momentum breakout strategy on the 15m timeframe. An entry requires the MACD
histogram to turn positive on the current candle (momentum flipping bullish) while the
ADX is above a threshold (a trend actually exists) and DI+ is above DI- by a margin
(the trend is upward). A higher-timeframe 4h EMA filter (close_4h > EMA_4h) keeps entries
aligned with the larger trend. Trades exit when the MACD line crosses below its signal
line, or via a minimal ROI table, a hard stoploss and an offset-activated trailing stop.
"""

# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
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


class MomentumMacdAdx(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "15m"
    can_short: bool = False
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # ROI: take profit on quick momentum pops, decay over time (minutes -> ratio)
    minimal_roi = {
        "0": 0.06,
        "180": 0.035,
        "480": 0.02,
        "1440": 0.01,
    }

    # Hard stop
    stoploss = -0.05

    # Trailing stop, only activated after a profit offset is reached
    trailing_stop = True
    trailing_stop_positive = 0.012
    trailing_stop_positive_offset = 0.03
    trailing_only_offset_is_reached = True

    use_custom_stoploss = False

    # Longest lookback: EMA 200 on the 4h informative (freqtrade applies
    # startup_candle_count per timeframe, so 220 covers both 15m MACD/ADX and 4h EMA200).
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

    # ------------------------------------------------------------------
    # Hyperopt parameters (buy space)
    # ------------------------------------------------------------------
    adx_threshold = IntParameter(15, 40, default=25, space="buy", optimize=True)
    di_diff_min = DecimalParameter(0.0, 10.0, default=2.0, decimals=1, space="buy", optimize=True)
    ema_4h_period = CategoricalParameter([21, 50, 100, 200], default=50, space="buy", optimize=True)
    # Require MACD line itself to be below zero at the flip -> early momentum turn (dip buy)
    # or above zero -> confirmed trend continuation. "any" disables the filter.
    macd_zone = CategoricalParameter(["any", "below", "above"], default="any", space="buy", optimize=True)
    # Require ADX to be rising vs. 3 candles ago (trend strengthening at the flip).
    adx_rising = BooleanParameter(default=True, space="buy", optimize=True)

    # ------------------------------------------------------------------
    # Hyperopt parameters (sell space)
    # ------------------------------------------------------------------
    # MACD cross-down confirmation: exit once macdhist < -(exit_hist_atr * ATR).
    # 0.0 = exit on the raw MACD/signal cross-down; larger = wait for a confirmed reversal.
    exit_hist_atr = DecimalParameter(0.0, 1.0, default=0.6, decimals=2, space="sell", optimize=True)
    # Additionally exit when ADX collapses below this level while DI- > DI+
    exit_adx_weak = IntParameter(10, 30, default=18, space="sell", optimize=True)

    # ------------------------------------------------------------------
    # Informative 4h
    # ------------------------------------------------------------------
    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        for p in (21, 50, 100, 200):
            dataframe[f"ema_{p}"] = ta.EMA(dataframe, timeperiod=p)
        return dataframe

    # ------------------------------------------------------------------
    # Primary 15m indicators
    # ------------------------------------------------------------------
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        macd = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe["macd"] = macd["macd"]
        dataframe["macdsignal"] = macd["macdsignal"]
        dataframe["macdhist"] = macd["macdhist"]

        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["plus_di"] = ta.PLUS_DI(dataframe, timeperiod=14)
        dataframe["minus_di"] = ta.MINUS_DI(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        # histogram flip: previous candle <= 0, current > 0
        dataframe["hist_flip_up"] = (
            (dataframe["macdhist"] > 0) & (dataframe["macdhist"].shift(1) <= 0)
        ).astype(int)

        return dataframe

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        ema_col = f"ema_{self.ema_4h_period.value}_4h"

        conditions = [
            dataframe["hist_flip_up"] == 1,
            dataframe["adx"] > self.adx_threshold.value,
            (dataframe["plus_di"] - dataframe["minus_di"]) > self.di_diff_min.value,
            dataframe["close_4h"] > dataframe[ema_col],
            dataframe["volume"] > 0,
        ]

        if self.macd_zone.value == "below":
            conditions.append(dataframe["macd"] < 0)
        elif self.macd_zone.value == "above":
            conditions.append(dataframe["macd"] > 0)

        if self.adx_rising.value:
            conditions.append(dataframe["adx"] > dataframe["adx"].shift(3))

        dataframe.loc[
            np.logical_and.reduce(conditions),
            ["enter_long", "enter_tag"],
        ] = (1, "macd_adx_momo")

        return dataframe

    # ------------------------------------------------------------------
    # Exit
    # ------------------------------------------------------------------
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # MACD cross-down, confirmed by the histogram falling below -k * ATR
        dataframe.loc[
            (dataframe["macd"] < dataframe["macdsignal"])
            & (dataframe["macdhist"] < -(self.exit_hist_atr.value * dataframe["atr"])),
            ["exit_long", "exit_tag"],
        ] = (1, "macd_cross_down")

        # trend has died: weak ADX with bearish DI orientation
        dataframe.loc[
            (dataframe["adx"] < self.exit_adx_weak.value)
            & (dataframe["minus_di"] > dataframe["plus_di"]),
            ["exit_long", "exit_tag"],
        ] = (1, "adx_weak")

        return dataframe
