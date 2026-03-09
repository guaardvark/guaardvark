
import time
import psutil
import logging
import threading
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from collections import deque, defaultdict
from dataclasses import dataclass, asdict
import json

logger = logging.getLogger(__name__)

@dataclass
class MetricSample:
    timestamp: datetime
    value: float
    tags: Optional[Dict[str, str]] = None

@dataclass
class SystemMetrics:
    timestamp: datetime
    cpu_percent: float
    memory_percent: float
    memory_used_mb: float
    memory_available_mb: float
    disk_usage_percent: float
    disk_free_gb: float
    process_count: int
    uptime_seconds: float
    
class MetricsCollector:
    
    def __init__(self, max_samples: int = 1000, collection_interval: int = 30):
        self.max_samples = max_samples
        self.collection_interval = collection_interval
        self.metrics_history = defaultdict(lambda: deque(maxlen=max_samples))
        self.start_time = datetime.now()
        self.is_collecting = False
        self.collection_thread = None
        self._lock = threading.RLock()
        
        self.request_counts = defaultdict(int)
        self.response_times = defaultdict(list)
        self.error_counts = defaultdict(int)
        
    def start_collection(self):
        if self.is_collecting:
            return
            
        self.is_collecting = True
        self.collection_thread = threading.Thread(
            target=self._collection_loop,
            daemon=True,
            name="MetricsCollector"
        )
        self.collection_thread.start()
        logger.info("Metrics collection started")
    
    def stop_collection(self):
        self.is_collecting = False
        if self.collection_thread:
            self.collection_thread.join(timeout=5)
        logger.info("Metrics collection stopped")
    
    def _collection_loop(self):
        while self.is_collecting:
            try:
                self._collect_system_metrics()
                time.sleep(self.collection_interval)
            except Exception as e:
                logger.error(f"Error collecting metrics: {e}")
                time.sleep(self.collection_interval)
    
    def _collect_system_metrics(self):
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            
            disk = psutil.disk_usage('/')
            
            process_count = len(psutil.pids())
            
            uptime = (datetime.now() - self.start_time).total_seconds()
            
            metrics = SystemMetrics(
                timestamp=datetime.now(),
                cpu_percent=cpu_percent,
                memory_percent=memory.percent,
                memory_used_mb=memory.used / (1024 * 1024),
                memory_available_mb=memory.available / (1024 * 1024),
                disk_usage_percent=disk.percent,
                disk_free_gb=disk.free / (1024 * 1024 * 1024),
                process_count=process_count,
                uptime_seconds=uptime
            )
            
            with self._lock:
                now = datetime.now()
                self.metrics_history['cpu_percent'].append(
                    MetricSample(now, cpu_percent)
                )
                self.metrics_history['memory_percent'].append(
                    MetricSample(now, memory.percent)
                )
                self.metrics_history['disk_usage_percent'].append(
                    MetricSample(now, disk.percent)
                )
                
        except Exception as e:
            logger.error(f"Failed to collect system metrics: {e}")
    
    def record_request(self, endpoint: str, method: str, status_code: int, 
                      response_time: float):
        with self._lock:
            key = f"{method}:{endpoint}"
            self.request_counts[key] += 1
            self.response_times[key].append(response_time)
            
            if status_code >= 400:
                self.error_counts[key] += 1
            
            if len(self.response_times[key]) > 100:
                self.response_times[key] = self.response_times[key][-100:]
    
    def get_current_metrics(self) -> Optional[SystemMetrics]:
        try:
            cpu_percent = psutil.cpu_percent()
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            process_count = len(psutil.pids())
            uptime = (datetime.now() - self.start_time).total_seconds()
            
            return SystemMetrics(
                timestamp=datetime.now(),
                cpu_percent=cpu_percent,
                memory_percent=memory.percent,
                memory_used_mb=memory.used / (1024 * 1024),
                memory_available_mb=memory.available / (1024 * 1024),
                disk_usage_percent=disk.percent,
                disk_free_gb=disk.free / (1024 * 1024 * 1024),
                process_count=process_count,
                uptime_seconds=uptime
            )
        except Exception as e:
            logger.error(f"Failed to get current metrics: {e}")
            return None
    
    def get_metrics_history(self, metric_name: str, 
                           hours: int = 1) -> List[MetricSample]:
        with self._lock:
            if metric_name not in self.metrics_history:
                return []
            
            cutoff_time = datetime.now() - timedelta(hours=hours)
            return [
                sample for sample in self.metrics_history[metric_name]
                if sample.timestamp >= cutoff_time
            ]
    
    def get_api_metrics(self) -> Dict[str, Any]:
        with self._lock:
            metrics = {}
            
            for endpoint in self.request_counts:
                request_count = self.request_counts[endpoint]
                error_count = self.error_counts[endpoint]
                response_times = self.response_times[endpoint]
                
                metrics[endpoint] = {
                    'request_count': request_count,
                    'error_count': error_count,
                    'error_rate': error_count / request_count if request_count > 0 else 0,
                    'avg_response_time': sum(response_times) / len(response_times) if response_times else 0,
                    'min_response_time': min(response_times) if response_times else 0,
                    'max_response_time': max(response_times) if response_times else 0
                }
            
            return metrics
    
    def get_health_status(self) -> Dict[str, Any]:
        try:
            current_metrics = self.get_current_metrics()
            if not current_metrics:
                return {'status': 'error', 'message': 'Unable to collect metrics'}
            
            cpu_warning = 80
            cpu_critical = 95
            memory_warning = 80
            memory_critical = 90
            disk_warning = 80
            disk_critical = 90
            
            health_issues = []
            status = 'healthy'
            
            if current_metrics.cpu_percent >= cpu_critical:
                health_issues.append(f"Critical CPU usage: {current_metrics.cpu_percent:.1f}%")
                status = 'critical'
            elif current_metrics.cpu_percent >= cpu_warning:
                health_issues.append(f"High CPU usage: {current_metrics.cpu_percent:.1f}%")
                if status == 'healthy':
                    status = 'warning'
            
            if current_metrics.memory_percent >= memory_critical:
                health_issues.append(f"Critical memory usage: {current_metrics.memory_percent:.1f}%")
                status = 'critical'
            elif current_metrics.memory_percent >= memory_warning:
                health_issues.append(f"High memory usage: {current_metrics.memory_percent:.1f}%")
                if status == 'healthy':
                    status = 'warning'
            
            if current_metrics.disk_usage_percent >= disk_critical:
                health_issues.append(f"Critical disk usage: {current_metrics.disk_usage_percent:.1f}%")
                status = 'critical'
            elif current_metrics.disk_usage_percent >= disk_warning:
                health_issues.append(f"High disk usage: {current_metrics.disk_usage_percent:.1f}%")
                if status == 'healthy':
                    status = 'warning'
            
            api_metrics = self.get_api_metrics()
            high_error_endpoints = [
                endpoint for endpoint, metrics in api_metrics.items()
                if metrics['error_rate'] > 0.1 and metrics['request_count'] > 10
            ]
            
            if high_error_endpoints:
                health_issues.append(f"High error rate on endpoints: {', '.join(high_error_endpoints)}")
                if status == 'healthy':
                    status = 'warning'
            
            return {
                'status': status,
                'issues': health_issues,
                'metrics': asdict(current_metrics),
                'api_summary': {
                    'total_endpoints': len(api_metrics),
                    'total_requests': sum(m['request_count'] for m in api_metrics.values()),
                    'total_errors': sum(m['error_count'] for m in api_metrics.values()),
                    'high_error_endpoints': len(high_error_endpoints)
                }
            }
            
        except Exception as e:
            logger.error(f"Failed to get health status: {e}")
            return {
                'status': 'error',
                'message': f'Health check failed: {str(e)}',
                'issues': ['Health monitoring system error']
            }
    
    def reset_metrics(self):
        with self._lock:
            self.metrics_history.clear()
            self.request_counts.clear()
            self.response_times.clear()
            self.error_counts.clear()
            self.start_time = datetime.now()
        logger.info("Metrics reset")

metrics_collector = MetricsCollector()

def get_metrics_collector() -> MetricsCollector:
    return metrics_collector 
