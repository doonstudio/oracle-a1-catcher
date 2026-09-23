"""
RelStrengthBtc - relative strength vs BTC momentum strategy (4h, BTC 1d regime gate).

Idea: in a crypto bull phase, capital rotates into the coins that are outperforming BTC.
For each pair we compute the 7-day (42 x 4h candles) return minus BTC's 7-day return
(BTC/USDT via the @informative decorator) and enter long on 4h when that relative strength
has been positive for at least a day and is rising, the pair's own 7-day return is positive,
price sits above a rising 4h EMA50, and the BTC regime gate is on (BTC 1d close above its 1d EMA50) - otherwise we
stay in cash. The position is closed when relative strength turns clearly negative, when
the 4h close is lost below the EMA50 for N candles, by a wide fixed ATR stop set at entry
(custom_stoploss), or by a loose ROI. Few trades, multi-day holds, spot only, no lookahead.
"""

from datetime import datetime

import numpy as np
import pandas as pd
from pandas import DataFrame

import talib.abstract as ta

from freqtrade.persistence import Trade
from freqtrade.strategy import (
    DecimalParameter,
    IntParameter,
    IStrategy,
    informative,
    stoploss_from_absolute,
)


class RelStrengthBtc(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "4h"
    can_short: bool = False
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # Loose ROI: profit taking is mainly done by the relative-strength exit
    minimal_roi = {"0": 0.30}

    # Hard cap on initial risk; the effective stop is the fixed ATR stop set in custom_stoploss
    stoploss = -0.15
    use_custom_stoploss = True

    trailing_stop = False

    # 7-day return window = 42 x 4h candles; EMA50 on 4h; BTC EMA50 on 1d (applied in
    # informative timeframe units, so 1d gets startup_candle_count days of warm-up)
    startup_candle_count: int = 120

    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    # 7 days in 4h candles
    rs_window: int = 42
    ema_len: int = 50

    # ---- Hyperopt parameters (buy space) ----
    # Minimum relative strength (pair 7d return - BTC 7d return) at entry.
    buy_rs_min = DecimalParameter(0.0, 0.10, default=0.02, decimals=3, space="buy", optimize=True)
    # Relative strength must be higher than it was this many 4h candles ago (rising).
    buy_rs_rise_bars = IntParameter(1, 6, default=3, space="buy", optimize=True)
    # Pair's own 7d return must exceed this (avoid "less bad than BTC" entries).
    buy_pair_ret_min = DecimalParameter(0.0, 0.10, default=0.0, decimals=3, space="buy", optimize=True)
    # Persistence: relative strength must have been above buy_rs_min for this many consecutive
    # 4h candles (momentum persistence, filters one-candle spikes).
    buy_rs_persist_bars = IntParameter(2, 12, default=6, space="buy", optimize=True)

    # ---- Hyperopt parameters (sell space) ----
    # Exit when relative strength drops below this (negative) level.
    sell_rs_exit = DecimalParameter(-0.10, 0.0, default=-0.03, decimals=3, space="sell", optimize=True)
    # Exit when the 4h close has been below the EMA50 for this many consecutive candles.
    sell_ema_bars = IntParameter(1, 4, default=2, space="sell", optimize=True)
    # ATR(14, 4h) multiple for the fixed initial stop distance below the entry price.
    sell_atr_mult = DecimalParameter(2.0, 5.0, default=3.5, decimals=1, space="sell", optimize=True)

    @property
    def plot_config(self):
        return {
            "main_plot": {"ema50": {"color": "orange"}},
            "subplots": {
                "RS": {"rs": {"color": "blue"}},
                "REGIME": {"btc_regime": {"color": "green"}},
            },
        }

    # ------------------------------------------------------------------ BTC 1d regime
    @informative("1d", "BTC/{stake}")
    def populate_indicators_btc_1d(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        return dataframe

    # ------------------------------------------------------------------ BTC 4h return
    @informative("4h", "BTC/{stake}")
    def populate_indicators_btc_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ret7"] = dataframe["close"] / dataframe["close"].shift(self.rs_window) - 1.0
        return dataframe

    # ------------------------------------------------------------------ indicators 4h
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=self.ema_len)
        dataframe["ema50_up"] = (dataframe["ema50"] > dataframe["ema50"].shift(1)).astype(int)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        dataframe["ret7"] = dataframe["close"] / dataframe["close"].shift(self.rs_window) - 1.0
        dataframe["rs"] = dataframe["ret7"] - dataframe["btc_usdt_ret7_4h"]

        # BTC regime: 1d close above 1d EMA50 (NaN -> off during warm-up)
        dataframe["btc_regime"] = (
            (dataframe["btc_usdt_close_1d"] > dataframe["btc_usdt_ema50_1d"]).fillna(False).astype(int)
        )
        return dataframe

    # ------------------------------------------------------------------ entry
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        rs = dataframe["rs"]
        above = (rs > self.buy_rs_min.value).astype(int)
        n_persist = self.buy_rs_persist_bars.value
        rs_persistent = above.rolling(n_persist, min_periods=n_persist).sum() >= n_persist
        conditions = [
            (dataframe["btc_regime"] > 0),
            rs_persistent,
            (rs > rs.shift(self.buy_rs_rise_bars.value)),
            (dataframe["ret7"] > self.buy_pair_ret_min.value),
            (dataframe["close"] > dataframe["ema50"]),
            (dataframe["ema50_up"] > 0),
            (dataframe["volume"] > 0),
        ]
        dataframe.loc[np.logical_and.reduce(conditions), ["enter_long", "enter_tag"]] = (1, "rs_up")
        return dataframe

    # ------------------------------------------------------------------ exit
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        rs_lost = dataframe["rs"] < self.sell_rs_exit.value

        below = (dataframe["close"] < dataframe["ema50"]).astype(int)
        n = self.sell_ema_bars.value
        ema_lost = below.rolling(n, min_periods=n).sum() >= n

        dataframe.loc[rs_lost & (dataframe["volume"] > 0), ["exit_long", "exit_tag"]] = (1, "rs_lost")
        dataframe.loc[ema_lost & (dataframe["volume"] > 0) & ~rs_lost, ["exit_long", "exit_tag"]] = (
            1,
            "ema50_lost",
        )
        return dataframe

    # ------------------------------------------------------------------ wide ATR stop
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
        """Fixed wide ATR stop: stop price = entry price minus atr_mult * ATR(14, 4h) of the
        last candle closed before entry, capped by the hard `stoploss`. Freqtrade never lowers a
        stop, so this is set once at entry and then stays put (no trailing). A 1R breakeven lock
        was tested and rejected: it stopped out pullbacks on trades that later reached ROI."""
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or dataframe.empty:
            return None
        closed = dataframe.loc[dataframe["date"] < trade.open_date_utc]
        if closed.empty:
            return None
        atr = float(closed["atr"].iloc[-1])
        if not np.isfinite(atr) or atr <= 0:
            return None
        open_rate = float(trade.open_rate)
        stop_price = max(open_rate - self.sell_atr_mult.value * atr, open_rate * (1.0 + self.stoploss))
        if stop_price >= current_rate:
            return None
        return stoploss_from_absolute(stop_price, current_rate, is_short=trade.is_short)
