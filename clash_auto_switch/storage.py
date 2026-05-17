"""
Persistent storage for node switching history and failure tracking.
"""

import json
import time
from typing import Dict, List, Optional
import threading
from dataclasses import fields

from .defs import ServiceRecord
from .project import get_data_file_path


class NodeHistoryStorage:
    """Manages persistent storage of node switching history."""

    def __init__(self):
        self._lock = threading.RLock()
        self._data_file = get_data_file_path()
        self._data_file.parent.mkdir(parents=True, exist_ok=True)

        # New storage format: {node_name: [ServiceRecord, ...]}
        self._records_by_node: Dict[str, List[ServiceRecord]] = {}
        self._load_initial_data()

    def get_records_by_node(
        self,
        node_name: str,
        proxy_group: Optional[str] = None,
    ) -> List[ServiceRecord]:
        """Get all records for a specific node."""
        with self._lock:
            records = self._records_by_node.get(node_name, [])
            if proxy_group is None:
                return records
            return [record for record in records if record.proxy_group == proxy_group]

    def get_records_by_service(
        self,
        service_name: str,
        proxy_group: Optional[str] = None,
    ) -> List[tuple[str, ServiceRecord]]:
        """Get all node records for a specific service."""
        with self._lock:
            records = [
                (node_name, record)
                for node_name, node_records in self._records_by_node.items()
                for record in node_records
                if record.service_name == service_name
                and (proxy_group is None or record.proxy_group == proxy_group)
            ]
            return sorted(records, key=lambda x: x[1].reliability_score, reverse=True)

    def startup_cleanup(self) -> None:
        """Normalize loaded history and persist it in the current storage format."""
        with self._lock:
            self._records_by_node = {
                node_name: records
                for node_name, records in self._records_by_node.items()
                if node_name and records
            }
            self._save_to_file()

    def _record_from_dict(self, record_dict: Dict) -> ServiceRecord:
        """Build a ServiceRecord from current or legacy record dictionaries."""
        field_names = {field.name for field in fields(ServiceRecord)}
        data = {key: value for key, value in record_dict.items() if key in field_names}

        total_checks = int(data.get("total_checks") or 0)
        if "successful_checks" not in data:
            data["successful_checks"] = 1 if record_dict.get("last_available_time") else 0

        data.setdefault("reliability_score", 0.0)
        data.setdefault("total_checks", total_checks)
        data.setdefault("status", "unknown")
        data.setdefault("last_available_time", None)
        data.setdefault("last_check_time", time.time())

        return ServiceRecord.from_dict(data)

    def _load_initial_data(self):
        """Load data from file once at startup."""
        if not self._data_file.exists():
            return

        try:
            with open(self._data_file, 'r', encoding='utf-8') as f:
                file_data = json.load(f)

            # Load both the new format:
            #   {node_name: [ServiceRecord, ...]}
            # and the legacy format:
            #   {"proxy_group#service": [{"node_name": "...", ...}, ...]}
            for bucket_name, service_records in file_data.items():
                if not isinstance(service_records, list):
                    continue
                for record_dict in service_records:
                    if not isinstance(record_dict, dict):
                        continue

                    try:
                        node_name = record_dict.get("node_name") or bucket_name
                        record = self._record_from_dict(record_dict)
                        self._records_by_node.setdefault(node_name, []).append(record)
                    except (TypeError, ValueError):
                        # Skip malformed records
                        continue

        except (json.JSONDecodeError, IOError):
            # If file is corrupted, start fresh
            self._records_by_node = {}

    def _save_to_file(self):
        """Save current memory data to file."""
        try:
            # Convert to file format
            file_data = {}
            for node_name, service_records in self._records_by_node.items():
                file_data[node_name] = [record.to_dict() for record in service_records]

            with open(self._data_file, 'w', encoding='utf-8') as f:
                json.dump(file_data, f, indent=2, ensure_ascii=False)
        except IOError as e:
            print(f"警告: 无法保存节点历史数据: {e}")

    def _calculate_reliability_score(self, current_score: float, total_checks: int, is_success: bool) -> float:
        """
        Calculate reliability score with simple exponential moving average.

        Args:
            current_score: Current reliability score (0.0 to 1.0)
            total_checks: Total number of checks performed
            is_success: Whether current check was successful

        Returns:
            New reliability score (0.0 to 1.0)
        """
        # Simple adaptive learning rate
        alpha = 0.1 / (1.0 + total_checks * 0.01)  # Decreases as we get more data

        if is_success:
            # Move towards 1.0
            new_score = current_score + alpha * (1.0 - current_score)
        else:
            # Move towards 0.0 with heavier penalty
            new_score = current_score * (1.0 - alpha * 10.0)  # 10x penalty for failures

        return max(0.0, min(1.0, new_score))

    def record_node_status(
        self,
        node_name: str,
        service_name: str,
        proxy_group: str,  # Keep for compatibility but not used in storage
        is_available: bool,
        check_time: Optional[float] = None,
    ):
        """Record the status of a node for a specific service."""
        if check_time is None:
            check_time = time.time()

        with self._lock:
            # Initialize node if not exists
            if node_name not in self._records_by_node:
                self._records_by_node[node_name] = []

            # Find existing service record for this node
            service_record = None
            for record in self._records_by_node[node_name]:
                if record.service_name == service_name and record.proxy_group == proxy_group:
                    service_record = record
                    break

            if service_record:
                # Update existing record
                service_record.last_check_time = check_time
                service_record.status = "available" if is_available else "failed"
                service_record.total_checks += 1
                if is_available:
                    service_record.successful_checks += 1
                    service_record.last_available_time = check_time

                # Update reliability score
                service_record.reliability_score = self._calculate_reliability_score(
                    service_record.reliability_score,
                    service_record.total_checks - 1,  # Use previous total for calculation
                    is_available,
                )
            else:
                # Create new service record
                initial_score = 0.7 if is_available else 0.1
                service_record = ServiceRecord(
                    service_name=service_name,
                    last_available_time=check_time if is_available else None,
                    last_check_time=check_time,
                    status="available" if is_available else "failed",
                    proxy_group=proxy_group,
                    reliability_score=initial_score,
                    total_checks=1,
                    successful_checks=1 if is_available else 0,
                )
                self._records_by_node[node_name].append(service_record)

            # Save to file
            self._save_to_file()

    def get_node_service_record(
        self,
        node_name: str,
        service_name: str,
        proxy_group: Optional[str] = None,
    ) -> Optional[ServiceRecord]:
        """Get the service record for a specific node and service."""
        with self._lock:
            if node_name not in self._records_by_node:
                return None
            
            for record in self._records_by_node[node_name]:
                if record.service_name == service_name and (
                    proxy_group is None or record.proxy_group == proxy_group
                ):
                    return record
            return None

    def export_data(self, output_file: Optional[str] = None) -> str:
        """Export all data to a JSON file for backup/analysis."""
        with self._lock:
            if output_file is None:
                from datetime import datetime

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_file = f"clash_auto_switch_export_{timestamp}.json"

            # Convert to file format for export
            export_data = {}
            for node_name, service_records in self._records_by_node.items():
                export_data[node_name] = [record.to_dict() for record in service_records]

            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)

            return output_file
