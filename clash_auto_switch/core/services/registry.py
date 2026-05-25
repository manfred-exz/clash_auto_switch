from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Awaitable, Callable, Optional

from .base import ServiceChecker, ServiceCheckResult, ServiceHostPatterns
import clash_auto_switch.core.services as services_package


ServiceCheckFunc = Callable[[Optional[str]], Awaitable[ServiceCheckResult]]
EXCLUDED_MODULES = {"base", "common", "probe", "registry"}


def discover_service_classes() -> tuple[type[ServiceChecker], ...]:
    classes: list[type[ServiceChecker]] = []
    package_path = services_package.__path__
    package_prefix = f"{services_package.__name__}."

    for module_info in pkgutil.iter_modules(package_path):
        if module_info.name in EXCLUDED_MODULES:
            continue

        module = importlib.import_module(f"{package_prefix}{module_info.name}")
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if obj is ServiceChecker:
                continue
            if obj.__module__ != module.__name__:
                continue
            if issubclass(obj, ServiceChecker):
                classes.append(obj)

    return tuple(sorted(classes, key=lambda checker_class: checker_class.service_name))


def discover_host_patterns() -> dict[str, ServiceHostPatterns]:
    patterns: dict[str, ServiceHostPatterns] = {}
    package_path = services_package.__path__
    package_prefix = f"{services_package.__name__}."

    for module_info in pkgutil.iter_modules(package_path):
        if module_info.name in EXCLUDED_MODULES:
            continue

        module = importlib.import_module(f"{package_prefix}{module_info.name}")
        module_patterns = getattr(module, "HOST_PATTERNS_BY_SERVICE", {})
        if module_patterns:
            patterns.update(module_patterns)

        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if obj is ServiceChecker:
                continue
            if obj.__module__ != module.__name__:
                continue
            if issubclass(obj, ServiceChecker) and obj.host_patterns is not None:
                patterns[obj.service_name] = obj.host_patterns

    return patterns


SERVICE_CLASSES = discover_service_classes()
SERVICE_HOST_PATTERNS = discover_host_patterns()


def auto_trigger_host_patterns(service_name: str) -> tuple[str, ...]:
    patterns = SERVICE_HOST_PATTERNS.get(service_name)
    if patterns is None:
        return ()
    return patterns.trigger_hosts


def connection_host_patterns(service_name: str) -> tuple[str, ...]:
    patterns = SERVICE_HOST_PATTERNS.get(service_name)
    if patterns is None:
        return ()
    return patterns.connection_match_hosts


def build_service_instances(classes: tuple[type[ServiceChecker], ...]) -> dict[str, ServiceChecker]:
    instances: dict[str, ServiceChecker] = {}
    for checker_class in classes:
        checker = checker_class()
        if checker.service_name in instances:
            raise RuntimeError(f"Duplicate service checker: {checker.service_name}")
        instances[checker.service_name] = checker
    return instances


SERVICE_CHECKER_INSTANCES = build_service_instances(SERVICE_CLASSES)

SERVICE_CHECKERS: dict[str, ServiceCheckFunc] = {
    service_name: checker.check
    for service_name, checker in SERVICE_CHECKER_INSTANCES.items()
}
