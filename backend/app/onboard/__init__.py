"""Add a company by ticker: SEC resolve, XBRL metrics, maturity wall, snapshot."""

from app.onboard.service import OnboardError, OnboardResult, onboard_ticker

__all__ = ["OnboardError", "OnboardResult", "onboard_ticker"]
