"""Import-limiting finder and module overlay context manager."""

from __future__ import annotations

import contextlib
import importlib.abc
import sys as _sys
import types
from typing import Any, Dict, Iterable, Mapping, Optional

from ._constants import _ALLOWED_SIM_MODULES, _ALLOWED_STDLIB_MODULES
from ._exceptions import ImportBlockedError


class _SimModuleFinder(importlib.abc.MetaPathFinder):
    def __init__(self, allowed_roots: Iterable[str], extra_allowed_roots: Iterable[str] = ()): 
        self.allowed_roots = set(allowed_roots) | set(extra_allowed_roots)

    def find_spec(self, fullname: str, path: Any, target: Any = None):
        root = fullname.split(".", 1)[0]
        if fullname in _ALLOWED_SIM_MODULES or root in self.allowed_roots:
            return None
        raise ImportBlockedError(f"import of {fullname!r} is blocked by PicoSimulator")


class _ModuleOverlay(contextlib.AbstractContextManager):
    def __init__(self, modules: Mapping[str, types.ModuleType], restrict_imports: bool, extra_allowed: Iterable[str] = ()): 
        self.modules = dict(modules)
        self.restrict_imports = restrict_imports
        self.extra_allowed = set(extra_allowed)
        self._old_modules: Dict[str, Optional[types.ModuleType]] = {}
        self._finder: Optional[_SimModuleFinder] = None

    def __enter__(self):
        for name, module in self.modules.items():
            self._old_modules[name] = _sys.modules.get(name)
            _sys.modules[name] = module
        if self.restrict_imports:
            allowed_roots = {name.split(".", 1)[0] for name in _ALLOWED_STDLIB_MODULES}
            allowed_roots |= {name.split(".", 1)[0] for name in _ALLOWED_SIM_MODULES}
            allowed_roots |= {"encodings", "codecs", "abc", "_abc", "types", "importlib"}
            self._finder = _SimModuleFinder(allowed_roots, self.extra_allowed)
            _sys.meta_path.insert(0, self._finder)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._finder is not None:
            with contextlib.suppress(ValueError):
                _sys.meta_path.remove(self._finder)
        for name, old in self._old_modules.items():
            if old is None:
                _sys.modules.pop(name, None)
            else:
                _sys.modules[name] = old
        return False


__all__ = ["_SimModuleFinder", "_ModuleOverlay"]
