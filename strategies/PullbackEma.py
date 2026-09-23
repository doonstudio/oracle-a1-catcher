"""
PullbackEma - pullback-in-uptrend strategy (spot, long only, 1h primary timeframe).

Idea: only trade in the direction of the higher-timeframe trend. The 4h chart must show
EMA50 above EMA200 with price holding above EMA50, and the 1d chart must have price above its
rising EMA50. On the 1h chart we
wait for a pullback into the EMA20/EMA50 zone (a recent low touched EMA20 while price still holds
above EMA50), an RSI "reset" (RSI dipped below a threshold within the last few candles and is now
turning up) and a bullish confirmation candle that closes back above EMA20. Exits: take profit into strength when price prints
a new swing high with an overbought RSI, exit when the 1h trend structure fails (EMA20 < EMA50), a
staged ROI table, a trailing stop and an ATR-based custom stop measured on the signal candle.
"""

from datetime import datetime

import numpy as np
import pandas as pd
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import (
    DecimalParameter,
    IntParameter,
    IStrategy,
    informative,
    stoploss_from_open,
)


class PullbackEma(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short = False
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # ROI table (fractions), keyed by minutes since entry.
    minimal_roi = {
        "0": 0.06,
        "480": 0.04,
        "1440": 0.02,
    }

    # Hard cap; the ATR custom stop tightens this per trade.
    stoploss = -0.12
    use_custom_stoploss = True

    trailing_stop = True
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.05
    trailing_only_offset_is_reached = True

    # Longest lookback on the primary timeframe (EMA50/ATR/RSI/swing window) and enough
    # candles for EMA200 on the 4h informative timeframe (freqtrade applies this count per timeframe).
    startup_candle_count: int = 220

    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    # ---------------- hyperopt parameters ----------------
    # Entry
    rsi_reset = IntParameter(30, 50, default=45, space="buy", optimize=True)
    rsi_lookback = IntParameter(2, 8, default=4, space="buy", optimize=True)
    pb_lookback = IntParameter(1, 6, default=3, space="buy", optimize=True)
    pb_zone_pct = DecimalParameter(0.0, 1.5, default=0.5, decimals=1, space="buy", optimize=True)
    # Exit
    swing_lookback = IntParameter(8, 48, default=24, space="sell", optimize=True)
    rsi_exit = IntParameter(55, 80, default=68, space="sell", optimize=True)
    atr_mult = DecimalParameter(1.5, 4.0, default=2.5, decimals=1, space="sell", optimize=True)

    # ---------------- informative timeframes ----------------
    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema200"] = ta.EMA(dataframe, timeperiod=200)
        # Trend up and still healthy: EMA50 above EMA200 and price holding above the 4h EMA50
        # (a 4h close below EMA50 is a breakdown, not a pullback).
        dataframe["trend_up"] = (
            (dataframe["ema50"] > dataframe["ema200"]) & (dataframe["close"] > dataframe["ema50"])
        ).astype(int)
        return dataframe

    @informative("1d")
    def populate_indicators_1d(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["trend_up"] = (
            (dataframe["close"] > dataframe["ema50"])
            & (dataframe["ema50"] > dataframe["ema50"].shift(1))
        ).astype(int)
        return dataframe

    # ---------------- primary timeframe ----------------
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pb_lb = int(self.pb_lookback.value)
        rsi_lb = int(self.rsi_lookback.value)
        zone_tol = 1.0 + float(self.pb_zone_pct.value) / 100.0

        # Lowest low of the last N closed candles (including the current one) touched the EMA20
        # zone (EMA20 * (1 + tolerance)), i.e. price pulled back into the EMA20/EMA50 area.
        recent_low = dataframe["low"].rolling(pb_lb).min()
        pulled_back = recent_low <= dataframe["ema20"] * zone_tol

        # RSI dipped below the reset level within the last N candles and is now turning up.
        rsi_min = dataframe["rsi"].rolling(rsi_lb).min()
        rsi_reset = (rsi_min < self.rsi_reset.value) & (dataframe["rsi"] > dataframe["rsi"].shift(1))

        # Bullish confirmation candle that reclaims EMA20 (bounce confirmed, not still falling).
        bullish_candle = (
            (dataframe["close"] > dataframe["open"])
            & (dataframe["close"] > dataframe["close"].shift(1))
            & (dataframe["close"] > dataframe["ema20"])
        )

        htf_trend = (dataframe["trend_up_4h"] == 1) & (dataframe["trend_up_1d"] == 1)

        conditions = (
            htf_trend
            & (dataframe["ema20"] > dataframe["ema50"])
            & (dataframe["close"] > dataframe["ema50"])
            & pulled_back
            & rsi_reset
            & bullish_candle
            & (dataframe["volume"] > 0)
        )
        dataframe.loc[conditions, ["enter_long", "enter_tag"]] = (1, "pullback_ema")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        swing_lb = int(self.swing_lookback.value)
        prior_high = dataframe["high"].rolling(swing_lb).max().shift(1)

        # Take profit into strength: new swing high with elevated RSI.
        swing_high = (dataframe["high"] > prior_high) & (dataframe["rsi"] > self.rsi_exit.value)
        # Structural trend failure on the primary timeframe (EMA20 crossed below EMA50).
        trend_break = dataframe["ema20"] < dataframe["ema50"]

        dataframe.loc[swing_high, ["exit_long", "exit_tag"]] = (1, "swing_high")
        dataframe.loc[trend_break, ["exit_long", "exit_tag"]] = (1, "ema_cross_down")
        return dataframe

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float | None:
        """
        ATR stop: entry price minus atr_mult * ATR of the signal candle (the last closed candle
        before the trade opened). Expressed relative to the current rate for freqtrade.
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or dataframe.empty:
            return None
        signal_rows = dataframe.loc[dataframe["date"] < trade.open_date_utc]
        if signal_rows.empty:
            return None
        atr = signal_rows["atr"].iloc[-1]
        if not np.isfinite(atr) or atr <= 0 or trade.open_rate <= 0:
            return None
        stop_distance = float(self.atr_mult.value) * float(atr) / float(trade.open_rate)
        open_relative_stop = -min(stop_distance, abs(self.stoploss))
        return stoploss_from_open(open_relative_stop, current_profit, is_short=False, leverage=1.0)
