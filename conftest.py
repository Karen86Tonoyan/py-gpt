"""
Root conftest: stub heavy GUI dependencies so alfa_eos unit tests run headless.
"""
import importlib.abc
import importlib.machinery
import sys
import types
from pathlib import Path

_SRC = Path(__file__).parent / "src"
_AGENTS_FS = str(_SRC / "pygpt_net" / "core" / "agents")


_PYTEST_ATTRS = frozenset({
    "pytest_plugins", "setup_module", "teardown_module",
    "setUpModule", "tearDownModule", "setup", "teardown",
    "pytestmark", "collect_ignore", "collect_ignore_glob",
})


class _LazyStub(types.ModuleType):
    def __getattr__(self, name: str):
        # Return safe defaults for pytest lifecycle names
        if name in _PYTEST_ATTRS:
            return None if name != "pytest_plugins" else []
        stub = type(name, (), {"__init__": lambda self, *a, **k: None})
        setattr(self, name, stub)
        return stub


class _AgentsStubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    _TARGETS = {"pygpt_net.core.agents", "src.pygpt_net.core.agents"}

    def find_spec(self, fullname, path, target=None):
        if fullname in self._TARGETS:
            spec = importlib.machinery.ModuleSpec(fullname, self, is_package=True)
            spec.submodule_search_locations = [_AGENTS_FS]
            return spec
        return None

    def create_module(self, spec):
        mod = _LazyStub(spec.name)
        mod.__path__ = [_AGENTS_FS]  # type: ignore[assignment]
        mod.__package__ = spec.name
        return mod

    def exec_module(self, module):
        pass


sys.meta_path.insert(0, _AgentsStubFinder())

_qt = _LazyStub("PySide6")
_qt.__path__ = []  # type: ignore[assignment]
sys.modules.setdefault("PySide6", _qt)
for _sub in (
    "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
    "PySide6.QtNetwork", "PySide6.QtMultimedia", "PySide6.QtOpenGL",
    "PySide6.QtPrintSupport", "PySide6.QtSvg", "PySide6.QtSvgWidgets",
    "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineCore",
):
    _sub_mod = _LazyStub(_sub)
    _sub_mod.__path__ = []  # type: ignore[assignment]
    sys.modules.setdefault(_sub, _sub_mod)
    setattr(_qt, _sub.split(".")[-1], _sub_mod)
