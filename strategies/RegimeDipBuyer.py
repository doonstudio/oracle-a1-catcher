"""
RegimeDipBuyer - regime-gated dip buying (1h entries, 4h pair trend, 1d BTC regime).

The market regime is read from BTC/USDT on the daily chart: trading is allowed only while the
BTC daily close sits above a rising EMA50 and the EMA20 is above the EMA50; otherwise the bot
stays in cash. When the regime is ON, a pair is bought after a short-term dip - 1h RSI(14)
falling below a threshold - as long as the pair itself still trades above both its 4h EMA50 and
its longer 4h EMA (dip inside a genuine uptrend). Positions are closed at a modest ATR(4h)
profit target measured from the entry price or after a maximum holding time (custom_exit), when
the pair's 4h close loses its 4h EMA or the BTC regime switches off (exit signal), or by a wider fixed
ATR(4h) stop set at entry (custom_stoploss, capped by the hard stoploss). Spot only, limit
orders, no lookahead.
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


class RegimeDipBuyer(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short: bool = False
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # ROI keys are minutes: loose cap only; exits are handled by custom_exit / exit signals.
    minimal_roi = {
        "0": 0.30,
    }

    # Hard cap; the effective stop is the ATR-based custom stoploss (tighter).
    stoploss = -0.10
    use_custom_stoploss = True
    trailing_stop = False

    # Longest lookback: EMA200 on 4h (informative frames get startup_candle_count in their own
    # timeframe), so 300 candles gives converged 4h EMAs. 1h data loses only 300h of warm-up.
    startup_candle_count: int = 300

    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }
    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    # ---- Hyperopt parameters (buy space) ----
    # 1h RSI must be below this value (the "dip").
    buy_rsi_dip = IntParameter(25, 42, default=35, space="buy", optimize=True)
    # Long 4h EMA the pair must be above (in addition to the 4h EMA50) - the "uptrend".
    buy_ema_4h = CategoricalParameter([100, 150, 200], default=100, space="buy", optimize=True)
    # Fast daily EMA for the BTC regime (must be above the daily EMA50).
    buy_btc_ema_fast = CategoricalParameter([10, 20, 30], default=20, space="buy", optimize=True)

    # ---- Hyperopt parameters (sell space) ----
    # Profit target: entry + N x ATR(14, 4h). Modest target = high hit rate, positive median.
    sell_atr_target = DecimalParameter(1.0, 3.0, default=1.5, decimals=1, space="sell", optimize=True)
    # Stop: entry - N x ATR(14, 4h) (never wider than the hard stoploss). Wider than the target
    # on purpose: a dip inside an uptrend needs room to complete before it resolves upwards.
    sell_atr_stop = DecimalParameter(1.5, 3.5, default=2.5, decimals=1, space="sell", optimize=True)
    # Maximum holding time in hours; the trade is closed at market structure regardless of P/L.
    sell_max_hold_h = IntParameter(48, 168, default=96, space="sell", optimize=True)
    # Trend-loss exit: which 4h EMA a completed 4h close must fall below to invalidate the dip.
    sell_exit_ema = CategoricalParameter([50, 100], default=50, space="sell", optimize=True)
    # Fixed buffer under the exit EMA (fraction) to avoid exits on a marginal poke through it.
    exit_ema_buffer: float = 0.01

    @property
    def plot_config(self):
        return {
            "main_plot": {
                "ema100_4h": {"color": "orange"},
            },
            "subplots": {
                "RSI": {"rsi": {"color": "red"}},
                "REGIME": {"btc_regime": {"color": "green"}},
            },
        }

    # ------------------------------------------------------------------ informative BTC 1d
    @informative("1d", "BTC/{stake}")
    def populate_indicators_btc_1d(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        for length in (10, 20, 30, 50):
            dataframe[f"ema{length}"] = ta.EMA(dataframe, timeperiod=length)
        # slope of the daily EMA50 computed on the daily frame itself (previous day -> no lookahead)
        dataframe["ema50_up"] = (dataframe["ema50"] > dataframe["ema50"].shift(1)).astype(int)
        return dataframe

    # ------------------------------------------------------------------ informative 4h (own pair)
    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        for length in (50, 100, 150, 200):
            dataframe[f"ema{length}"] = ta.EMA(dataframe, timeperiod=length)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        return dataframe

    # ------------------------------------------------------------------ indicators 1h
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        fast = f"btc_usdt_ema{self.buy_btc_ema_fast.value}_1d"
        regime = (
            (dataframe["btc_usdt_close_1d"] > dataframe["btc_usdt_ema50_1d"])
            & (dataframe[fast] > dataframe["btc_usdt_ema50_1d"])
            & (dataframe["btc_usdt_ema50_up_1d"] > 0)
        )
        dataframe["btc_regime"] = regime.fillna(False).astype(int)
        return dataframe

    # ------------------------------------------------------------------ entry
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        ema_col = f"ema{self.buy_ema_4h.value}_4h"
        conditions = [
            (dataframe["btc_regime"] > 0),
            (dataframe["close"] > dataframe["ema50_4h"]),
            (dataframe["close"] > dataframe[ema_col]),
            (dataframe["rsi"] < self.buy_rsi_dip.value),
            (dataframe["atr_4h"] > 0),
            (dataframe["volume"] > 0),
        ]
        dataframe.loc[np.logical_and.reduce(conditions), ["enter_long", "enter_tag"]] = (1, "regime_dip")
        return dataframe

    # ------------------------------------------------------------------ exit signal
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        ema_col = f"ema{self.sell_exit_ema.value}_4h"
        trend_lost = dataframe["close_4h"] < dataframe[ema_col] * (1.0 - self.exit_ema_buffer)
        regime_off = dataframe["btc_regime"] == 0

        dataframe.loc[trend_lost & (dataframe["volume"] > 0), ["exit_long", "exit_tag"]] = (1, "ema4h_lost")
        dataframe.loc[regime_off & ~trend_lost & (dataframe["volume"] > 0), ["exit_long", "exit_tag"]] = (
            1,
            "regime_off",
        )
        return dataframe

    # ------------------------------------------------------------------ helpers
    def _entry_atr(self, pair: str, trade: Trade) -> float | None:
        """ATR(14, 4h) as known on the signal candle (last 1h candle closed before the trade opened).
        Cached per trade in custom data so the lookup runs once."""
        cached = trade.get_custom_data("entry_atr")
        if cached is not None:
            return float(cached)
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or dataframe.empty:
            return None
        rows = dataframe.loc[dataframe["date"] < trade.open_date_utc]
        if rows.empty:
            return None
        atr = rows["atr_4h"].iloc[-1]
        if atr is None or np.isnan(atr) or atr <= 0:
            return None
        trade.set_custom_data("entry_atr", float(atr))
        return float(atr)

    # ------------------------------------------------------------------ ATR profit target
    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ):
        atr = self._entry_atr(pair, trade)
        if atr is None:
            return None
        target = trade.open_rate + self.sell_atr_target.value * atr
        if current_rate >= target:
            return "atr_target"
        if current_time - trade.open_date_utc >= timedelta(hours=int(self.sell_max_hold_h.value)):
            return "max_hold"
        return None

    # ------------------------------------------------------------------ ATR stop (fixed at entry)
    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ):
        atr = self._entry_atr(pair, trade)
        if atr is None:
            return None
        stop_rate = trade.open_rate - self.sell_atr_stop.value * atr
        return stoploss_from_absolute(stop_rate, current_rate, is_short=trade.is_short)
