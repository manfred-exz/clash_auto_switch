from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from clash_auto_switch.project import get_data_directory


class DiagnosticLogger:
    """Append structured diagnostic events for postmortem debugging."""

    def __init__(self, filename: str = "diagnostics.jsonl") -> None:
        self.path = get_data_directory() / filename

    def write(
        self,
        kind: str,
        *,
        service_name: str | None = None,
        message: str | None = None,
        **fields: Any,
    ) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "time": datetime.now().isoformat(timespec="seconds"),
                "kind": kind,
            }
            if service_name is not None:
                payload["service_name"] = service_name
            if message is not None:
                payload["message"] = message
            payload.update(_json_safe(fields))

            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
                file.write("\n")
        except OSError:
            return


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
