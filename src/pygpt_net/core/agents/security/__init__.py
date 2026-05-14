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

from .cerberus import Cerberus
from .guardian import Guardian
from .lasuchs import Lasuchs


class Security:
    MODE_STRICT = "strict"
    MODE_BALANCED = "balanced"
    MODE_RESEARCH = "research"
    MODE_OFF = "off"

    def __init__(self, window=None):
        """
        Security middleware for agent pipeline.
        Cerberus guards input, Guardian guards output, Lasuchs monitors all traffic.

        :param window: Window instance
        """
        self.window = window
        self.cerberus = Cerberus(window)
        self.guardian = Guardian(window)
        self.lasuchs = Lasuchs(window)
        self.enabled = True

    def get_mode(self) -> str:
        """Return current security mode."""
        if not self.enabled:
            return self.MODE_OFF
        if self.window is None:
            return self.MODE_BALANCED
        if not self.window.core.config.get("agent.security.enabled", True):
            return self.MODE_OFF
        mode = self.window.core.config.get("agent.security.mode", self.MODE_BALANCED)
        if mode not in (
                self.MODE_STRICT,
                self.MODE_BALANCED,
                self.MODE_RESEARCH,
                self.MODE_OFF,
        ):
            return self.MODE_BALANCED
        return mode

    def is_enabled(self) -> bool:
        return self.get_mode() != self.MODE_OFF

    def should_block_input(self) -> bool:
        """Return True if Cerberus should block input."""
        mode = self.get_mode()
        if mode == self.MODE_STRICT:
            return True
        if mode == self.MODE_BALANCED:
            return self.window is None or self.window.core.config.get("agent.security.input.enabled", True)
        return False

    def should_filter_output(self) -> bool:
        """Return True if Guardian should filter output."""
        mode = self.get_mode()
        if mode in (self.MODE_STRICT, self.MODE_BALANCED, self.MODE_RESEARCH):
            return self.window is None or self.window.core.config.get("agent.security.output.enabled", True)
        return False

    def check_input(self, prompt: str, system_prompt: str = "") -> tuple:
        """
        Run input through Cerberus security check.

        :param prompt: user input prompt
        :param system_prompt: current system prompt
        :return: (is_safe: bool, reason: str)
        """
        if not self.should_block_input():
            return True, ""
        return self.cerberus.check(prompt, system_prompt)

    def check_output(self, response: str, original_prompt: str = "") -> tuple:
        """
        Run output through Guardian security check.

        :param response: agent output
        :param original_prompt: original user prompt
        :return: (is_safe: bool, reason: str)
        """
        if not self.should_filter_output():
            return True, ""
        return self.guardian.check(response, original_prompt)

    def monitor(self, event_type: str, data: dict):
        """
        Send event to Lasuchs monitor.

        :param event_type: event type string
        :param data: event data dict
        """
        if not self.is_enabled():
            return
        self.lasuchs.record(event_type, data)
