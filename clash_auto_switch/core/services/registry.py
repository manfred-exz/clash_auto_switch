from __future__ import annotations

import importlib
import inspect
import pkgutil

from .base import ServiceChecker
import clash_auto_switch.core.services as services_package


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


SERVICE_CLASSES = discover_service_classes()


def build_service_instances(classes: tuple[type[ServiceChecker], ...]) -> dict[str, ServiceChecker]:
    instances: dict[str, ServiceChecker] = {}
    for checker_class in classes:
        checker = checker_class()
        if checker.service_name in instances:
            raise RuntimeError(f"Duplicate service checker: {checker.service_name}")
        instances[checker.service_name] = checker
    return instances


SERVICE_CHECKER_INSTANCES = build_service_instances(SERVICE_CLASSES)


def get_service(service_name: str) -> ServiceChecker:
    if not service_name:
        raise ValueError("service_name must not be empty")

    try:
        checker = SERVICE_CHECKER_INSTANCES[service_name]
    except KeyError as exc:
        raise KeyError(f"Unknown service: {service_name}") from exc

    if checker.service_name != service_name:
        raise RuntimeError(
            f"Service registry mismatch: requested {service_name}, got {checker.service_name}"
        )
    if not callable(checker.check):
        raise RuntimeError(f"Service checker has no callable check method: {service_name}")

    return checker


def get_all_services() -> list[ServiceChecker]:
    return list(SERVICE_CHECKER_INSTANCES.values())
