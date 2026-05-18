#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ================================================== #
# This file is a part of PYGPT package               #
# Website: https://pygpt.net                         #
# GitHub:  https://github.com/szczyglis-dev/py-gpt   #
# MIT License                                        #
# Created By  : Marcin Szczygliński                  #
# Updated Date: 2025.09.19 00:00:00                  #
# ================================================== #

from .custom import Custom
from .legacy import Legacy
from .memory import Memory
from .observer import Observer
from .provider import Provider
from .runner import Runner
from .tools import Tools
from .security import Security
from .visualization import Visualization
from .alfa_eos import AlfaEOS


class Agents:
    def __init__(self, window=None):
        """
        Agents core

        :param window: Window instance
        """
        self.window = window
        self.custom = Custom(window)
        self.legacy = Legacy(window)
        self.memory = Memory(window)
        self.observer = Observer(window)
        self.provider = Provider(window)
        self.runner = Runner(window)
        self.tools = Tools(window)
        self.security = Security(window)
        self.visualization = Visualization(window)
        self.alfa_eos = AlfaEOS()
