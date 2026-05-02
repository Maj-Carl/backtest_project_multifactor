"""多因子截面处理、IC 报告与面板工具。"""
from data.factors.panel import apply_cross_section_to_multi_data
from data.factors.ic_report import maybe_write_factor_ic_report

__all__ = ["apply_cross_section_to_multi_data", "maybe_write_factor_ic_report"]
