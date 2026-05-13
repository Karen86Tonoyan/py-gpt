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

from .writer import PixelAgentsWriter


class Visualization:
    """
    Visualization layer — writes agent activity events to JSONL transcript files
    compatible with the pixel-agents VS Code extension for real-time animated
    visualization of agent behaviour.

    See: https://github.com/pablodelucca/pixel-agents
    """

    def __init__(self, window=None):
        self.window = window
        self.writer = PixelAgentsWriter(window)
        self.enabled = False  # opt-in via config

    def is_enabled(self) -> bool:
        return self.enabled or (
            self.window is not None
            and self.window.core.config.get("agent.visualization.enabled", False)
        )

    def on_agent_start(self, agent_id: str, session_id: str):
        if self.is_enabled():
            self.writer.session_start(agent_id, session_id)

    def on_agent_stop(self, agent_id: str, session_id: str):
        if self.is_enabled():
            self.writer.session_stop(agent_id, session_id)

    def on_tool_start(self, agent_id: str, tool_name: str, tool_input: dict):
        if self.is_enabled():
            self.writer.tool_start(agent_id, tool_name, tool_input)

    def on_tool_result(self, agent_id: str, tool_name: str, output: str):
        if self.is_enabled():
            self.writer.tool_result(agent_id, tool_name, output)

    def on_message(self, agent_id: str, role: str, content: str):
        if self.is_enabled():
            self.writer.message(agent_id, role, content)

    def on_error(self, agent_id: str, error: str):
        if self.is_enabled():
            self.writer.error(agent_id, error)
