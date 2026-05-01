"""多因子策略实现：计算评分、选股与再平衡交易逻辑。"""
import math

import backtrader as bt

from utils.logger import setup_logger, get_trade_logger


class MultiFactorPandasData(bt.feeds.PandasData):
    lines = ("mom20", "mom60", "vol20", "liq20")
    params = (
        ("mom20", -1),
        ("mom60", -1),
        ("vol20", -1),
        ("liq20", -1),
    )


class PriceVolumeMultiFactorStrategy(bt.Strategy):
    params = (
        ("holding_count", 5),
        ("rank_buffer", 2),
        ("score_delta", 0.30),
        ("min_hold_days", 5),
        ("rebalance_cooldown", 2),
        ("w_mom20", 0.35),
        ("w_mom60", 0.25),
        ("w_vol20", -0.20),
        ("w_liq20", 0.20),
    )

    def __init__(self):
        self.logger = setup_logger(self.__class__.__name__)
        self.trade_logger = get_trade_logger()
        self.hold_days = {}
        self.last_rebalance_bar = -9999

    @staticmethod
    def _zscore_map(raw_map):
        values = list(raw_map.values())
        if not values:
            return {}
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / max(len(values), 1)
        std = math.sqrt(variance)
        if std == 0:
            return {k: 0.0 for k in raw_map}
        return {k: (v - mean) / std for k, v in raw_map.items()}

    def _collect_scores(self):
        mom20 = {}
        mom60 = {}
        vol20 = {}
        liq20 = {}

        for data in self.datas:
            if len(data) < 65:
                continue
            m20 = data.mom20[0]
            m60 = data.mom60[0]
            v20 = data.vol20[0]
            l20 = data.liq20[0]
            if any(math.isnan(x) for x in [m20, m60, v20, l20]):
                continue
            mom20[data] = m20
            mom60[data] = m60
            vol20[data] = v20
            liq20[data] = l20

        z_mom20 = self._zscore_map(mom20)
        z_mom60 = self._zscore_map(mom60)
        z_vol20 = self._zscore_map(vol20)
        z_liq20 = self._zscore_map(liq20)

        scores = {}
        for data in z_mom20:
            if data not in z_mom60 or data not in z_vol20 or data not in z_liq20:
                continue
            score = (
                self.p.w_mom20 * z_mom20[data]
                + self.p.w_mom60 * z_mom60[data]
                + self.p.w_vol20 * z_vol20[data]
                + self.p.w_liq20 * z_liq20[data]
            )
            scores[data] = score
        return scores

    def _needs_rebalance(self, ranked):
        if len(self) - self.last_rebalance_bar < self.p.rebalance_cooldown:
            return False

        current_holding = [d for d in self.datas if self.getposition(d).size > 0]
        if not current_holding:
            return True

        rank_map = {d: idx for idx, (d, _) in enumerate(ranked, start=1)}
        worst_holding_score = min((score for d, score in ranked if d in current_holding), default=None)
        best_candidate_score = None
        for d, s in ranked:
            if d not in current_holding:
                best_candidate_score = s
                break

        for d in current_holding:
            rank = rank_map.get(d, 9999)
            if rank > self.p.holding_count + self.p.rank_buffer and self.hold_days.get(d, 0) >= self.p.min_hold_days:
                return True

        if worst_holding_score is not None and best_candidate_score is not None:
            if best_candidate_score - worst_holding_score > self.p.score_delta:
                return True
        return False

    def next(self):
        for data in self.datas:
            if self.getposition(data).size > 0:
                self.hold_days[data] = self.hold_days.get(data, 0) + 1
            else:
                self.hold_days[data] = 0

        scores = self._collect_scores()
        if len(scores) < self.p.holding_count:
            return

        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        if not self._needs_rebalance(ranked):
            return

        target_datas = [data for data, _ in ranked[: self.p.holding_count]]
        target_set = set(target_datas)
        target_pct = 1.0 / self.p.holding_count

        # 先卖后买，降低现金不足概率
        for data in self.datas:
            pos = self.getposition(data).size
            if pos <= 0:
                continue
            if data not in target_set and self.hold_days.get(data, 0) >= self.p.min_hold_days:
                self.order_target_percent(data=data, target=0.0)
                self.trade_logger.info("多因子卖出,%s,score_out", data._name)

        for data in target_datas:
            self.order_target_percent(data=data, target=target_pct)
            self.trade_logger.info("多因子调仓,%s,target=%.3f", data._name, target_pct)

        self.last_rebalance_bar = len(self)
