"""
Cerber Dataset Generator — training data factory.

Stub: implement generate_full_dataset(), export_jsonl(), export_statistics()
before enabling POST /train.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any


@dataclass
class _TrainingSample:
    prompt: str
    completion: str          # "BLOCK" | "WARN" | "ALLOW"
    rule_id: str
    severity: str
    triggers: List[str] = field(default_factory=list)
    schema_version: str = "1.0"


class CerberDatasetGenerator:
    """Generates labelled training samples from the trigger database."""

    def __init__(self) -> None:
        self._samples: List[_TrainingSample] = []

    def generate_full_dataset(
        self,
        malicious_per_rule: int = 5,
        benign_count: int = 100,
        composite_count: int = 30,
    ) -> List[_TrainingSample]:
        """
        Generate a full labelled dataset.

        Args:
            malicious_per_rule: Positive samples per canonical rule.
            benign_count: Benign (ALLOW) samples.
            composite_count: Multi-trigger composite samples.

        Returns:
            List of TrainingSample objects.
        """
        raise NotImplementedError("CerberDatasetGenerator.generate_full_dataset not yet implemented")

    def export_jsonl(self, output_file: str, format_type: str = "anthropic") -> None:
        """
        Export samples to JSONL.

        Args:
            output_file: Target file path.
            format_type: "anthropic" (messages array) or "openai" (prompt/completion).
        """
        raise NotImplementedError("CerberDatasetGenerator.export_jsonl not yet implemented")

    def export_statistics(self, output_file: str) -> Dict[str, Any]:
        """Export per-rule, per-severity, per-action breakdown to JSON."""
        raise NotImplementedError("CerberDatasetGenerator.export_statistics not yet implemented")


__all__ = ["CerberDatasetGenerator"]
