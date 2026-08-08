"""
分布式串口管理器 - 支持多串口并行处理
"""
import os
import threading
import queue
import time
import logging
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, Future
import json
import psutil
from .serial_collector_optimized import OptimizedSerialCollector
from .exceptions import ProtocolParserError, ConnectionError


@dataclass
class SerialPortConfig:
    """串口配置类"""
    port: str
    baudrate: int = 115200
    timeout: float = 1.0
    parity: str = 'N'
    stopbits: int = 1
    bytesize: int = 8
    max_buffer_size: int = 1024 * 1024
    max_reconnect_attempts: int = 5
    reconnect_delay: float = 2.0


@dataclass
class SerialPortStatus:
    """串口状态类"""
    port: str
    is_connected: bool = False
    is_running: bool = False
    error_count: int = 0
    last_error: Optional[str] = None
    data_received: int = 0
    connection_time: Optional[float] = None
    uptime: float = 0.0
    cpu_usage: float = 0.0
    memory_usage: float = 0.0


class DistributedSerialManager:
    """分布式串口管理器 - 支持多串口并行处理"""
    
    def __init__(self, 
                 max_workers: int = None,
                 health_check_interval: float = 30.0,
                 load_balancing: bool = True,
                 enable_dynamic_thread_pool: bool = True):
        """
        初始化分布式串口管理器
        
        Args:
            max_workers: 最大工作线程数，默认为CPU核心数
            health_check_interval: 健康检查间隔（秒）
            load_balancing: 是否启用负载均衡
            enable_dynamic_thread_pool: 是否启用动态线程池调整
        """
        # 动态计算最优线程数
        if max_workers is None:
            cpu_count = os.cpu_count() or 4
            memory_gb = psutil.virtual_memory().total / (1024**3)
            if memory_gb > 8:
                max_workers = cpu_count * 2
            elif memory_gb > 4:
                max_workers = cpu_count
            else:
                max_workers = max(2, cpu_count // 2)
        
        self.max_workers = max_workers
        self.health_check_interval = health_check_interval
        self.load_balancing = load_balancing
        self.enable_dynamic_thread_pool = enable_dynamic_thread_pool
        
        # 动态线程池配置
        self._min_workers = 2
        self._max_workers_limit = min(max_workers, 32)
        self._current_workers = max_workers
        
        # 串口集合
        self.serial_ports: Dict[str, OptimizedSerialCollector] = {}
        self.port_configs: Dict[str, SerialPortConfig] = {}
        self.port_status: Dict[str, SerialPortStatus] = {}
        
        # 线程池 - 动态调整
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="serial-worker")
        
        # 任务队列
        self.task_queue = queue.Queue()
        self.result_queue = queue.Queue()
        
        # 状态管理
        self._state_lock = threading.RLock()
        self.is_running = False
        self.health_check_thread = None
        self._health_check_stop_event = threading.Event()
        self._monitor_thread = None
        
        # 性能监控
        self.performance_metrics = {
            'total_data_received': 0,
            'total_connections': 0,
            'total_errors': 0,
            'average_cpu_usage': 0.0,
            'average_memory_usage': 0.0,
            'port_throughput': {}
        }
        
        # 资源监控配置
        self._resource_monitor_interval = 5.0  # 资源监控间隔（秒）
        self._resource_thresholds = {
            'cpu_warning': 80.0,
            'cpu_critical': 95.0,
            'memory_warning': 70.0,
            'memory_critical': 90.0,
            'thread_warning': 0.8,  # 线程使用率阈值
            'thread_critical': 0.95
        }
        self._resource_alerts = []
        
        # 负载均衡器状态
        self._load_balancer = None
        self._last_port_selection = {}
        
        # 事件回调
        self.event_callbacks: Dict[str, List[Callable]] = {
            'port_connected': [],
            'port_disconnected': [],
            'data_received': [],
            'error_occurred': [],
            'health_check': [],
            'resource_alert': [],
            'thread_pool_adjusted': []
        }
        
        # 日志
        self.logger = logging.getLogger(__name__)
        
        # 预编译正则表达式（用于性能优化）
        self._load_balancing_pattern = None
        self._port_selection_pattern = None
        
        # 缓存机制
        self._port_cache = {}
        self._metrics_cache = {}
        self._cache_ttl = 5.0  # 缓存过期时间（秒）
        
        # 启动时间
        self.start_time = time.time()
        
        # 启动资源监控
        if self.enable_dynamic_thread_pool:
            self._start_resource_monitor()
        
    def register_port(self, port_id: str, config: SerialPortConfig) -> bool:
        """
        注册串口
        
        Args:
            port_id: 串口标识符
            config: 串口配置
            
        Returns:
            bool: 注册是否成功
        """
        with self._state_lock:
            if port_id in self.serial_ports:
                self.logger.warning(f"串口 {port_id} 已存在")
                return False
                
            try:
                # 创建优化的串口收集器
                collector = OptimizedSerialCollector(
                    port=config.port,
                    baudrate=config.baudrate,
                    timeout=config.timeout,
                    parity=config.parity,
                    stopbits=config.stopbits,
                    bytesize=config.bytesize,
                    max_buffer_size=config.max_buffer_size,
                    max_reconnect_attempts=config.max_reconnect_attempts,
                    reconnect_delay=config.reconnect_delay
                )
                
                # 设置事件回调（使用单层闭包，确保正确传递 port_id）
                collector.set_event_callback(
                    'data_received',
                    lambda data, pid=port_id: self._on_data_received(pid, data)
                )
                collector.set_event_callback(
                    'error_occurred',
                    lambda error, pid=port_id: self._on_error_occurred(pid, error)
                )
                collector.set_event_callback(
                    'connection_changed',
                    lambda connected, pid=port_id: self._on_connection_changed(pid, connected)
                )
                
                # 存储配置和状态
                self.serial_ports[port_id] = collector
                self.port_configs[port_id] = config
                self.port_status[port_id] = SerialPortStatus(port=port_id)
                
                # 启动串口
                future = self.executor.submit(collector.start)
                self._port_cache[port_id] = {
                    'future': future,
                    'start_time': time.time(),
                    'config': config
                }
                
                self.performance_metrics['total_connections'] += 1
                self.logger.info(f"串口 {port_id} 注册成功")
                
                # 触发事件
                self._trigger_event('port_connected', {'port_id': port_id, 'config': config})
                
                return True
                
            except Exception as e:
                self.logger.error(f"注册串口 {port_id} 失败: {e}")
                self.performance_metrics['total_errors'] += 1
                return False
    
    def unregister_port(self, port_id: str) -> bool:
        """
        注销串口（先安全停止收集器，再清理状态）
        
        Args:
            port_id: 串口标识符
            
        Returns:
            bool: 注销是否成功
        """
        with self._state_lock:
            if port_id not in self.serial_ports:
                self.logger.warning(f"串口 {port_id} 不存在")
                return False
            
            try:
                # 1. 先获取收集器引用
                collector = self.serial_ports[port_id]
                
                # 2. 直接停止收集器（同步等待完成）
                collector.stop(timeout=2.0)
                
                # 3. 确认工作线程已退出
                if hasattr(collector, '_thread') and collector._thread is not None:
                    if collector._thread.is_alive():
                        self.logger.warning(f"串口 {port_id} 工作线程未能及时退出")
                
                # 4. 安全地从管理器状态中清除
                del self.serial_ports[port_id]
                if port_id in self.port_configs:
                    del self.port_configs[port_id]
                if port_id in self.port_status:
                    del self.port_status[port_id]
                if port_id in self._port_cache:
                    del self._port_cache[port_id]
                
                # 触发事件
                self._trigger_event('port_disconnected', {'port_id': port_id})
                
                self.logger.info(f"串口 {port_id} 注销成功")
                return True
                
            except Exception as e:
                self.logger.error(f"注销串口 {port_id} 失败: {e}")
                self.performance_metrics['total_errors'] += 1
                return False
    
    def start_port(self, port_id: str) -> bool:
        """
        启动指定串口
        
        Args:
            port_id: 串口标识符
            
        Returns:
            bool: 启动是否成功
        """
        with self._state_lock:
            if port_id not in self.serial_ports:
                self.logger.error(f"串口 {port_id} 不存在")
                return False
                
            try:
                collector = self.serial_ports[port_id]
                future = self.executor.submit(collector.start)
                
                # 更新状态
                self.port_status[port_id].is_running = True
                self.port_status[port_id].connection_time = time.time()
                
                self._port_cache[port_id] = {
                    'future': future,
                    'start_time': time.time(),
                    'config': self.port_configs[port_id]
                }
                
                self.logger.info(f"串口 {port_id} 启动成功")
                return True
                
            except Exception as e:
                self.logger.error(f"启动串口 {port_id} 失败: {e}")
                self.performance_metrics['total_errors'] += 1
                return False
    
    def stop_port(self, port_id: str) -> bool:
        """
        停止指定串口
        
        Args:
            port_id: 串口标识符
            
        Returns:
            bool: 停止是否成功
        """
        with self._state_lock:
            if port_id not in self.serial_ports:
                self.logger.error(f"串口 {port_id} 不存在")
                return False
                
            try:
                collector = self.serial_ports[port_id]
                future = self.executor.submit(collector.stop)
                
                # 更新状态
                self.port_status[port_id].is_running = False
                
                # 等待停止完成
                future.result(timeout=10.0)
                
                self.logger.info(f"串口 {port_id} 停止成功")
                return True
                
            except Exception as e:
                self.logger.error(f"停止串口 {port_id} 失败: {e}")
                self.performance_metrics['total_errors'] += 1
                return False
    
    def get_port_status(self, port_id: str) -> Optional[SerialPortStatus]:
        """
        获取串口状态
        
        Args:
            port_id: 串口标识符
            
        Returns:
            SerialPortStatus: 串口状态，如果不存在返回None
        """
        # 检查缓存
        cache_key = f"status_{port_id}"
        if cache_key in self._metrics_cache:
            cache_data = self._metrics_cache[cache_key]
            if time.time() - cache_data['timestamp'] < self._cache_ttl:
                return cache_data['data']
        
        with self._state_lock:
            if port_id not in self.port_status:
                return None
                
            status = self.port_status[port_id]
            
            # 更新运行时间
            if status.is_running and status.connection_time:
                status.uptime = time.time() - status.connection_time
            
            # 获取系统资源使用情况
            try:
                process = psutil.Process()
                status.cpu_usage = process.cpu_percent()
                status.memory_usage = process.memory_info().rss / 1024 / 1024  # MB
            except Exception as e:
                self.logger.warning(f"获取资源使用情况失败: {e}")
            
            # 更新缓存
            self._metrics_cache[cache_key] = {
                'data': status,
                'timestamp': time.time()
            }
            
            return status
    
    def get_all_ports_status(self) -> Dict[str, SerialPortStatus]:
        """
        获取所有串口状态
        
        Returns:
            Dict[str, SerialPortStatus]: 所有串口状态
        """
        status_dict = {}
        for port_id in self.serial_ports:
            status_dict[port_id] = self.get_port_status(port_id)
        return status_dict
    
    def distribute_data(self, data: bytes, port_id: Optional[str] = None) -> bool:
        """
        分发数据到指定串口或根据负载均衡选择串口
        
        Args:
            data: 要发送的数据
            port_id: 目标串口ID，如果为None则自动选择
            
        Returns:
            bool: 分发是否成功
        """
        if port_id:
            # 直接发送到指定串口
            if port_id in self.serial_ports:
                collector = self.serial_ports[port_id]
                try:
                    collector.send_data(data)
                    self.performance_metrics['total_data_received'] += len(data)
                    return True
                except Exception as e:
                    self.logger.error(f"发送数据到串口 {port_id} 失败: {e}")
                    return False
            else:
                self.logger.error(f"串口 {port_id} 不存在")
                return False
        else:
            # 负载均衡选择串口
            selected_port = self._select_port_by_load_balancing()
            if selected_port:
                return self.distribute_data(data, selected_port)
            else:
                self.logger.error("没有可用的串口")
                return False
    
    def _select_port_by_load_balancing(self) -> Optional[str]:
        """
        根据负载均衡选择串口
        
        Returns:
            Optional[str]: 选择的串口ID
        """
        available_ports = []
        
        for port_id, collector in self.serial_ports.items():
            status = self.port_status[port_id]
            if status.is_running and status.is_connected:
                # 计算负载分数
                load_score = self._calculate_load_score(port_id)
                available_ports.append((port_id, load_score))
        
        if not available_ports:
            return None
        
        # 选择负载最低的串口
        available_ports.sort(key=lambda x: x[1])
        return available_ports[0][0]
    
    def _calculate_load_score(self, port_id: str) -> float:
        """
        计算串口负载分数
        
        Args:
            port_id: 串口ID
            
        Returns:
            float: 负载分数，越低越好
        """
        status = self.port_status[port_id]
        
        # 基础负载分数
        base_score = 0.0
        
        # CPU使用率负载
        cpu_load = status.cpu_usage / 100.0
        
        # 内存使用率负载
        memory_load = min(status.memory_usage / 100.0, 1.0)
        
        # 错误率负载
        error_rate = 0.0
        if status.data_received > 0:
            error_rate = status.error_count / status.data_received
        
        # 综合负载分数
        load_score = base_score + cpu_load * 0.3 + memory_load * 0.3 + error_rate * 0.4
        
        return load_score
    
    def start_health_check(self) -> None:
        """
        启动健康检查
        """
        if self.health_check_thread and self.health_check_thread.is_alive():
            return
            
        self._health_check_stop_event.clear()
        self.is_running = True
        self.health_check_thread = threading.Thread(target=self._health_check_loop)
        self.health_check_thread.daemon = True
        self.health_check_thread.start()
        
        self.logger.info("健康检查已启动")
    
    def stop_health_check(self) -> None:
        """
        停止健康检查
        """
        self.is_running = False
        self._health_check_stop_event.set()
        if self.health_check_thread:
            self.health_check_thread.join(timeout=5.0)
        self.logger.info("健康检查已停止")
    
    def _health_check_loop(self) -> None:
        """
        健康检查循环
        """
        while self.is_running:
            try:
                # 检查所有串口状态
                for port_id in self.serial_ports:
                    self._check_port_health(port_id)
                
                # 更新性能指标
                self._update_performance_metrics()
                
                # 触发健康检查事件
                self._trigger_event('health_check', {
                    'timestamp': time.time(),
                    'total_ports': len(self.serial_ports),
                    'active_ports': len([p for p in self.port_status.values() if p.is_running]),
                    'total_data': self.performance_metrics['total_data_received']
                })
                
                # 使用可中断的等待机制
                self._health_check_stop_event.wait(self.health_check_interval)
                
            except Exception as e:
                self.logger.error(f"健康检查循环错误: {e}")
                self._health_check_stop_event.wait(5.0)
    
    def _check_port_health(self, port_id: str) -> None:
        """
        检查单个串口健康状态
        
        Args:
            port_id: 串口ID
        """
        status = self.port_status[port_id]
        collector = self.serial_ports[port_id]
        
        # 检查连接状态
        is_connected = collector.is_connected()
        
        if is_connected != status.is_connected:
            status.is_connected = is_connected
            if is_connected:
                self.logger.info(f"串口 {port_id} 连接恢复")
            else:
                self.logger.warning(f"串口 {port_id} 连接断开")
        
        # 检查运行状态
        is_running = collector.is_running()
        
        if is_running != status.is_running:
            status.is_running = is_running
            if is_running:
                self.logger.info(f"串口 {port_id} 运行恢复")
            else:
                self.logger.warning(f"串口 {port_id} 运行停止")
        
        # 检查错误计数
        current_error_count = collector.get_error_count()
        if current_error_count > status.error_count:
            new_errors = current_error_count - status.error_count
            status.error_count = current_error_count
            status.last_error = f"新增 {new_errors} 个错误"
            
            self.logger.warning(f"串口 {port_id} 出现错误: {status.last_error}")
            self._trigger_event('error_occurred', {
                'port_id': port_id,
                'error_count': new_errors,
                'total_errors': status.error_count
            })
    
    def _update_performance_metrics(self) -> None:
        """
        更新性能指标
        """
        # 更新吞吐量指标
        for port_id in self.serial_ports:
            status = self.port_status[port_id]
            if status.is_running:
                self.performance_metrics['port_throughput'][port_id] = status.data_received
        
        # 计算平均资源使用率
        total_cpu = 0.0
        total_memory = 0.0
        active_ports = 0
        
        for port_id in self.serial_ports:
            status = self.port_status[port_id]
            if status.is_running:
                total_cpu += status.cpu_usage
                total_memory += status.memory_usage
                active_ports += 1
        
        if active_ports > 0:
            self.performance_metrics['average_cpu_usage'] = total_cpu / active_ports
            self.performance_metrics['average_memory_usage'] = total_memory / active_ports
    
    def _on_data_received(self, port_id: str, data: bytes) -> None:
        """
        数据接收回调
        
        Args:
            port_id: 串口ID
            data: 接收到的数据
        """
        # 更新状态
        with self._state_lock:
            if port_id in self.port_status:
                self.port_status[port_id].data_received += len(data)
        
        # 更新性能指标
        self.performance_metrics['total_data_received'] += len(data)
        
        # 触发事件
        self._trigger_event('data_received', {
            'port_id': port_id,
            'data': data,
            'data_size': len(data)
        })
    
    def _on_error_occurred(self, port_id: str, error: Exception) -> None:
        """
        错误发生回调
        
        Args:
            port_id: 串口ID
            error: 错误对象
        """
        # 更新状态
        with self._state_lock:
            if port_id in self.port_status:
                self.port_status[port_id].error_count += 1
                self.port_status[port_id].last_error = str(error)
        
        # 更新性能指标
        self.performance_metrics['total_errors'] += 1
        
        # 触发事件
        self._trigger_event('error_occurred', {
            'port_id': port_id,
            'error': str(error),
            'error_count': self.port_status[port_id].error_count
        })
    
    def _on_connection_changed(self, port_id: str, connected: bool) -> None:
        """
        连接状态变化回调
        
        Args:
            port_id: 串口ID
            connected: 连接状态
        """
        with self._state_lock:
            if port_id in self.port_status:
                self.port_status[port_id].is_connected = connected
        
        # 触发事件
        event_type = 'port_connected' if connected else 'port_disconnected'
        self._trigger_event(event_type, {'port_id': port_id, 'connected': connected})
    
    def _trigger_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        触发事件
        
        Args:
            event_type: 事件类型
            data: 事件数据
        """
        if event_type in self.event_callbacks:
            for callback in self.event_callbacks[event_type]:
                try:
                    callback(data)
                except Exception as e:
                    self.logger.error(f"事件回调错误: {e}")
    
    def register_event_callback(self, event_type: str, callback: Callable) -> None:
        """
        注册事件回调
        
        Args:
            event_type: 事件类型
            callback: 回调函数
        """
        if event_type in self.event_callbacks:
            self.event_callbacks[event_type].append(callback)
    
    def unregister_event_callback(self, event_type: str, callback: Callable) -> None:
        """
        注销事件回调
        
        Args:
            event_type: 事件类型
            callback: 回调函数
        """
        if event_type in self.event_callbacks:
            if callback in self.event_callbacks[event_type]:
                self.event_callbacks[event_type].remove(callback)
    
    def get_performance_metrics(self) -> Dict[str, Any]:
        """
        获取性能指标
        
        Returns:
            Dict[str, Any]: 性能指标
        """
        return self.performance_metrics.copy()
    
    def get_system_info(self) -> Dict[str, Any]:
        """
        获取系统信息
        
        Returns:
            Dict[str, Any]: 系统信息
        """
        return {
            'start_time': self.start_time,
            'uptime': time.time() - self.start_time,
            'total_ports': len(self.serial_ports),
            'active_ports': len([p for p in self.port_status.values() if p.is_running]),
            'max_workers': self.max_workers,
            'current_workers': self._current_workers,
            'load_balancing': self.load_balancing,
            'health_check_interval': self.health_check_interval,
            'memory_usage': psutil.Process().memory_info().rss / 1024 / 1024,  # MB
            'cpu_usage': psutil.Process().cpu_percent(),
            'thread_count': threading.active_count(),
            'resource_alerts': self._resource_alerts.copy()
        }
    
    # ========== 动态线程池调整 ==========
    
    def _start_resource_monitor(self) -> None:
        """启动资源监控线程"""
        self._monitor_thread = threading.Thread(target=self._resource_monitor_loop, daemon=True)
        self._monitor_thread.start()
        self.logger.info("资源监控线程已启动")
    
    def _resource_monitor_loop(self) -> None:
        """资源监控循环"""
        while self.is_running:  # 严格跟随运行状态，防止后台线程永久死循环
            try:
                self._check_resources()
                self._adjust_thread_pool_based_on_load()
                time.sleep(self._resource_monitor_interval)
            except Exception as e:
                self.logger.error(f"资源监控错误: {e}")
                time.sleep(1.0)
    
    def _check_resources(self) -> None:
        """检查系统资源状态"""
        try:
            # 获取系统指标
            cpu_usage = psutil.cpu_percent(interval=0.1)
            memory = psutil.virtual_memory()
            process = psutil.Process()
            process_memory_mb = process.memory_info().rss / 1024 / 1024
            
            # 检查阈值
            alerts = []
            
            # CPU检查
            if cpu_usage > self._resource_thresholds['cpu_critical']:
                alerts.append({
                    'level': 'critical',
                    'type': 'cpu',
                    'message': f'CPU使用率过高: {cpu_usage:.1f}%',
                    'value': cpu_usage
                })
            elif cpu_usage > self._resource_thresholds['cpu_warning']:
                alerts.append({
                    'level': 'warning',
                    'type': 'cpu',
                    'message': f'CPU使用率警告: {cpu_usage:.1f}%',
                    'value': cpu_usage
                })
            
            # 内存检查
            memory_percent = memory.percent
            if memory_percent > self._resource_thresholds['memory_critical']:
                alerts.append({
                    'level': 'critical',
                    'type': 'memory',
                    'message': f'内存使用率过高: {memory_percent:.1f}%',
                    'value': memory_percent
                })
            elif memory_percent > self._resource_thresholds['memory_warning']:
                alerts.append({
                    'level': 'warning',
                    'type': 'memory',
                    'message': f'内存使用率警告: {memory_percent:.1f}%',
                    'value': memory_percent
                })
            
            # 更新告警列表
            self._resource_alerts = alerts
            
            # 触发告警事件
            if alerts:
                for alert in alerts:
                    self._trigger_event('resource_alert', alert)
            
            # 更新性能指标
            self.performance_metrics['average_cpu_usage'] = cpu_usage
            self.performance_metrics['average_memory_usage'] = memory_percent
            
        except Exception as e:
            self.logger.error(f"资源检查错误: {e}")
    
    def _adjust_thread_pool_based_on_load(self) -> None:
        """根据系统负载动态调整线程池大小"""
        if not self.enable_dynamic_thread_pool:
            return
        
        try:
            # 获取当前负载
            cpu_usage = psutil.cpu_percent(interval=0.1)
            active_ports = len([p for p in self.port_status.values() if p.is_running])
            active_threads = threading.active_count()
            
            # 计算线程使用率
            thread_usage = active_threads / max(self._current_workers, 1)
            
            new_workers = self._current_workers
            
            # 高负载时增加线程
            if thread_usage > self._resource_thresholds['thread_warning'] and active_ports > 0:
                new_workers = min(self._current_workers + 1, self._max_workers_limit)
            # 低负载时减少线程
            elif thread_usage < 0.3 and self._current_workers > self._min_workers:
                new_workers = max(self._current_workers - 1, self._min_workers)
            
            # CPU过载时减少线程
            if cpu_usage > self._resource_thresholds['cpu_critical']:
                new_workers = max(self._current_workers - 2, self._min_workers)
            elif cpu_usage > self._resource_thresholds['cpu_warning']:
                new_workers = max(self._current_workers - 1, self._min_workers)
            
            # 如果需要调整
            if new_workers != self._current_workers:
                self._adjust_thread_pool_size(new_workers)
                
        except Exception as e:
            self.logger.error(f"线程池调整错误: {e}")
    
    def _adjust_thread_pool_size(self, new_size: int) -> None:
        """调整线程池大小"""
        try:
            old_size = self._current_workers
            self._current_workers = new_size
            
            # 创建新的线程池
            old_executor = self.executor
            self.executor = ThreadPoolExecutor(max_workers=new_size, thread_name_prefix="serial-worker")
            
            # 重新启动所有串口的工作线程
            for port_id, collector in self.serial_ports.items():
                if collector.is_running():
                    # 提交到新线程池
                    self.executor.submit(self._restart_collector, port_id, collector)
            
            # 关闭旧线程池（等待现有任务完成）
            old_executor.shutdown(wait=False)
            
            self.logger.info(f"线程池大小已调整: {old_size} -> {new_size}")
            self._trigger_event('thread_pool_adjusted', {
                'old_size': old_size,
                'new_size': new_size
            })
            
        except Exception as e:
            self.logger.error(f"线程池大小调整失败: {e}")
    
    def _restart_collector(self, port_id: str, collector: OptimizedSerialCollector) -> None:
        """重启串口收集器（在新线程池中）"""
        try:
            # 简化处理：让收集器继续运行
            pass
        except Exception as e:
            self.logger.error(f"重启收集器失败 {port_id}: {e}")
    
    def set_thread_pool_size(self, size: int) -> None:
        """
        手动设置线程池大小
        
        Args:
            size: 新的线程池大小
        """
        size = max(self._min_workers, min(size, self._max_workers_limit))
        self._adjust_thread_pool_size(size)
    
    # ========== 负载均衡支持 ==========
    
    def get_optimal_port(self, data_size: int = 0) -> Optional[str]:
        """
        获取最优串口（基于负载均衡）
        
        Args:
            data_size: 数据大小（用于选择处理能力更强的串口）
            
        Returns:
            Optional[str]: 最优串口ID
        """
        if not self.load_balancing or not self.serial_ports:
            return None
        
        # 获取所有运行中的串口
        active_ports = [
            (port_id, status) 
            for port_id, status in self.port_status.items() 
            if status.is_running and status.is_connected
        ]
        
        if not active_ports:
            return None
        
        # 基于CPU和内存使用进行简单的负载均衡
        if data_size > 0:
            # 大数据选择CPU使用率低的串口
            active_ports.sort(key=lambda x: x[1].cpu_usage)
        else:
            # 轮询策略
            last_port = self._last_port_selection.get('last_port', '')
            found = False
            for port_id, status in active_ports:
                if found:
                    self._last_port_selection['last_port'] = port_id
                    return port_id
                if port_id == last_port:
                    found = True
            
            # 如果没有找到，返回第一个
            if active_ports:
                selected_port = active_ports[0][0]
                self._last_port_selection['last_port'] = selected_port
                return selected_port
        
        return active_ports[0][0]
    
    def get_resource_monitor(self) -> Dict[str, Any]:
        """
        获取资源监控数据
        
        Returns:
            Dict[str, Any]: 资源监控数据
        """
        try:
            return {
                'cpu_usage': psutil.cpu_percent(interval=0.1),
                'memory_usage': psutil.virtual_memory().percent,
                'process_memory_mb': psutil.Process().memory_info().rss / 1024 / 1024,
                'active_threads': threading.active_count(),
                'current_workers': self._current_workers,
                'resource_alerts': self._resource_alerts.copy(),
                'port_statuses': {
                    port_id: {
                        'is_running': status.is_running,
                        'is_connected': status.is_connected,
                        'cpu_usage': status.cpu_usage,
                        'memory_usage': status.memory_usage
                    }
                    for port_id, status in self.port_status.items()
                }
            }
        except Exception as e:
            self.logger.error(f"获取资源监控数据失败: {e}")
            return {}
    
    def shutdown(self) -> None:
        """
        关闭分布式管理器（优雅清理所有资源）
        """
        self.logger.info("开始关闭分布式串口管理器...")
        
        # 0. 立即标记停止信号，让所有后台线程（健康检查、资源监控）立即感知
        self.is_running = False
        
        # 1. 停止健康检查
        self.stop_health_check()
        
        # 2. 在持有锁的情况下，停止所有收集器并清理状态
        with self._state_lock:
            # 先停止所有收集器
            for port_id in list(self.serial_ports.keys()):
                try:
                    collector = self.serial_ports[port_id]
                    collector.stop(timeout=2.0)
                except Exception as e:
                    self.logger.warning(f"停止串口 {port_id} 时出错: {e}")
            
            # 清空所有状态字典
            self.serial_ports.clear()
            self.port_configs.clear()
            self.port_status.clear()
            self._port_cache.clear()
        
        # 3. 等待资源监控线程退出
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=1.0)
        
        # 4. 彻底关闭线程池
        self.executor.shutdown(wait=True)
        
        # 5. 清理缓存
        self._metrics_cache.clear()
        self._resource_alerts.clear()
        
        self.logger.info("分布式串口管理器已关闭")
    
    def __del__(self):
        """析构函数"""
        self.shutdown()