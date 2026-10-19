"""L2-regularised logistic regression fitted by Newton–Raphson (IRLS) on standardised features. CPU, numpy only."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.float64]


@dataclass(frozen=True)
class LogisticModel:
    mean: tuple[float, ...]
    scale: tuple[float, ...]
    weights: tuple[float, ...]       # intercept first
    l2: float

    def predict(self, x: Array) -> Array:
        z = (x - np.array(self.mean)) / np.array(self.scale)
        logits = self.weights[0] + z @ np.array(self.weights[1:])
        return np.asarray(1 / (1 + np.exp(-np.clip(logits, -30, 30))), dtype=np.float64)


def fit(x: Array, y: Array, *, l2: float = 1.0, iterations: int = 50) -> LogisticModel:
    if x.ndim != 2 or len(x) != len(y) or len(y) == 0:
        raise ValueError("x must be (n, k) with n labels")
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale == 0] = 1.0
    z = np.column_stack([np.ones(len(x)), (x - mean) / scale])
    w = np.zeros(z.shape[1])
    penalty = np.eye(z.shape[1]) * l2
    penalty[0, 0] = 0.0                                    # the intercept is not shrunk
    for _ in range(iterations):
        p = 1 / (1 + np.exp(-np.clip(z @ w, -30, 30)))
        gradient = z.T @ (p - y) + penalty @ w
        hessian = (z * (p * (1 - p))[:, None]).T @ z + penalty
        step = np.linalg.solve(hessian, gradient)
        w -= step
        if np.max(np.abs(step)) < 1e-10:
            break
    return LogisticModel(tuple(map(float, mean)), tuple(map(float, scale)), tuple(map(float, w)), l2)
