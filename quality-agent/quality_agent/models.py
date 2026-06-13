"""パイプライン各段で受け渡すデータモデル.

依存を最小化するため pydantic ではなく標準の dataclass を使う。各段は JSON
ファイルを介して疎結合に繋がるため、to_dict / from_dict で round-trip できる
ことを保証する (ネストした dataclass を明示的に復元する)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Metric:
    """ツール出力を正規化した 1 つの定量指標."""

    id: str  # 例: "security.cve.critical"
    characteristic: str  # ISO 25010/25019 の品質特性キー 例: "security"
    name: str  # 人間可読名
    value: float
    unit: str = "count"
    subcharacteristic: str | None = None
    source: str = ""  # 算出元ツール 例: "trivy"
    labels: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "characteristic": self.characteristic,
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "subcharacteristic": self.subcharacteristic,
            "source": self.source,
            "labels": dict(self.labels),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Metric":
        return cls(
            id=d["id"],
            characteristic=d["characteristic"],
            name=d["name"],
            value=float(d["value"]),
            unit=d.get("unit", "count"),
            subcharacteristic=d.get("subcharacteristic"),
            source=d.get("source", ""),
            labels=dict(d.get("labels", {})),
        )


@dataclass
class Collection:
    """collect 段の出力. 正規化済みメトリクスの束."""

    collected_at: str  # ISO8601 UTC
    target: str  # 評価対象の識別子 (プロジェクト/クラスタ名)
    metrics: list[Metric]
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "collected_at": self.collected_at,
            "target": self.target,
            "sources": list(self.sources),
            "metrics": [m.to_dict() for m in self.metrics],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Collection":
        return cls(
            collected_at=d["collected_at"],
            target=d["target"],
            metrics=[Metric.from_dict(m) for m in d.get("metrics", [])],
            sources=list(d.get("sources", [])),
        )


@dataclass
class Finding:
    """ルールベース解析または LLM 解析が出す所見."""

    id: str
    characteristic: str
    severity: str  # "info" | "warn" | "critical"
    message: str
    metric_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "characteristic": self.characteristic,
            "severity": self.severity,
            "message": self.message,
            "metric_id": self.metric_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Finding":
        return cls(
            id=d["id"],
            characteristic=d["characteristic"],
            severity=d["severity"],
            message=d["message"],
            metric_id=d.get("metric_id"),
        )


@dataclass
class Analysis:
    """analyze 段の出力. メトリクス + 派生所見."""

    collected_at: str
    target: str
    metrics: list[Metric]
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "collected_at": self.collected_at,
            "target": self.target,
            "metrics": [m.to_dict() for m in self.metrics],
            "findings": [f.to_dict() for f in self.findings],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Analysis":
        return cls(
            collected_at=d["collected_at"],
            target=d["target"],
            metrics=[Metric.from_dict(m) for m in d.get("metrics", [])],
            findings=[Finding.from_dict(f) for f in d.get("findings", [])],
        )


@dataclass
class CharacteristicScore:
    """1 つの品質特性に対する 0-100 正規化スコア."""

    characteristic: str
    score: float  # 0-100
    weight: float  # CoU 重み (正規化前の素の重み)
    contributing_metrics: list[str] = field(default_factory=list)
    insufficient_data: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "characteristic": self.characteristic,
            "score": self.score,
            "weight": self.weight,
            "contributing_metrics": list(self.contributing_metrics),
            "insufficient_data": self.insufficient_data,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CharacteristicScore":
        return cls(
            characteristic=d["characteristic"],
            score=float(d["score"]),
            weight=float(d["weight"]),
            contributing_metrics=list(d.get("contributing_metrics", [])),
            insufficient_data=bool(d.get("insufficient_data", False)),
        )


@dataclass
class ScoreReport:
    """score 段の出力. 特性別スコア + CoU 重み付き総合スコア."""

    scored_at: str
    target: str
    context_id: str
    characteristics: list[CharacteristicScore]
    overall: float  # 0-100, CoU 重み付き加重平均
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scored_at": self.scored_at,
            "target": self.target,
            "context_id": self.context_id,
            "overall": self.overall,
            "characteristics": [c.to_dict() for c in self.characteristics],
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ScoreReport":
        return cls(
            scored_at=d["scored_at"],
            target=d["target"],
            context_id=d["context_id"],
            characteristics=[
                CharacteristicScore.from_dict(c) for c in d.get("characteristics", [])
            ],
            overall=float(d["overall"]),
            notes=list(d.get("notes", [])),
        )
