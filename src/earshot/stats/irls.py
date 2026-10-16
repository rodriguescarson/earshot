"""Logistic regression by iteratively reweighted least squares (Newton's method), with a small
ridge penalty for stability. Used for pass difficulty; no scikit-learn dependency."""

from __future__ import annotations

import numpy as np


def fit(
    x: np.ndarray, y: np.ndarray, l2: float = 1e-4, iterations: int = 50, tol: float = 1e-9
) -> np.ndarray:
    """Coefficients (intercept first) for P(y=1) = sigmoid([1, x] @ beta)."""
    xb = np.column_stack([np.ones(len(x)), x])
    beta = np.zeros(xb.shape[1])
    penalty = l2 * np.eye(xb.shape[1])
    penalty[0, 0] = 0.0
    for _ in range(iterations):
        p = 1.0 / (1.0 + np.exp(-(xb @ beta)))
        w = np.clip(p * (1 - p), 1e-9, None)
        grad = xb.T @ (y - p) - penalty @ beta
        hess = (xb * w[:, None]).T @ xb + penalty
        step = np.linalg.solve(hess, grad)
        beta = beta + step
        if np.max(np.abs(step)) < tol:
            break
    return beta


def predict(beta: np.ndarray, x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-(beta[0] + x @ beta[1:])))


def log_loss(y: np.ndarray, p: np.ndarray) -> float:
    p = np.clip(p, 1e-12, 1 - 1e-12)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def calibration_error(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    """Expected calibration error over equal-count bins of predicted probability."""
    order = np.argsort(p)
    err = 0.0
    for chunk in np.array_split(order, bins):
        if len(chunk):
            err += len(chunk) / len(p) * abs(float(np.mean(p[chunk])) - float(np.mean(y[chunk])))
    return err
