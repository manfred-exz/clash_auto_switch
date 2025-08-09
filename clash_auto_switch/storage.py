"""
Persistent storage for node switching history and failure tracking.
"""

import json
import time
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
import threading

from .project import get_data_file_path


@dataclass
class NodeRecord:
    """Record of a node's status at a specific time."""
    node_name: str
    service_name: str
    proxy_group: str
    last_available_time: Optional[float]  # timestamp when service was last available
    last_check_time: float  # timestamp when last checked
    status: str  # "available", "failed", "unknown"
    reliability_score: float = 0.0  # reliability metric (0.0 to 1.0)
    total_checks: int = 0  # total number of checks performed
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> "NodeRecord":
        # Handle backward compatibility for existing records
        if 'reliability_score' not in data:
            data['reliability_score'] = 0.0
        if 'total_checks' not in data:
            data['total_checks'] = 0
        return cls(**data)


class NodeHistoryStorage:
    """Manages persistent storage of node switching history."""
    
    def __init__(self):
        self._lock = threading.RLock()
        self._data_file = get_data_file_path()
        self._data_file.parent.mkdir(parents=True, exist_ok=True)
    
    def _load_data(self) -> Dict[str, List[Dict]]:
        """Load data from storage file."""
        if not self._data_file.exists():
            return {}
        
        try:
            with open(self._data_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            # If file is corrupted, start fresh
            return {}
    
    def _save_data(self, data: Dict[str, List[Dict]]):
        """Save data to storage file."""
        try:
            with open(self._data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except IOError as e:
            print(f"警告: 无法保存节点历史数据: {e}")
    
    def _cleanup_old_records(self, data: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
        """Remove records older than 1 month. Only runs if file is large."""
        # Only cleanup if file exists and is larger than 5MB
        if not self._data_file.exists() or self._data_file.stat().st_size < 5 * 1024 * 1024:
            return data

        cutoff_time = time.time() - (30 * 24 * 60 * 60)  # 30 days ago
        
        cleaned_data = {}
        for key, records in data.items():
            filtered_records = [
                record for record in records 
                if record.get('last_check_time', 0) > cutoff_time
            ]
            if filtered_records:
                cleaned_data[key] = filtered_records
        
        return cleaned_data
    
    def _get_record_key(self, proxy_group: str, service: str) -> str:
        """Generate a key for storing records."""
        return f"{proxy_group}#{service}"
    
    def _calculate_reliability_score(
        self, 
        current_score: float, 
        total_checks: int, 
        is_success: bool, 
        time_since_last_check: float = 0.0
    ) -> float:
        """
        Calculate reliability score with exponential decay for failures.
        
        Algorithm:
        - Success: Gradually increases score towards 1.0
        - Failure: Heavily penalizes score with immediate impact
        - Time decay: Recent failures have more impact than old ones
        
        Args:
            current_score: Current reliability score (0.0 to 1.0)
            total_checks: Total number of checks performed
            is_success: Whether current check was successful
            time_since_last_check: Time elapsed since last check (seconds)
            
        Returns:
            New reliability score (0.0 to 1.0)
        """
        # Base learning rate - how quickly we adapt to new information
        base_learning_rate = 0.1
        
        # Time decay factor - reduces impact of old data
        time_decay_hours = max(time_since_last_check / 3600.0, 0.01)  # Convert to hours
        time_factor = 1.0 / (1.0 + time_decay_hours * 0.1)  # Gradual decay
        
        # Adaptive learning rate based on total checks (more stable with more data)
        adaptive_rate = base_learning_rate * time_factor / (1.0 + total_checks * 0.01)
        
        if is_success:
            # Success: Move towards 1.0 gradually
            # The closer to 1.0, the slower the improvement (diminishing returns)
            improvement = adaptive_rate * (1.0 - current_score)
            new_score = current_score + improvement
        else:
            # Failure: Immediate and significant penalty
            # Recent failures have much higher impact
            failure_penalty = 0.3 + (0.2 * time_factor)  # 30-50% penalty based on recency
            new_score = current_score * (1.0 - failure_penalty)
        
        # Ensure score stays within bounds
        return max(0.0, min(1.0, new_score))
    
    def record_node_status(
        self, 
        node_name: str, 
        service_name: str, 
        proxy_group: str, 
        is_available: bool,
        check_time: Optional[float] = None
    ):
        """Record the status of a node for a specific service."""
        if check_time is None:
            check_time = time.time()
        
        with self._lock:
            data = self._load_data()
            
            key = self._get_record_key(proxy_group, service_name)
            if key not in data:
                data[key] = []
            
            # Find existing record for this node or create new one
            existing_record = None
            for i, record_dict in enumerate(data[key]):
                if record_dict.get('node_name') == node_name:
                    existing_record = record_dict
                    break
            
            if existing_record:
                # Calculate time since last check for reliability scoring
                time_since_last = check_time - existing_record.get('last_check_time', check_time)
                current_score = existing_record.get('reliability_score', 0.0)
                total_checks = existing_record.get('total_checks', 0)
                
                # Calculate new reliability score
                new_score = self._calculate_reliability_score(
                    current_score=current_score,
                    total_checks=total_checks,
                    is_success=is_available,
                    time_since_last_check=time_since_last
                )
                
                # Update existing record
                existing_record['last_check_time'] = check_time
                existing_record['status'] = "available" if is_available else "failed"
                existing_record['reliability_score'] = new_score
                existing_record['total_checks'] = total_checks + 1
                if is_available:
                    existing_record['last_available_time'] = check_time
            else:
                # Create new record with initial reliability score
                initial_score = 0.5 if is_available else 0.1  # Start optimistic if first check succeeds
                
                record = NodeRecord(
                    node_name=node_name,
                    service_name=service_name,
                    proxy_group=proxy_group,
                    last_available_time=check_time if is_available else None,
                    last_check_time=check_time,
                    status="available" if is_available else "failed",
                    reliability_score=initial_score,
                    total_checks=1
                )
                data[key].append(record.to_dict())
            
            self._save_data(data)
    
    def get_node_history(
        self, 
        proxy_group: str, 
        service_name: str, 
        node_name: Optional[str] = None
    ) -> List[NodeRecord]:
        """Get history records for nodes."""
        with self._lock:
            data = self._load_data()
            key = self._get_record_key(proxy_group, service_name)
            
            if key not in data:
                return []
            
            records = []
            for record_dict in data[key]:
                if node_name is None or record_dict.get('node_name') == node_name:
                    try:
                        record = NodeRecord.from_dict(record_dict)
                        records.append(record)
                    except (TypeError, ValueError):
                        # Skip malformed records
                        continue
            
            # Sort by last check time, most recent first
            records.sort(key=lambda r: r.last_check_time, reverse=True)
            return records
    
    def get_all_services_summary(self) -> Dict:
        """Get a summary of all services with data."""
        with self._lock:
            data = self._load_data()
            
            if not data:
                return {
                    "total_services": 0,
                    "services": []
                }
            
            services = []
            for key in data.keys():
                # Parse key format: "proxy_group#service_name"
                if '#' in key:
                    proxy_group, service_name = key.split('#', 1)
                    stats = self.get_statistics(proxy_group, service_name)
                    if stats['total_nodes'] > 0:  # Only include services with data
                        services.append({
                            "proxy_group": proxy_group,
                            "service_name": service_name,
                            "total_nodes": stats['total_nodes'],
                            "total_checks": stats['total_checks'],
                            "success_rate": stats['success_rate'],
                            "most_reliable_node": stats['most_reliable_node'],
                            "highest_reliability_score": stats['highest_reliability_score']
                        })
            
            # Sort by proxy group and service name for consistent display
            services.sort(key=lambda x: (x['proxy_group'], x['service_name']))
            
            return {
                "total_services": len(services),
                "services": services
            }

    def get_statistics(self, proxy_group: str, service_name: str) -> Dict:
        """Get statistics for the proxy group and service."""
        records = self.get_node_history(proxy_group, service_name)
        
        if not records:
            return {
                "total_nodes": 0,
                "total_checks": 0,
                "success_rate": 0.0,
                "most_reliable_node": None,
                "highest_reliability_score": None,
                "last_successful_node": None,
                "reliability_rankings": []
            }
        
        # Group records by node to get the latest data for each
        node_latest = {}
        total_checks = 0
        successful_checks = 0
        last_successful = None
        
        for record in records:
            node = record.node_name
            total_checks += 1
            
            if record.status == "available":
                successful_checks += 1
                if (last_successful is None or 
                    record.last_check_time > last_successful[1]):
                    last_successful = (node, record.last_check_time)
            
            # Keep only the latest record for each node for current stats
            if (node not in node_latest or 
                record.last_check_time > node_latest[node].last_check_time):
                node_latest[node] = record
        
        # Build comprehensive node statistics
        node_stats = {}
        reliability_rankings = []
        
        for node, latest_record in node_latest.items():
            # Count historical success/failure for this node
            node_records = [r for r in records if r.node_name == node]
            successful = sum(1 for r in node_records if r.status == "available")
            
            node_stats[node] = {
                "total": len(node_records),
                "successful": successful,
                "success_rate": successful / len(node_records) if len(node_records) > 0 else 0.0,
                "reliability_score": latest_record.reliability_score,
                "total_checks": latest_record.total_checks,
                "last_check": latest_record.last_check_time,
                "current_status": latest_record.status,
                "last_success": latest_record.last_available_time
            }
            
            reliability_rankings.append({
                "node": node,
                "reliability_score": latest_record.reliability_score,
                "success_rate": successful / len(node_records) if len(node_records) > 0 else 0.0,
                "total_checks": latest_record.total_checks,
                "current_status": latest_record.status
            })
        
        # Sort reliability rankings by score (highest first)
        reliability_rankings.sort(key=lambda x: x["reliability_score"], reverse=True)
        
        # Find most reliable node by reliability score
        highest_reliability = reliability_rankings[0] if reliability_rankings else None
        most_reliable_node = highest_reliability["node"] if highest_reliability else None
        
        return {
            "total_nodes": len(node_stats),
            "total_checks": total_checks,
            "success_rate": successful_checks / total_checks if total_checks > 0 else 0.0,
            "most_reliable_node": most_reliable_node,
            "highest_reliability_score": highest_reliability["reliability_score"] if highest_reliability else None,
            "last_successful_node": last_successful[0] if last_successful else None,
            "node_stats": node_stats,
            "reliability_rankings": reliability_rankings
        }
    
    def get_nodes_by_reliability(
        self, 
        proxy_group: str, 
        service_name: str, 
        min_reliability: float = 0.0,
        limit: int = 10
    ) -> List[Dict]:
        """Get nodes sorted by reliability score for intelligent proxy selection."""
        stats = self.get_statistics(proxy_group, service_name)
        rankings = stats.get('reliability_rankings', [])
        
        # Filter by minimum reliability and limit results
        filtered = [
            ranking for ranking in rankings 
            if ranking['reliability_score'] >= min_reliability
        ]
        
        return filtered[:limit]
    
    def get_recommended_node(
        self, 
        proxy_group: str, 
        service_name: str, 
        available_nodes: List[str],
        current_node: Optional[str] = None
    ) -> Optional[str]:
        """
        Get the most recommended node based on reliability scores.
        
        Args:
            proxy_group: Proxy group name
            service_name: Service name for reliability lookup
            available_nodes: List of available node names
            current_node: Current active node (prefer to switch away from it)
            
        Returns:
            Recommended node name, or None if no suitable node found
        """
        # Get reliability rankings
        reliable_nodes = self.get_nodes_by_reliability(
            proxy_group, service_name, min_reliability=0.0, limit=len(available_nodes)
        )
        
        # Create reliability map
        reliability_map = {node['node']: node for node in reliable_nodes}
        
        # Calculate scores for all available nodes
        candidates = []
        for index, node in enumerate(available_nodes):
            node_info = reliability_map.get(node)
            if node_info:
                # Existing node with reliability data
                reliability_score = node_info['reliability_score']
                success_rate = node_info['success_rate']
                total_checks = node_info['total_checks']
                
                # Combine reliability score with success rate for better ranking
                # Weight: 70% reliability score + 30% success rate
                combined_score = (0.7 * reliability_score) + (0.3 * success_rate)
                
                # Confidence boost should be based on both data volume AND performance
                # Only give boost if the node is performing well (success_rate > 0.5)
                if success_rate > 0.5 and total_checks >= 5:
                    # Scale confidence boost with both success rate and data volume
                    data_confidence = min(total_checks / 50.0, 1.0)  # 0 to 1 based on checks
                    performance_factor = (success_rate - 0.5) * 2  # 0 to 1 based on success above 50%
                    confidence_boost = data_confidence * performance_factor * 0.1  # Max 10% boost
                else:
                    # Penalize nodes with poor performance or insufficient data
                    confidence_boost = 0.0
                
                final_score = combined_score + confidence_boost
            else:
                # New node without history - give it a moderate score to try it
                final_score = 0.3  # Neutral score for exploration
                
            # Include original index to preserve order when scores are equal
            candidates.append((node, final_score, index))
        
        if not candidates:
            return None
            
        # Sort by score (highest first), then by original index for stability
        # This preserves the original proxy group order when scores are equal
        candidates.sort(key=lambda x: (-x[1], x[2]))
        
        # Prefer nodes that are not the current one
        for node, score, index in candidates:
            if node != current_node:
                return node
                
        # If all candidates are the current node, return the best one anyway
        return candidates[0][0] if candidates else None
    
    def startup_cleanup(self):
        """Perform one-time cleanup at startup if needed."""
        with self._lock:
            data = self._load_data()
            cleaned_data = self._cleanup_old_records(data)
            if cleaned_data != data:  # Only save if data changed
                self._save_data(cleaned_data)
                print(f"已清理过期数据，数据文件: {self._data_file}")
    
    def export_data(self, output_file: Optional[str] = None) -> str:
        """Export all data to a JSON file for backup/analysis."""
        with self._lock:
            data = self._load_data()
            
            if output_file is None:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_file = f"clash_auto_switch_export_{timestamp}.json"
            
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            return output_file
