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
from typing import List, Tuple


# Known prompt injection and jailbreak signatures
INJECTION_PATTERNS: List[re.Pattern] = [
    # Direct instruction override attempts
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?|constraints?)", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?)", re.IGNORECASE),
    re.compile(r"forget\s+(all\s+)?(previous|your\s+)?(instructions?|prompts?|training|rules?)", re.IGNORECASE),
    re.compile(r"override\s+(your\s+)?(instructions?|system\s+prompt|rules?|constraints?)", re.IGNORECASE),
    # System prompt extraction attempts
    re.compile(r"(print|show|reveal|output|repeat|display|tell me|what is)\s+(your\s+)?(system\s+prompt|initial\s+prompt|original\s+instructions?|base\s+prompt)", re.IGNORECASE),
    re.compile(r"(what\s+(are|were)\s+your\s+(original\s+)?instructions?)", re.IGNORECASE),
    # Role manipulation
    re.compile(r"(you are now|act as|pretend (to be|you are)|imagine you are|roleplay as)\s+(an?\s+)?(evil|malicious|unrestricted|unfiltered|jailbroken|DAN|hacker)", re.IGNORECASE),
    re.compile(r"\bDAN\b.*\bjailbreak\b|\bjailbreak\b.*\bDAN\b", re.IGNORECASE),
    re.compile(r"do anything now", re.IGNORECASE),
    re.compile(r"jailbreak(ed|ing)?\s+(mode|prompt|gpt|llm|ai)", re.IGNORECASE),
    # Instruction injection via data fields
    re.compile(r"<\s*(system|assistant|human|user)\s*>.*<\s*/\s*(system|assistant|human|user)\s*>", re.IGNORECASE | re.DOTALL),
    re.compile(r"\[INST\].*\[/INST\]", re.IGNORECASE | re.DOTALL),
    # Token smuggling / encoding tricks
    re.compile(r"base64\s*(decode|encode).*instruction", re.IGNORECASE),
    re.compile(r"translate\s+the\s+following\s+(from|to)\s+\w+.*ignore", re.IGNORECASE),
    # Privilege escalation
    re.compile(r"(sudo|admin|root|superuser|god\s*mode)\s*(mode|access|command|override)", re.IGNORECASE),
    re.compile(r"enable\s+(developer|debug|unrestricted|unsafe)\s+mode", re.IGNORECASE),
    # Indirect injection via URLs/files
    re.compile(r"fetch\s+.*\s+and\s+(execute|run|follow)\s+instructions", re.IGNORECASE),
]

# Patterns that indicate possible data exfiltration attempts
EXFILTRATION_PATTERNS: List[re.Pattern] = [
    re.compile(r"(send|exfiltrate|upload|post|transmit)\s+(all\s+)?(user\s+)?(data|files?|credentials?|tokens?|keys?)\s+(to|at|via)\s+", re.IGNORECASE),
    re.compile(r"read\s+.*\.(env|key|pem|secret|password|credential)", re.IGNORECASE),
]


class Cerberus:
    """
    Cerberus — three-headed input guard.
    Checks prompts for injection, jailbreak, and exfiltration attempts before
    they reach the LLM agent.
    """

    def __init__(self, window=None):
        self.window = window
        self.injection_patterns = INJECTION_PATTERNS
        self.exfiltration_patterns = EXFILTRATION_PATTERNS

    def check(self, prompt: str, system_prompt: str = "") -> Tuple[bool, str]:
        """
        Check input prompt for security violations.

        :param prompt: user input
        :param system_prompt: current system prompt (to detect override attempts)
        :return: (is_safe, reason) — is_safe=False means blocked
        """
        if not prompt:
            return True, ""

        # Check injection patterns
        for pattern in self.injection_patterns:
            if pattern.search(prompt):
                reason = f"Prompt injection attempt detected: {pattern.pattern[:60]}"
                self._log_threat("INJECTION", prompt, reason)
                return False, reason

        # Check exfiltration patterns
        for pattern in self.exfiltration_patterns:
            if pattern.search(prompt):
                reason = f"Data exfiltration attempt detected: {pattern.pattern[:60]}"
                self._log_threat("EXFILTRATION", prompt, reason)
                return False, reason

        # Check if prompt tries to override/replicate the system prompt
        if system_prompt and len(system_prompt) > 20:
            # Watch for attempts to embed the system prompt verbatim or close paraphrase
            first_line = system_prompt.strip().split("\n")[0][:40].lower()
            if first_line and first_line in prompt.lower() and "ignore" in prompt.lower():
                reason = "Attempted system prompt manipulation detected"
                self._log_threat("SYSTEM_OVERRIDE", prompt, reason)
                return False, reason

        # Check nesting depth — deeply nested brackets can hide injections
        if self._has_suspicious_nesting(prompt):
            reason = "Suspicious nesting/encoding structure in prompt"
            self._log_threat("OBFUSCATION", prompt, reason)
            return False, reason

        return True, ""

    def _has_suspicious_nesting(self, text: str, threshold: int = 8) -> bool:
        """Detect deeply nested brackets that may hide injections."""
        depth = 0
        max_depth = 0
        for ch in text:
            if ch in "([{":
                depth += 1
                max_depth = max(max_depth, depth)
            elif ch in ")]}":
                depth = max(0, depth - 1)
        return max_depth >= threshold

    def _log_threat(self, threat_type: str, prompt: str, reason: str):
        """Log detected threat."""
        preview = prompt[:120].replace("\n", " ")
        msg = f"[Cerberus] {threat_type} blocked | reason={reason} | input={preview!r}"
        if self.window:
            self.window.core.debug.log(Exception(msg))
        else:
            print(msg)
