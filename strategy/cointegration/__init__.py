"""
QuantLuna — strategy.cointegration package

Exports publice Sprint 9:
  EngleGrangerTest, EGResult
  JohansenTest, JohansenResult
  ResidualDiagnostics, ResidualReport
  CointegrationValidator, ValidationReport
"""

from .engle_granger import EngleGrangerTest, EGResult
from .johansen import JohansenTest, JohansenResult
from .residual_diagnostics import ResidualDiagnostics, ResidualReport
from .validator import CointegrationValidator, ValidatorConfig, ValidationReport

__all__ = [
    "EngleGrangerTest",
    "EGResult",
    "JohansenTest",
    "JohansenResult",
    "ResidualDiagnostics",
    "ResidualReport",
    "CointegrationValidator",
    "ValidatorConfig",
    "ValidationReport",
]
