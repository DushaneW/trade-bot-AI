"""Vectorised technical indicators on numpy arrays."""
from __future__ import annotations

import numpy as np

from .models import Candle


def arrays(candles: list[Candle]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """High, low, close and volume series."""
    n = len(candles)
    high = np.fromiter((c.high for c in candles), float, n)
    low = np.fromiter((c.low for c in candles), float, n)
    close = np.fromiter((c.close for c in candles), float, n)
    volume = np.fromiter((c.volume for c in candles), float, n)
    return high, low, close, volume


def efficiency_ratio(close: np.ndarray, n: int = 30) -> float:
    """Kaufman efficiency: 1 = straight line, 0 = pure noise."""
    w = close[-n - 1:]
    path = np.abs(np.diff(w)).sum()
    return 0.0 if path == 0 else float(abs(w[-1] - w[0]) / path)


def rsi_series(close: np.ndarray, n: int = 14) -> np.ndarray:
    d = np.diff(close, prepend=close[0])
    gain = ema(np.clip(d, 0, None), n)
    loss = ema(np.clip(-d, 0, None), n)
    rs = np.divide(gain, loss, out=np.full_like(gain, np.inf), where=loss > 0)
    return 100 - 100 / (1 + rs)


def ema(x: np.ndarray, n: int) -> np.ndarray:
    alpha = 2 / (n + 1)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = alpha * x[i] + (1 - alpha) * out[i - 1]
    return out


def sma(x: np.ndarray, n: int) -> np.ndarray:
    c = np.cumsum(np.insert(x, 0, 0.0))
    out = np.full_like(x, np.nan)
    out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def rsi(close: np.ndarray, n: int = 14) -> float:
    d = np.diff(close[-(n * 4):])
    gain = ema(np.clip(d, 0, None), n)[-1]
    loss = ema(np.clip(-d, 0, None), n)[-1]
    if loss == 0:
        return 100.0
    return 100 - 100 / (1 + gain / loss)


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> np.ndarray:
    prev = np.roll(close, 1)
    prev[0] = close[0]
    tr = np.maximum(high - low, np.maximum(abs(high - prev), abs(low - prev)))
    return ema(tr, n)


def zscore(close: np.ndarray, n: int = 20) -> float:
    w = close[-n:]
    sd = w.std()
    return 0.0 if sd == 0 else float((w[-1] - w.mean()) / sd)


def log_returns(close: np.ndarray) -> np.ndarray:
    return np.diff(np.log(close))


def correlations(closes: dict[str, np.ndarray], n: int = 100) -> dict[tuple[str, str], float]:
    """Pairwise correlation of the last `n` log returns, both orderings of every pair."""
    keys = [k for k, c in closes.items() if len(c) > 10]
    if len(keys) < 2:
        return {}
    m = min(n, min(len(closes[k]) for k in keys) - 1)
    rets = np.array([log_returns(closes[k][-m - 1:]) for k in keys])
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = np.corrcoef(rets)
    out = {}
    for i, a in enumerate(keys):
        for j, b in enumerate(keys):
            if i != j:
                v = corr[i, j]
                out[(a, b)] = float(v) if np.isfinite(v) else 0.0
    return out
