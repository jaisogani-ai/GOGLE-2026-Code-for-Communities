"""Stock-out arithmetic. Pure numpy, vectorised over scenario samples."""
from __future__ import annotations

import numpy as np

EPS = 1e-9


def stockout_hours(stock0, rate, deliveries, horizon):
    """Hours in [0, horizon] with zero stock.

    stock0, rate: arrays (K,) or scalars. deliveries: list of (time, qty), each (K,) or scalar,
    times in hours from now. Deliveries at or after the horizon do not help.
    """
    stock0 = np.atleast_1d(np.asarray(stock0, dtype=float))
    k = stock0.shape[0]
    rate = np.maximum(np.broadcast_to(np.asarray(rate, dtype=float), (k,)), EPS)
    if deliveries:
        times = np.stack([np.broadcast_to(np.asarray(t, dtype=float), (k,)) for t, _ in deliveries], axis=1)
        qtys = np.stack([np.broadcast_to(np.asarray(q, dtype=float), (k,)) for _, q in deliveries], axis=1)
        order = np.argsort(times, axis=1)
        times = np.clip(np.take_along_axis(times, order, axis=1), 0.0, horizon)
        qtys = np.take_along_axis(qtys, order, axis=1)
    else:
        times = np.zeros((k, 0))
        qtys = np.zeros((k, 0))
    stock = stock0.copy()
    prev = np.zeros(k)
    zero = np.zeros(k)
    for j in range(times.shape[1]):
        dt = times[:, j] - prev
        zero += np.maximum(dt - stock / rate, 0.0)
        stock = np.maximum(stock - rate * dt, 0.0) + qtys[:, j]
        prev = times[:, j]
    zero += np.maximum((horizon - prev) - stock / rate, 0.0)
    return zero
