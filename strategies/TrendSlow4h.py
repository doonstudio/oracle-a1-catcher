"""
TrendSlow4h - slow pullback trend-following on the 4h timeframe with a BTC daily regime gate.

Idea: only trade when the whole market is in an uptrend (BTC/USDT daily close above a rising
daily EMA50, the "regime gate") and the pair's own 4h EMA (34/50/89, hyperoptable) is rising. We enter
when price has recently pulled back to (or below) that rising EMA and then closes back above it,
provided the close is not already stretched too far above the EMA (in ATR units) so we do not
chase. Exits are deliberately slow: a 4h close below the EMA by more than a fraction of ATR
while the EMA itself has turned down (trend reversal), BTC closing back below its daily EMA50
(regime off), a wide ATR-based initial stop (3-4x ATR measured on the signal candle), or a loose
ROI. Holds are expected to last days to weeks with
few trades and a large average winner. Spot only, long only, no lookahead.
"""

from datetime import datetime, timedelta

import numpy as np
from pandas import DataFrame

import talib.abstract as ta

from freqtrade.persistence import Trade
from freqtrade.strategy import (
    CategoricalParameter,
    DecimalParameter,
    IntParameter,
    IStrategy,
    informative,
    stoploss_from_absolute,
)


class TrendSlow4h(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "4h"
    can_short: bool = False
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # Loose ROI: let the trend run, but bank a very large move.
    minimal_roi = {"0": 0.30}

    # Hard maximum loss; the ATR stop in custom_stoploss is normally tighter than this.
    stoploss = -0.15
    use_custom_stoploss = True
    trailing_stop = False

    # Longest lookbacks: 4h EMA89 and BTC 1d EMA50 (=300 4h candles). 300 candles of 4h data
    # give the 1d informative 50 daily candles of warm-up, so the daily EMA50 is defined from the
    # first traded candle.
    startup_candle_count: int = 300

    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    # ---- Hyperopt parameters (buy space) ----
    # 4h EMA length that defines the trend and the pullback level.
    buy_ema_len = CategoricalParameter([34, 50, 89], default=50, space="buy", optimize=True)
    # EMA must be higher than it was this many 4h candles ago (rising trend).
    buy_slope_bars = IntParameter(3, 12, default=6, space="buy", optimize=True)
    # Do not chase: (close - EMA) / ATR must be below this.
    buy_max_ext = DecimalParameter(0.5, 3.0, default=1.5, decimals=1, space="buy", optimize=True)
    # Regime gate: BTC daily EMA50 must be higher than it was this many days ago (BTC uptrend),
    # in addition to BTC daily close being above the EMA50.
    buy_btc_slope_days = IntParameter(3, 10, default=5, space="buy", optimize=True)
    # Fixed (non-optimised) structural settings
    btc_ema_len: int = 50  # BTC/USDT daily EMA length used as the regime gate
    pullback_window: int = 8  # the low must have touched the EMA within the last N 4h candles

    # ---- Hyperopt parameters (sell space) ----
    # Trend-reversal exit: close must be below EMA by more than this many ATR ...
    sell_exit_atr = DecimalParameter(0.0, 1.5, default=0.5, decimals=1, space="sell", optimize=True)
    # ... AND the EMA itself must have turned down over this many 4h candles.
    sell_slope_bars = IntParameter(2, 10, default=4, space="sell", optimize=True)
    # Initial protective stop distance in ATR (measured on the signal candle).
    sell_atr_mult = DecimalParameter(2.0, 5.0, default=3.5, decimals=1, space="sell", optimize=True)

    @property
    def plot_config(self):
        return {
            "main_plot": {"ema34": {}, "ema50": {}, "ema89": {}},
            "subplots": {"ATR": {"atr": {}}},
        }

    # ------------------------------------------------------------------ BTC 1d regime
    @informative("1d", "BTC/USDT")
    def populate_indicators_btc_1d(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        # EMA slope over n daily candles (computed on the daily frame => no mixing with 4h rows)
        for n in range(3, 11):
            dataframe[f"ema50_slope{n}"] = dataframe["ema50"] - dataframe["ema50"].shift(n)
        return dataframe

    # ------------------------------------------------------------------ indicators 4h
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        for length in (34, 50, 89):
            dataframe[f"ema{length}"] = ta.EMA(dataframe, timeperiod=length)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        return dataframe

    # ------------------------------------------------------------------ helpers
    def _regime_on(self, dataframe: DataFrame):
        """BTC daily close above its daily EMA (used for entries and, without slope, exits)."""
        btc_close = dataframe["btc_usdt_close_1d"]
        btc_ema = dataframe[f"btc_usdt_ema{self.btc_ema_len}_1d"]
        return (btc_close > btc_ema).fillna(False)

    def _regime_rising(self, dataframe: DataFrame):
        """BTC daily EMA rising over the last N days (entries only -> hysteresis vs. exits)."""
        # ema_1d_shift{n} is computed on the daily frame itself, so shift(n) is n daily candles.
        col = f"btc_usdt_ema{self.btc_ema_len}_slope{self.buy_btc_slope_days.value}_1d"
        return (dataframe[col] > 0).fillna(False)

    # ------------------------------------------------------------------ entry
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        ema = dataframe[f"ema{self.buy_ema_len.value}"]
        atr = dataframe["atr"]

        ema_rising = ema > ema.shift(self.buy_slope_bars.value)
        touched = (dataframe["low"] <= ema).astype(int)
        pulled_back = touched.rolling(self.pullback_window, min_periods=1).max() > 0
        extension = (dataframe["close"] - ema) / atr.replace(0, np.nan)

        conditions = [
            self._regime_on(dataframe),
            self._regime_rising(dataframe),
            ema_rising.fillna(False),
            pulled_back,
            dataframe["close"] > ema,
            extension < self.buy_max_ext.value,
            dataframe["volume"] > 0,
        ]

        dataframe.loc[np.logical_and.reduce(conditions), ["enter_long", "enter_tag"]] = (
            1,
            "ema_pullback",
        )
        return dataframe

    # ------------------------------------------------------------------ exit
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        ema = dataframe[f"ema{self.buy_ema_len.value}"]
        atr = dataframe["atr"]

        ema_falling = ema < ema.shift(self.sell_slope_bars.value)
        trend_break = (dataframe["close"] < (ema - self.sell_exit_atr.value * atr)) & ema_falling
        regime_off = ~self._regime_on(dataframe)

        dataframe.loc[trend_break.fillna(False) & (dataframe["volume"] > 0), ["exit_long", "exit_tag"]] = (
            1,
            "trend_break",
        )
        dataframe.loc[regime_off & (dataframe["volume"] > 0), ["exit_long", "exit_tag"]] = (
            1,
            "regime_off",
        )
        return dataframe

    # ------------------------------------------------------------------ ATR stop
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
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or dataframe.empty:
            return None
        # Signal candle = last candle that closed before the trade was opened.
        signal = dataframe.loc[dataframe["date"] < trade.open_date_utc]
        if signal.empty:
            return None
        atr = float(signal["atr"].iloc[-1])
        if not np.isfinite(atr) or atr <= 0:
            return None
        stop_rate = trade.open_rate - self.sell_atr_mult.value * atr
        return -stoploss_from_absolute(stop_rate, current_rate, is_short=trade.is_short)
