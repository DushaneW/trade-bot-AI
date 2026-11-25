"""Build a connector from a stored account record."""
from __future__ import annotations

from ..models import Market
from .base import Connector, ExecResult, KeyCheck, StopState
from .sim import SimConnector

__all__ = ["Connector", "ExecResult", "KeyCheck", "StopState", "build", "VENUES"]

VENUES = {
    "crypto": ["binance", "bybit", "okx", "kraken", "coinbase", "kucoin", "bitget", "gate", "mexc"],
    "stocks": ["alpaca"],
    "forex": ["oanda"],
}

VENUE_NAMES = {
    "binance": "Binance", "bybit": "Bybit", "okx": "OKX", "kraken": "Kraken", "coinbase": "Coinbase",
    "kucoin": "KuCoin", "bitget": "Bitget", "gate": "Gate.io", "mexc": "MEXC", "alpaca": "Alpaca", "oanda": "OANDA",
}

# Exchanges that need a passphrase alongside key and secret.
NEEDS_PASSPHRASE = {"okx", "kucoin", "bitget"}

SUGGESTED_MARKETS = {
    "crypto": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT"],
    "stocks": ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "TSLA", "META"],
    "forex": ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CHF", "EUR_GBP"],
}


def build(account: dict, secrets: dict) -> Connector:
    venue = account["venue"]
    market = Market(account["market"])
    sandbox = bool(account.get("sandbox", True))
    tf = account.get("timeframe", "5m")
    if venue == "sim":
        return SimConnector(market)
    if market == Market.CRYPTO:
        from .crypto import CryptoConnector
        return CryptoConnector(venue, secrets.get("api_key", ""), secrets.get("secret", ""),
                               secrets.get("password", ""), sandbox, account.get("timeframe", "1m"))
    if market == Market.STOCKS:
        from .stocks import AlpacaConnector
        return AlpacaConnector(secrets.get("api_key", ""), secrets.get("secret", ""), sandbox, tf)
    if market == Market.FOREX:
        from .forex import OandaConnector
        return OandaConnector(secrets.get("api_key", ""), secrets.get("account_id", ""), sandbox, tf)
    raise ValueError(f"Unsupported market: {market}")
