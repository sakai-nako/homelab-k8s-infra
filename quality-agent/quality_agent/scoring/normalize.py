"""メトリクスを 0-100 に正規化する関数群.

docs/quality-model.md「スコアモデル方針」の 3 方式に対応:
- linear : しきい値 [bad, good] を直線補間
- logarithmic : 件数系 (CVE 数, バグ数) を log スケールで補間
- binary : 存在/不在を 0/100

いずれも「100 が良い」向きに揃える。範囲外は 0-100 にクランプする。
"""

from __future__ import annotations

import math


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def linear(value: float, good: float, bad: float) -> float:
    """good で 100, bad で 0 となる直線補間.

    good と bad の大小はどちらでもよい (good>bad の「大きいほど良い」指標にも対応)。
    """
    if good == bad:
        return 100.0 if value == good else 0.0
    return _clamp(100.0 * (value - bad) / (good - bad))


def logarithmic(value: float, good: float, bad: float) -> float:
    """件数系を log スケールで補間. good(少) で 100, bad(多) で 0.

    value, good, bad は非負を想定。CVE 数のように 0 が最良で、件数が増えるほど
    悪化が緩やかになる指標に使う。
    """
    v = math.log1p(max(0.0, value))
    g = math.log1p(max(0.0, good))
    b = math.log1p(max(0.0, bad))
    if g == b:
        return 100.0 if v <= g else 0.0
    return _clamp(100.0 * (b - v) / (b - g))


def binary(present: bool) -> float:
    """存在/不在を 100/0 で返す (probe 設定やポリシの有無など)."""
    return 100.0 if present else 0.0
