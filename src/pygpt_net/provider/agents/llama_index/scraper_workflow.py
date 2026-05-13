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

from typing import Dict, Any

from pygpt_net.core.bridge import BridgeContext
from pygpt_net.core.types import (
    AGENT_TYPE_LLAMA,
    AGENT_MODE_WORKFLOW,
)

from .workflow.scraper import get_workflow, SCRAPER_SYSTEM_PROMPT
from ..base import BaseAgent


class ScraperAgent(BaseAgent):
    """
    Browser Scraper Agent — uses Playwright to navigate the web,
    extract data from Google Maps, Facebook public pages, and arbitrary URLs.
    """

    def __init__(self, *args, **kwargs):
        super(ScraperAgent, self).__init__(*args, **kwargs)
        self.id = "scraper"
        self.type = AGENT_TYPE_LLAMA
        self.mode = AGENT_MODE_WORKFLOW
        self.name = "Browser Scraper (Playwright)"

    def get_agent(self, window, kwargs: Dict[str, Any]):
        """
        Build and return a ScraperWorkflow instance.

        :param window: Window instance
        :param kwargs: Agent parameters from Runner
        :return: ScraperWorkflow instance
        """
        return get_workflow(window, kwargs)

    def get_options(self) -> dict:
        return {
            "__prompt__": SCRAPER_SYSTEM_PROMPT,
            "scraper": {
                "label": "Scraper settings",
                "options": {
                    "headless": {
                        "label": "Headless browser",
                        "description": "Run browser in headless (no-window) mode",
                        "type": "bool",
                        "default": True,
                    },
                },
            },
        }
