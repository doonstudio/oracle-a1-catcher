"""
MeanRevBb - Bollinger Band mean reversion with higher-timeframe trend filter.

Idea: on the 15m timeframe, buy short-term exhaustion (close at/below the lower
Bollinger Band together with an oversold RSI), but ONLY while the higher timeframe
trend is up (1h close above its EMA200, optionally also 4h close above its EMA).
The expectation is that dips inside an uptrend snap back toward the band mean.
Exits come from a tight time-decaying ROI table, a snap-back to the middle band or an
RSI recovery (only taken in profit), a trailing stop once in profit, a time-stop that
closes the trade after a few hours if the snap-back did not happen (the thesis has a
short shelf life), and a hard stoploss as the last resort.
"""

# flake8: noqa: F401
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from pandas import DataFrame

import talib.abstract as ta
from technical import qtpylib

from freqtrade.persistence import Trade
from freqtrade.strategy import (
    IStrategy,
    informative,
    BooleanParameter,
    DecimalParameter,
    IntParameter,
)


class MeanRevBb(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "15m"
    can_short: bool = False
    process_only_new_candles = True
    use_exit_signal = True
    # Only take the mean-reversion exit signal when the trade is actually in profit;
    # otherwise let the time-decaying ROI table / stops handle the trade.
    exit_profit_only = True
    exit_profit_offset = 0.0
    ignore_roi_if_entry_signal = False

    # Tight, time-decaying ROI table (minutes -> profit ratio).
    minimal_roi = {
        "0": 0.025,
        "45": 0.015,
        "120": 0.01,
        "360": 0.005,
        "720": 0.0,
    }

    stoploss = -0.06

    trailing_stop = True
    trailing_stop_positive = 0.01
    trailing_stop_positive_offset = 0.02
    trailing_only_offset_is_reached = True

    use_custom_stoploss = False

    # 1h EMA200 needs >=200 informative candles; freqtrade fetches
    # startup_candle_count candles per informative timeframe as well.
    startup_candle_count: int = 400

    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    # ---------------- Hyperopt parameters ----------------
    # Entry
    buy_rsi = IntParameter(20, 40, default=30, space="buy", optimize=True)
    # Position of close inside the band: 0 = at the lower band, <0 = below it.
    buy_bb_pct = DecimalParameter(-0.10, 0.25, default=0.05, decimals=2, space="buy", optimize=True)
    # Minimum relative band width ((upper-lower)/mid): the snap-back target (distance to the
    # middle band) must be large enough to clear fees and the typical time-stop loss.
    buy_bb_width_min = DecimalParameter(0.01, 0.08, default=0.04, decimals=3, space="buy", optimize=True)
    # Require 4h trend confirmation in addition to 1h.
    buy_use_4h_trend = BooleanParameter(default=True, space="buy", optimize=True)

    # Exit
    sell_rsi = IntParameter(50, 75, default=60, space="sell", optimize=True)
    sell_use_bb_mid = BooleanParameter(default=True, space="sell", optimize=True)
    # Time-stop: close the trade after this many hours regardless of profit.
    sell_max_hours = IntParameter(2, 12, default=6, space="sell", optimize=True)

    plot_config = {
        "main_plot": {
            "bb_lowerband": {"color": "grey"},
            "bb_middleband": {"color": "orange"},
            "bb_upperband": {"color": "grey"},
        },
        "subplots": {"RSI": {"rsi": {"color": "red"}}},
    }

    # ---------------- Informative timeframes ----------------
    @informative("1h")
    def populate_indicators_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["trend_up"] = (dataframe["close"] > dataframe["ema200"]).astype(int)
        return dataframe

    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["trend_up"] = (dataframe["close"] > dataframe["ema50"]).astype(int)
        return dataframe

    # ---------------- Primary timeframe ----------------
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        bollinger = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2)
        dataframe["bb_lowerband"] = bollinger["lower"]
        dataframe["bb_middleband"] = bollinger["mid"]
        dataframe["bb_upperband"] = bollinger["upper"]
        band_range = (dataframe["bb_upperband"] - dataframe["bb_lowerband"]).replace(0, np.nan)
        dataframe["bb_pct"] = (dataframe["close"] - dataframe["bb_lowerband"]) / band_range
        dataframe["bb_width"] = band_range / dataframe["bb_middleband"]
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions = [
            dataframe["rsi"] < self.buy_rsi.value,
            # the candle actually touched / pierced the lower band ...
            dataframe["low"] <= dataframe["bb_lowerband"],
            # ... and closed in the lower part of the band (or below it)
            dataframe["bb_pct"] <= self.buy_bb_pct.value,
            dataframe["bb_width"] >= self.buy_bb_width_min.value,
            dataframe["trend_up_1h"] == 1,
            dataframe["volume"] > 0,
        ]
        if self.buy_use_4h_trend.value:
            conditions.append(dataframe["trend_up_4h"] == 1)

        dataframe.loc[np.logical_and.reduce(conditions), ["enter_long", "enter_tag"]] = (1, "bb_rsi_dip")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        exit_cond = dataframe["rsi"] > self.sell_rsi.value
        if self.sell_use_bb_mid.value:
            exit_cond = exit_cond | (dataframe["close"] >= dataframe["bb_middleband"])

        dataframe.loc[exit_cond & (dataframe["volume"] > 0), ["exit_long", "exit_tag"]] = (1, "bb_mid_or_rsi")
        return dataframe

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ):
        """
        Time-stop: a mean-reversion snap-back either happens within a few hours or it
        does not; holding longer only waits for the hard stoploss.
        """
        if current_time - trade.open_date_utc >= timedelta(hours=int(self.sell_max_hours.value)):
            return "time_stop"
        return None
