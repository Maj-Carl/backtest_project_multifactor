"""回测引擎封装：统一经纪商、策略、数据和分析器装配。"""
# backtest/backtest_engine.py
import backtrader as bt
from config.config import Config


class BacktestEngine:
    def __init__(self):
        self.cerebro = bt.Cerebro()
        self._setup_broker()

    def _setup_broker(self):
        self.cerebro.broker.setcash(Config.INITIAL_CASH)
        self.cerebro.broker.setcommission(commission=Config.COMMISSION)
        self.cerebro.broker.set_slippage_perc(Config.SLIPPAGE)

    def add_strategy(self, strategy_class, **kwargs):
        self.cerebro.addstrategy(strategy_class, **kwargs)

    def add_data(self, data_feed):
        self.cerebro.adddata(data_feed)

    def add_sizer(self, sizer_class=bt.sizers.FixedSize, **kwargs):
        self.cerebro.addsizer(sizer_class, **kwargs)

    def add_analyzers(self):
        self.cerebro.addanalyzer(bt.analyzers.AnnualReturn, _name='AnnualReturn')
        self.cerebro.addanalyzer(bt.analyzers.SharpeRatio,
                                 riskfreerate=0.003,
                                 annualize=True,
                                 _name='SharpeRatio')
        self.cerebro.addanalyzer(bt.analyzers.DrawDown, _name='DrawDown')
        self.cerebro.addanalyzer(bt.analyzers.PyFolio, _name='pyfolio')

    def run_backtest(self):
        return self.cerebro.run()