"""多因子策略实现：截面处理后的因子加权评分、目标权重与再平衡。"""
import math

import backtrader as bt

from utils.logger import get_backtest_logger, get_trade_logger

# 与 ``data/features/price_factors`` 列名一致；截面结果已写回同名列（z-score），rollvol20 为原始 20 日收益波动
_SCORE_LINES = ("mom20", "mom60", "vol20", "liq20", "rev20", "dvol20", "amihud20", "rollvol20")
_MF_FEED_LINES = _SCORE_LINES


class MultiFactorPandasData(bt.feeds.PandasData):
    """行情 + 因子列（截面 z 已写回）+ rollvol20（目标波动用）。"""

    lines = _MF_FEED_LINES
    params = tuple((name, name) for name in _MF_FEED_LINES)


class PriceVolumeMultiFactorStrategy(bt.Strategy):
    params = (
        ("holding_count", 12),
        ("rank_buffer", 3),
        ("score_delta", 0.22),
        ("min_hold_days", 5),
        ("rebalance_cooldown", 2),
        ("w_mom20", 0.24),
        ("w_mom60", 0.18),
        ("w_vol20", -0.26),
        ("w_liq20", 0.14),
        ("w_rev20", 0.10),
        ("w_dvol20", -0.12),
        ("w_amihud20", -0.10),
        ("weight_scheme", "exp_score"),
        ("max_single_weight", 0.12),
        ("min_single_weight", 0.02),
        ("target_vol_enabled", True),
        ("target_vol_annual", 0.18),
        ("defense_enabled", True),
        ("defense_dd_trigger", 0.12),
        ("defense_gross_exposure", 0.58),
        ("defense_dd_deep", 0.22),
        ("defense_gross_exposure_deep", 0.38),
    )

    def __init__(self):
        self.logger = get_backtest_logger()
        self.trade_logger = get_trade_logger()
        self.hold_days = {}
        self.last_rebalance_bar = -9999
        self._equity_peak = 0.0
        self._last_applied_gross = 1.0

    def _trade_log_date(self) -> str:
        try:
            return self.datetime.date(0).isoformat()
        except Exception:
            return ""

    def notify_order(self, order):
        """每笔委托状态变化写入 trading.log（与终端无关，专供对账/分析）。"""
        if order.status in (order.Submitted, order.Accepted):
            return
        dt = self._trade_log_date()
        sym = getattr(order.data, "_name", "") if order.data is not None else ""
        if order.status == order.Completed:
            side = "BUY" if order.isbuy() else "SELL"
            ex = order.executed
            self.trade_logger.info(
                "ORDER_COMPLETED,%s,%s,%s,ref=%s,size=%s,price=%.4f,value=%.4f,comm=%.4f",
                dt,
                sym,
                side,
                order.ref,
                ex.size,
                ex.price,
                ex.value,
                ex.comm,
            )
        else:
            self.trade_logger.info(
                "ORDER_END,%s,%s,ref=%s,status=%s,req_size=%s",
                dt,
                sym,
                order.ref,
                order.getstatusname(),
                order.size,
            )

    def notify_trade(self, trade):
        """单标的仓位从开到平的一次汇总（平仓时写 trading.log）。"""
        if not trade.isclosed:
            return
        dt = self._trade_log_date()
        self.trade_logger.info(
            "TRADE_CLOSED,%s,%s,pnl=%.4f,pnlcomm=%.4f,commission=%.4f,barlen=%s",
            dt,
            trade.getdataname(),
            trade.pnl,
            trade.pnlcomm,
            trade.commission,
            trade.barlen,
        )

    def _gross_exposure_from_drawdown(self) -> float:
        """按净值相对历史高点的回撤，返回目标总仓位比例 (0, 1]。"""
        if not self.p.defense_enabled:
            return 1.0
        v = float(self.broker.getvalue())
        if v > self._equity_peak:
            self._equity_peak = v
        if self._equity_peak <= 0:
            return 1.0
        dd = 1.0 - v / self._equity_peak
        if dd >= self.p.defense_dd_deep:
            return float(self.p.defense_gross_exposure_deep)
        if dd >= self.p.defense_dd_trigger:
            return float(self.p.defense_gross_exposure)
        return 1.0

    def _line_val(self, data, name: str):
        line = getattr(data.lines, name, None)
        if line is None:
            line = getattr(data, name, None)
        if line is None:
            return float("nan")
        try:
            return float(line[0])
        except (TypeError, ValueError, IndexError):
            return float("nan")

    def _collect_scores(self):
        """截面因子已为 z-score（列 mom20…），此处仅线性加权。"""
        score_names = ("mom20", "mom60", "vol20", "liq20", "rev20", "dvol20", "amihud20")
        scores = {}
        for data in self.datas:
            if len(data) < 65:
                continue
            vals = []
            for nm in score_names:
                x = self._line_val(data, nm)
                vals.append(x)
            if any(math.isnan(x) for x in vals):
                continue
            (
                z20,
                z60,
                zv,
                zl,
                zr,
                zdv,
                za,
            ) = vals
            score = (
                self.p.w_mom20 * z20
                + self.p.w_mom60 * z60
                + self.p.w_vol20 * zv
                + self.p.w_liq20 * zl
                + self.p.w_rev20 * zr
                + self.p.w_dvol20 * zdv
                + self.p.w_amihud20 * za
            )
            if math.isnan(score):
                continue
            scores[data] = score
        return scores

    def _vol_target_scale(self, target_datas: list) -> float:
        if not self.p.target_vol_enabled or not target_datas:
            return 1.0
        vols = []
        for d in target_datas:
            if len(d) < 22:
                continue
            v = self._line_val(d, "rollvol20")
            if not math.isnan(v) and v > 0:
                vols.append(v)
        if not vols:
            return 1.0
        av = sum(vols) / len(vols)
        est_ann = av * math.sqrt(252.0)
        if est_ann < 1e-6:
            return 1.0
        return min(1.0, float(self.p.target_vol_annual) / est_ann)

    def _weights_from_scores(self, ranked_slice: list, gross: float) -> dict:
        """在 top slice 上分配目标资金比例，总和为 gross。"""
        items = list(ranked_slice)
        if not items:
            return {}
        if str(self.p.weight_scheme).lower() == "equal":
            each = gross / max(len(items), 1)
            return {d: each for d, _ in items}

        clipped = [(d, min(3.0, max(-3.0, float(s)))) for d, s in items]
        exps = [math.exp(s) for _, s in clipped]
        s_ex = sum(exps) or 1.0
        raw = {d: gross * e / s_ex for (d, _), e in zip(clipped, exps)}
        cap = float(self.p.max_single_weight)
        floor = float(self.p.min_single_weight)
        for _ in range(12):
            overs = [d for d, w in raw.items() if w > cap + 1e-9]
            if not overs:
                break
            exc = sum(raw[d] - cap for d in overs)
            for d in overs:
                raw[d] = cap
            others = [d for d in raw if d not in overs]
            if not others:
                break
            add = exc / len(others)
            for d in others:
                raw[d] += add
        for d in list(raw.keys()):
            if raw[d] < floor:
                raw[d] = floor
        ssum = sum(raw.values())
        if ssum > 1e-9:
            raw = {d: w * gross / ssum for d, w in raw.items()}
        return raw

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
        gross = self._gross_exposure_from_drawdown()
        need_risk_resize = self.p.defense_enabled and abs(gross - self._last_applied_gross) >= 0.02

        if not need_risk_resize and not self._needs_rebalance(ranked):
            return

        target_slice = ranked[: self.p.holding_count]
        target_datas = [d for d, _ in target_slice]
        target_set = set(target_datas)
        wmap = self._weights_from_scores(target_slice, gross)
        vs = self._vol_target_scale(target_datas)
        if vs < 1.0 - 1e-6:
            wmap = {d: w * vs for d, w in wmap.items()}
            s2 = sum(wmap.values())
            if s2 > 1e-9:
                wmap = {d: w * gross / s2 for d, w in wmap.items()}

        if need_risk_resize and gross < self._last_applied_gross - 0.01:
            peak = self._equity_peak
            dd_pct = (1.0 - float(self.broker.getvalue()) / peak) * 100.0 if peak > 0 else 0.0
            self.trade_logger.info(
                "多因子风控减仓,%s,gross=%.2f,dd_from_peak=%.1f%%",
                self._trade_log_date(),
                gross,
                dd_pct,
            )

        for data in self.datas:
            pos = self.getposition(data).size
            if pos <= 0:
                continue
            if data not in target_set and self.hold_days.get(data, 0) >= self.p.min_hold_days:
                self.order_target_percent(data=data, target=0.0)
                self.trade_logger.info(
                    "多因子卖出,%s,%s,score_out", self._trade_log_date(), data._name
                )

        for data in target_datas:
            tgt = wmap.get(data, 0.0)
            self.order_target_percent(data=data, target=tgt)
            self.trade_logger.info(
                "多因子调仓,%s,%s,target=%.4f,gross=%.2f,vol_scale=%.2f",
                self._trade_log_date(),
                data._name,
                tgt,
                gross,
                vs,
            )

        self._last_applied_gross = gross
        self.last_rebalance_bar = len(self)
