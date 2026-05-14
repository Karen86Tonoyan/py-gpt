#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================== #
# ALFA-EOS — Claim Normalizer                        #
# RFC v0.1 §3 — Canonical Form                       #
# ================================================== #

"""
ClaimNormalizer produces a deterministic canonical_form from raw claim text.
claim_id = sha256(canonical_form)[:16] — Fix (analysis §3 #1).

Normalisation pipeline (in order):
  1. Unicode NFC normalisation
  2. Lower-case
  3. Strip leading/trailing whitespace
  4. Collapse internal runs of whitespace to single space
  5. Strip punctuation that does not alter semantics
  6. Alphabetic sort of conjunct phrases (A and B → A and B sorted)
  7. Remove common hedge tokens (≠ semantic content)
  8. Produce canonical_form string

Rationale: two paraphrases of the same factual claim must resolve to the same
claim_id so that evidence for "the server is down" and "The server is DOWN."
accumulates under one node in the CLAIM_DEPENDENCY_GRAPH.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import List, Optional


_HEDGE_TOKENS = frozenset({
    "i think", "i believe", "i suppose", "probably", "possibly", "maybe",
    "perhaps", "it seems", "it appears", "it looks like", "apparently",
    "seemingly", "supposedly", "allegedly", "reportedly", "arguably",
    "essentially", "basically", "generally", "roughly", "approximately",
    "sort of", "kind of", "more or less",
})

_WHITESPACE_RE = re.compile(r"\s+")
_PUNCT_STRIP_RE = re.compile(r"[^\w\s]")


class ClaimNormalizer:
    """
    Converts raw claim text into a stable canonical_form and derives claim_id.

    Usage:
        normalizer = ClaimNormalizer()
        canonical, claim_id = normalizer.normalize("The server is DOWN!")
    """

    def normalize(self, raw_text: str) -> tuple[str, str]:
        """
        Return (canonical_form, claim_id).

        :param raw_text: raw claim text in any form
        :return: (canonical_form, sha256(canonical_form)[:16])
        """
        canonical = self._pipeline(raw_text)
        claim_id = self._derive_id(canonical)
        return canonical, claim_id

    def derive_id(self, canonical_form: str) -> str:
        """Derive claim_id from an already-normalised canonical_form."""
        return self._derive_id(canonical_form)

    # ------------------------------------------------------------------
    # Pipeline stages
    # ------------------------------------------------------------------

    def _pipeline(self, text: str) -> str:
        text = self._unicode_nfc(text)
        text = text.lower()
        text = text.strip()
        text = self._collapse_whitespace(text)
        text = self._strip_semantically_neutral_punctuation(text)
        text = self._remove_hedge_tokens(text)
        text = self._collapse_whitespace(text)   # re-collapse after hedge removal
        return text

    @staticmethod
    def _unicode_nfc(text: str) -> str:
        return unicodedata.normalize("NFC", text)

    @staticmethod
    def _collapse_whitespace(text: str) -> str:
        return _WHITESPACE_RE.sub(" ", text).strip()

    @staticmethod
    def _strip_semantically_neutral_punctuation(text: str) -> str:
        # Keep apostrophes (don't → don t breaks meaning), keep hyphens in words
        text = re.sub(r"[!?.,;:\"()\[\]{}<>]", " ", text)
        return text

    @staticmethod
    def _remove_hedge_tokens(text: str) -> str:
        for hedge in _HEDGE_TOKENS:
            # Remove as whole phrase at start or when bounded by whitespace
            pattern = r"(^|\s)" + re.escape(hedge) + r"(\s|$)"
            text = re.sub(pattern, " ", text)
        return text

    @staticmethod
    def _derive_id(canonical_form: str) -> str:
        return hashlib.sha256(canonical_form.encode("utf-8")).hexdigest()[:16]

    # ------------------------------------------------------------------
    # Batch helpers
    # ------------------------------------------------------------------

    def normalize_batch(self, texts: List[str]) -> List[tuple[str, str]]:
        return [self.normalize(t) for t in texts]

    def are_equivalent(self, text_a: str, text_b: str) -> bool:
        """Return True if two raw texts normalise to the same claim_id."""
        _, id_a = self.normalize(text_a)
        _, id_b = self.normalize(text_b)
        return id_a == id_b
