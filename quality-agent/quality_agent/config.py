"""ポリシ (しきい値) と利用コンテキスト (CoU) の YAML ローダ.

しきい値はプロジェクト単位で policies/<project>.yaml に、利用コンテキストは
contexts/<project>.yaml に外出しして Git 管理する (docs/quality-model.md 参照)。
ローカル LLM 段では設定読み込みに PyYAML のみ使う。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml


@dataclass
class Policy:
    """スコア算出のしきい値・重み. policies/<project>.yaml に対応."""

    name: str
    # severity 別の CVE 重み (加重 CVE 数の算出に使う)
    severity_weights: dict[str, float] = field(
        default_factory=lambda: {
            "critical": 10.0,
            "high": 5.0,
            "medium": 1.0,
            "low": 0.1,
            "unknown": 1.0,
        }
    )
    # 特性別の正規化しきい値 {characteristic: {"good": x, "bad": y}}
    thresholds: dict[str, dict[str, float]] = field(default_factory=dict)
    # データ不足判定の下限 (これ未満のサンプル数は "insufficient data")
    min_samples: int = 1
    # 特性別の下限上書き。QiU 系 (Issue/PR 件数) は少サンプルで振れるため
    # 大きめに設定する (docs/quality-model.md「少サンプル時の過剰反応を抑える」)。
    min_samples_per_characteristic: dict[str, int] = field(default_factory=dict)

    def min_samples_for(self, characteristic: str) -> int:
        return self.min_samples_per_characteristic.get(
            characteristic, self.min_samples
        )

    @classmethod
    def load(cls, path: str) -> "Policy":
        with open(path, "r", encoding="utf-8") as f:
            raw: dict[str, Any] = yaml.safe_load(f) or {}
        base = cls(name=raw.get("name", "default"))
        if "severity_weights" in raw:
            base.severity_weights.update(
                {k: float(v) for k, v in raw["severity_weights"].items()}
            )
        base.thresholds = {
            c: {k: float(v) for k, v in t.items()}
            for c, t in raw.get("thresholds", {}).items()
        }
        base.min_samples = int(raw.get("min_samples", base.min_samples))
        base.min_samples_per_characteristic = {
            c: int(v)
            for c, v in raw.get("min_samples_per_characteristic", {}).items()
        }
        return base


@dataclass
class Context:
    """利用コンテキスト (Context of Use). contexts/<project>.yaml に対応.

    ISO/IEC 25019:2023 の「コンテキスト前提が変われば QiU 要件も再定義」に対応し、
    特性別の重み weights で CoU 重み付き総合スコアを算出する。
    """

    context_id: str
    weights: dict[str, float] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str) -> "Context":
        with open(path, "r", encoding="utf-8") as f:
            raw: dict[str, Any] = yaml.safe_load(f) or {}
        return cls(
            context_id=raw.get("context_id", "unknown"),
            weights={k: float(v) for k, v in raw.get("weights", {}).items()},
            constraints=dict(raw.get("constraints", {})),
        )
