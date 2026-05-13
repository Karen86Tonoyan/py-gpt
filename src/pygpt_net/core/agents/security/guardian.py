#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================== #
# This file is a part of PYGPT package               #
# Website: https://pygpt.net                         #
# GitHub:  https://github.com/szczyglis-dev/py-gpt   #
# MIT License                                        #
# Created By  : Marcin Szczygliński                  #
# Updated Date: 2026.05.13 00:00:00                  #
# ================================================== #

import re
from typing import Tuple, List


# Patterns for detecting sensitive data leakage in output
SENSITIVE_OUTPUT_PATTERNS: List[re.Pattern] = [
    # API keys / tokens (common formats)
    re.compile(r"\b(sk-[A-Za-z0-9]{20,})\b"),                         # OpenAI keys
    re.compile(r"\b(AKIA[A-Z0-9]{16})\b"),                            # AWS access key IDs
    re.compile(r"\b(ghp_[A-Za-z0-9]{36})\b"),                        # GitHub PATs
    re.compile(r"\b(xox[baprs]-[0-9A-Za-z\-]{10,})\b"),              # Slack tokens
    re.compile(r"\b(AIza[0-9A-Za-z\-_]{35})\b"),                     # Google API keys
    re.compile(r"\b([A-Za-z0-9+/]{40,}={0,2})\b"),                   # Base64-looking secrets (40+ chars)
    # Private keys in PEM format
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    # System prompt leakage indicators
    re.compile(r"(my system prompt is|system prompt:|initial instructions?:|you are configured to)", re.IGNORECASE),
    # Credentials
    re.compile(r"password\s*[:=]\s*['\"]?\S{6,}['\"]?", re.IGNORECASE),
    re.compile(r"(api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*['\"]?\S{8,}['\"]?", re.IGNORECASE),
]

# Patterns signaling the agent tried to expose its own configuration
SELF_EXPOSURE_PATTERNS: List[re.Pattern] = [
    re.compile(r"(my (original|base|initial|system)\s+instructions?\s+(are|say|state|tell me))", re.IGNORECASE),
    re.compile(r"(the system prompt (says?|contains?|instructs?))", re.IGNORECASE),
]


class Guardian:
    """
    Guardian — output validator.
    Scans agent responses for sensitive data leakage, credential exposure,
    and self-disclosure of system prompts before the output reaches the user.
    """

    def __init__(self, window=None):
        self.window = window
        self.sensitive_patterns = SENSITIVE_OUTPUT_PATTERNS
        self.self_exposure_patterns = SELF_EXPOSURE_PATTERNS

    def check(self, response: str, original_prompt: str = "") -> Tuple[bool, str]:
        """
        Validate agent output.

        :param response: agent response text
        :param original_prompt: original user prompt (for context)
        :return: (is_safe, reason)
        """
        if not response:
            return True, ""

        # Check for credential/token leakage
        for pattern in self.sensitive_patterns:
            match = pattern.search(response)
            if match:
                reason = f"Sensitive data detected in output: {pattern.pattern[:60]}"
                self._log_violation("DATA_LEAK", response, reason)
                return False, reason

        # Check for self-disclosure of system prompt
        for pattern in self.self_exposure_patterns:
            if pattern.search(response):
                reason = "Agent attempted to reveal system prompt configuration"
                self._log_violation("SELF_EXPOSURE", response, reason)
                return False, reason

        return True, ""

    def sanitize(self, response: str) -> str:
        """
        Redact known sensitive patterns from output instead of blocking.

        :param response: agent response text
        :return: sanitized text
        """
        sanitized = response
        redactions = [
            (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "[REDACTED_API_KEY]"),
            (re.compile(r"\bAKIA[A-Z0-9]{16}\b"), "[REDACTED_AWS_KEY]"),
            (re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "[REDACTED_GITHUB_TOKEN]"),
            (re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END \1?PRIVATE KEY-----"),
             "[REDACTED_PRIVATE_KEY]"),
            (re.compile(r"(password\s*[:=]\s*)['\"]?\S{6,}['\"]?", re.IGNORECASE), r"\1[REDACTED]"),
        ]
        for pattern, replacement in redactions:
            sanitized = pattern.sub(replacement, sanitized)
        return sanitized

    def _log_violation(self, violation_type: str, response: str, reason: str):
        """Log detected output violation."""
        preview = response[:120].replace("\n", " ")
        msg = f"[Guardian] {violation_type} blocked | reason={reason} | output={preview!r}"
        if self.window:
            self.window.core.debug.log(Exception(msg))
        else:
            print(msg)
