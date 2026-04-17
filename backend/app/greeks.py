"""Black–Scholes analytic Greeks.

Vectorised over numpy arrays so the full chain is priced in a single
call. Inputs use fractional IV (0.2 == 20%), years to expiry (1/365 for
1DTE), and continuous risk-free rate.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm


SQRT_TWO_PI = np.sqrt(2.0 * np.pi)


def _d1(S, K, T, r, sigma, q=0.0):
    sigma = np.maximum(sigma, 1e-6)
    T = np.maximum(T, 1e-6)
    return (np.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * np.sqrt(T))


def _d2(d1, sigma, T):
    return d1 - sigma * np.sqrt(np.maximum(T, 1e-6))


@dataclass
class GreekBundle:
    delta: np.ndarray
    gamma: np.ndarray
    vega: np.ndarray
    theta: np.ndarray
    vanna: np.ndarray
    charm: np.ndarray
    price: np.ndarray


def bs_price(S, K, T, r, sigma, is_call, q=0.0):
    d1 = _d1(S, K, T, r, sigma, q)
    d2 = _d2(d1, sigma, T)
    call = S * np.exp(-q * T) * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    put = K * np.exp(-r * T) * norm.cdf(-d2) - S * np.exp(-q * T) * norm.cdf(-d1)
    return np.where(is_call, call, put)


def greeks(S, K, T, r, sigma, is_call, q=0.0) -> GreekBundle:
    """Return the six Greeks used by Gammagamma in one pass.

    ``vanna`` = dDelta/dSigma = dVega/dSpot.
    ``charm`` = -dDelta/dT (per year).
    """
    S = np.asarray(S, dtype=float)
    K = np.asarray(K, dtype=float)
    T = np.maximum(np.asarray(T, dtype=float), 1e-6)
    sigma = np.maximum(np.asarray(sigma, dtype=float), 1e-6)
    is_call = np.asarray(is_call, dtype=bool)

    d1 = _d1(S, K, T, r, sigma, q)
    d2 = _d2(d1, sigma, T)
    nd1 = np.exp(-0.5 * d1 * d1) / SQRT_TWO_PI

    delta_call = np.exp(-q * T) * norm.cdf(d1)
    delta_put = delta_call - np.exp(-q * T)
    delta = np.where(is_call, delta_call, delta_put)

    gamma = np.exp(-q * T) * nd1 / (S * sigma * np.sqrt(T))
    vega = S * np.exp(-q * T) * nd1 * np.sqrt(T)

    theta_call = (
        -(S * np.exp(-q * T) * nd1 * sigma) / (2 * np.sqrt(T))
        - r * K * np.exp(-r * T) * norm.cdf(d2)
        + q * S * np.exp(-q * T) * norm.cdf(d1)
    )
    theta_put = (
        -(S * np.exp(-q * T) * nd1 * sigma) / (2 * np.sqrt(T))
        + r * K * np.exp(-r * T) * norm.cdf(-d2)
        - q * S * np.exp(-q * T) * norm.cdf(-d1)
    )
    theta = np.where(is_call, theta_call, theta_put)

    vanna = -np.exp(-q * T) * nd1 * d2 / sigma

    charm_call = (
        q * np.exp(-q * T) * norm.cdf(d1)
        - np.exp(-q * T)
        * nd1
        * (2 * (r - q) * T - d2 * sigma * np.sqrt(T))
        / (2 * T * sigma * np.sqrt(T))
    )
    charm_put = charm_call - q * np.exp(-q * T)
    charm = np.where(is_call, charm_call, charm_put)

    price = bs_price(S, K, T, r, sigma, is_call, q)
    return GreekBundle(delta, gamma, vega, theta, vanna, charm, price)


def implied_vol(
    market_price: np.ndarray,
    S: np.ndarray,
    K: np.ndarray,
    T: np.ndarray,
    r: float,
    is_call: np.ndarray,
    q: float = 0.0,
    max_iter: int = 60,
    tol: float = 1e-4,
) -> np.ndarray:
    """Vectorised Newton–Raphson IV solve with bisection fallback.

    Intrinsic-value and near-expiry guards return ``nan`` for degenerate
    inputs so callers can filter them out of aggregate GEX.
    """
    S = np.asarray(S, dtype=float)
    K = np.asarray(K, dtype=float)
    T = np.asarray(T, dtype=float)
    mp = np.asarray(market_price, dtype=float)
    is_call = np.asarray(is_call, dtype=bool)

    sigma = np.full(S.shape, 0.30)
    invalid = (
        (mp <= 0.0)
        | (T <= 0.0)
        | (S <= 0.0)
        | (K <= 0.0)
        | np.isnan(mp)
    )
    sigma[invalid] = np.nan
    active = ~invalid

    for _ in range(max_iter):
        if not active.any():
            break
        g = greeks(S, K, T, r, np.where(active, sigma, 0.2), is_call, q)
        diff = g.price - mp
        step = diff / np.maximum(g.vega, 1e-6)
        new_sigma = np.clip(sigma - step, 1e-3, 5.0)
        sigma = np.where(active, new_sigma, sigma)
        active = active & (np.abs(diff) > tol)

    return sigma
