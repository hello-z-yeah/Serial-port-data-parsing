"""
Web界面监控 - 实现Web界面监控
"""
import json
import os
import threading
import time
import logging
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
import asyncio
import websockets
from flask import Flask, render_template, jsonify, request, Response
from flask_socketio import SocketIO, emit
import psutil
import plotly.graph_objs as go
import plotly.utils
from .serial_manager import DistributedSerialManager
from .plugin_system import PluginManager
from .exceptions import ProtocolParserError


@dataclass
class WebMonitorConfig:
    """Web监控配置类"""
    host: str = "0.0.0.0"
    port: int = 8080
    debug: bool = False
    secret_key: str = "your-secret-key-here"
    max_connections: int = 100
    update_interval: float = 1.0
    websocket_timeout: int = 30
    enable_cors: bool = True
    static_folder: str = "static"
    template_folder: str = "templates"


@dataclass
class SystemMetrics:
    """系统指标类"""
    timestamp: float
    cpu_usage: float
    memory_usage: float
    disk_usage: float
    network_sent: float
    network_received: float
    active_threads: int
    active_connections: int


class WebMonitor:
    """Web监控界面"""
    
    def __init__(self, 
                 serial_manager: DistributedSerialManager,
                 plugin_manager: PluginManager,
                 config: WebMonitorConfig = None):
        """
        初始化Web监控
        
        Args:
            serial_manager: 串口管理器
            plugin_manager: 插件管理器
            config: Web监控配置
        """
        self.serial_manager = serial_manager
        self.plugin_manager = plugin_manager
        self.config = config or WebMonitorConfig()
        
        # Flask应用
        self.app = Flask(__name__, 
                        static_folder=self.config.static_folder,
                        template_folder=self.config.template_folder)
        self.app.config['SECRET_KEY'] = self.config.secret_key
        self.app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB
        
        # SocketIO
        self.socketio = SocketIO(self.app, cors_allowed_origins="*" if self.config.enable_cors else [])
        
        # 系统指标
        self.system_metrics: Dict[str, SystemMetrics] = {}
        self.metrics_lock = threading.RLock()
        
        # WebSocket连接
        self.websocket_connections = set()
        self.connection_lock = threading.RLock()
        
        # 实时数据
        self.real_time_data = {
            'serial_ports': {},
            'plugins': {},
            'system': {},
            'performance': {}
        }
        
        # 历史数据
        self.history_data = {
            'cpu_usage': [],
            'memory_usage': [],
            'disk_usage': [],
            'network_sent': [],
            'network_received': [],
            'port_throughput': {}
        }
        
        # 控制变量
        self.is_running = False
        self.metrics_thread = None
        self.websocket_thread = None
        
        # 日志
        self.logger = logging.getLogger(__name__)
        
        # 注册路由
        self._register_routes()
        
        # 注册SocketIO事件
        self._register_socketio_events()
        
        # 预编译模板数据
        self._template_cache = {}
        
    def _register_routes(self):
        """注册Flask路由"""
        
        @self.app.route('/')
        def index():
            """主页"""
            return self._render_template('index.html')
        
        @self.app.route('/dashboard')
        def dashboard():
            """仪表板"""
            return self._render_template('dashboard.html')
        
        @self.app.route('/serial-ports')
        def serial_ports():
            """串口管理页面"""
            return self._render_template('serial_ports.html')
        
        @self.app.route('/plugins')
        def plugins():
            """插件管理页面"""
            return self._render_template('plugins.html')
        
        @self.app.route('/system')
        def system():
            """系统监控页面"""
            return self._render_template('system.html')
        
        @self.app.route('/api/system/metrics')
        def api_system_metrics():
            """获取系统指标API"""
            return jsonify(self._get_system_metrics())
        
        @self.app.route('/api/serial-ports/status')
        def api_serial_ports_status():
            """获取串口状态API"""
            return jsonify(self.serial_manager.get_all_ports_status())
        
        @self.app.route('/api/serial-ports/performance')
        def api_serial_ports_performance():
            """获取串口性能API"""
            return jsonify(self.serial_manager.get_performance_metrics())
        
        @self.app.route('/api/plugins/info')
        def api_plugins_info():
            """获取插件信息API"""
            return jsonify(self.plugin_manager.get_plugin_stats())
        
        @self.app.route('/api/plugins/list')
        def api_plugins_list():
            """获取插件列表API"""
            plugins_info = {}
            for plugin_name, plugin_info in self.plugin_manager.plugins.items():
                plugins_info[plugin_name] = {
                    'name': plugin_info.name,
                    'version': plugin_info.version,
                    'description': plugin_info.description,
                    'author': plugin_info.author,
                    'enabled': plugin_info.enabled,
                    'load_time': plugin_info.load_time
                }
            return jsonify(plugins_info)
        
        @self.app.route('/api/history/<metric>')
        def api_history(metric):
            """获取历史数据API"""
            if metric in self.history_data:
                return jsonify(self.history_data[metric])
            return jsonify({'error': f'Unknown metric: {metric}'}), 404
        
        @self.app.route('/api/real-time')
        def api_real_time():
            """获取实时数据API"""
            return jsonify(self.real_time_data)
        
        @self.app.route('/api/start-monitoring')
        def api_start_monitoring():
            """启动监控API"""
            if not self.is_running:
                self.start_monitoring()
            return jsonify({'status': 'started'})
        
        @self.app.route('/api/stop-monitoring')
        def api_stop_monitoring():
            """停止监控API"""
            if self.is_running:
                self.stop_monitoring()
            return jsonify({'status': 'stopped'})
        
        @self.app.route('/api/serial-ports/<port_id>/start')
        def api_start_port(port_id):
            """启动串口API"""
            success = self.serial_manager.start_port(port_id)
            return jsonify({'success': success, 'port_id': port_id})
        
        @self.app.route('/api/serial-ports/<port_id>/stop')
        def api_stop_port(port_id):
            """停止串口API"""
            success = self.serial_manager.stop_port(port_id)
            return jsonify({'success': success, 'port_id': port_id})
        
        @self.app.route('/api/serial-ports/<port_id>/restart')
        def api_restart_port(port_id):
            """重启串口API"""
            stop_success = self.serial_manager.stop_port(port_id)
            time.sleep(1.0)  # 等待1秒
            start_success = self.serial_manager.start_port(port_id)
            return jsonify({
                'success': start_success and stop_success,
                'port_id': port_id,
                'stop_success': stop_success,
                'start_success': start_success
            })
        
        @self.app.route('/api/plugins/<plugin_name>/enable')
        def api_enable_plugin(plugin_name):
            """启用插件API"""
            success = self.plugin_manager.enable_plugin(plugin_name)
            return jsonify({'success': success, 'plugin_name': plugin_name})
        
        @self.app.route('/api/plugins/<plugin_name>/disable')
        def api_disable_plugin(plugin_name):
            """禁用插件API"""
            success = self.plugin_manager.disable_plugin(plugin_name)
            return jsonify({'success': success, 'plugin_name': plugin_name})
        
        @self.app.route('/api/system/info')
        def api_system_info():
            """获取系统信息API"""
            return jsonify(self.serial_manager.get_system_info())
        
        @self.app.route('/api/chart/<chart_type>')
        def api_chart(chart_type):
            """获取图表数据API"""
            return jsonify(self._generate_chart_data(chart_type))
        
        @self.app.route('/health')
        def health_check():
            """健康检查API"""
            return jsonify({
                'status': 'healthy',
                'timestamp': datetime.now().isoformat(),
                'uptime': time.time() - self.config.port
            })
    
    def _register_socketio_events(self):
        """注册SocketIO事件"""
        
        @self.socketio.on('connect')
        def handle_connect():
            """处理WebSocket连接"""
            with self.connection_lock:
                self.websocket_connections.add(request.sid)
            self.logger.info(f"WebSocket连接建立: {request.sid}")
        
        @self.socketio.on('disconnect')
        def handle_disconnect():
            """处理WebSocket断开连接"""
            with self.connection_lock:
                self.websocket_connections.discard(request.sid)
            self.logger.info(f"WebSocket连接断开: {request.sid}")
        
        @self.socketio.on('subscribe')
        def handle_subscribe(data):
            """处理订阅请求"""
            channel = data.get('channel', 'all')
            self.logger.info(f"订阅频道: {channel}, 连接ID: {request.sid}")
            emit('subscribed', {'channel': channel})
        
        @self.socketio.on('unsubscribe')
        def handle_unsubscribe(data):
            """处理取消订阅请求"""
            channel = data.get('channel', 'all')
            self.logger.info(f"取消订阅频道: {channel}, 连接ID: {request.sid}")
            emit('unsubscribed', {'channel': channel})
    
    def _render_template(self, template_name: str) -> str:
        """渲染模板"""
        template_key = f"template_{template_name}"
        
        # 检查缓存
        if template_key in self._template_cache:
            cache_data = self._template_cache[template_key]
            if time.time() - cache_data['timestamp'] < 300.0:  # 5分钟缓存
                return cache_data['content']
        
        # 渲染模板
        try:
            content = render_template(template_name)
            
            # 更新缓存
            self._template_cache[template_key] = {
                'content': content,
                'timestamp': time.time()
            }
            
            return content
            
        except Exception as e:
            self.logger.error(f"渲染模板 {template_name} 失败: {e}")
            return f"<html><body><h1>Template Error</h1><p>{e}</p></body></html>"
    
    def _get_system_metrics(self) -> Dict[str, Any]:
        """获取系统指标"""
        with self.metrics_lock:
            if not self.system_metrics:
                return {}
            
            # 获取最新的指标
            latest_metrics = max(self.system_metrics.values(), key=lambda x: x.timestamp)
            
            return {
                'cpu_usage': latest_metrics.cpu_usage,
                'memory_usage': latest_metrics.memory_usage,
                'disk_usage': latest_metrics.disk_usage,
                'network_sent': latest_metrics.network_sent,
                'network_received': latest_metrics.network_received,
                'active_threads': latest_metrics.active_threads,
                'active_connections': latest_metrics.active_connections,
                'timestamp': latest_metrics.timestamp
            }
    
    def _collect_system_metrics(self):
        """收集系统指标"""
        try:
            # CPU使用率
            cpu_usage = psutil.cpu_percent(interval=1)
            
            # 内存使用率
            memory = psutil.virtual_memory()
            memory_usage = memory.percent
            
            # 磁盘使用率
            disk = psutil.disk_usage('/')
            disk_usage = disk.percent
            
            # 网络统计
            network = psutil.net_io_counters()
            network_sent = network.bytes_sent
            network_received = network.bytes_received
            
            # 活动线程数
            active_threads = threading.active_count()
            
            # 活动连接数
            with self.connection_lock:
                active_connections = len(self.websocket_connections)
            
            # 创建系统指标
            metrics = SystemMetrics(
                timestamp=time.time(),
                cpu_usage=cpu_usage,
                memory_usage=memory_usage,
                disk_usage=disk_usage,
                network_sent=network_sent,
                network_received=network_received,
                active_threads=active_threads,
                active_connections=active_connections
            )
            
            # 存储指标
            with self.metrics_lock:
                self.system_metrics[str(metrics.timestamp)] = metrics
            
            # 更新历史数据
            self._update_history_data(metrics)
            
            # 更新实时数据
            self._update_real_time_data(metrics)
            
        except Exception as e:
            self.logger.error(f"收集系统指标失败: {e}")
    
    def _update_history_data(self, metrics: SystemMetrics):
        """更新历史数据"""
        # 限制历史数据长度
        max_history_points = 1000
        
        # 更新CPU使用率
        self.history_data['cpu_usage'].append({
            'timestamp': metrics.timestamp,
            'value': metrics.cpu_usage
        })
        if len(self.history_data['cpu_usage']) > max_history_points:
            self.history_data['cpu_usage'] = self.history_data['cpu_usage'][-max_history_points:]
        
        # 更新内存使用率
        self.history_data['memory_usage'].append({
            'timestamp': metrics.timestamp,
            'value': metrics.memory_usage
        })
        if len(self.history_data['memory_usage']) > max_history_points:
            self.history_data['memory_usage'] = self.history_data['memory_usage'][-max_history_points:]
        
        # 更新磁盘使用率
        self.history_data['disk_usage'].append({
            'timestamp': metrics.timestamp,
            'value': metrics.disk_usage
        })
        if len(self.history_data['disk_usage']) > max_history_points:
            self.history_data['disk_usage'] = self.history_data['disk_usage'][-max_history_points:]
        
        # 更新网络发送
        self.history_data['network_sent'].append({
            'timestamp': metrics.timestamp,
            'value': metrics.network_sent
        })
        if len(self.history_data['network_sent']) > max_history_points:
            self.history_data['network_sent'] = self.history_data['network_sent'][-max_history_points:]
        
        # 更新网络接收
        self.history_data['network_received'].append({
            'timestamp': metrics.timestamp,
            'value': metrics.network_received
        })
        if len(self.history_data['network_received']) > max_history_points:
            self.history_data['network_received'] = self.history_data['network_received'][-max_history_points:]
    
    def _update_real_time_data(self, metrics: SystemMetrics):
        """更新实时数据"""
        # 更新系统数据
        self.real_time_data['system'] = {
            'cpu_usage': metrics.cpu_usage,
            'memory_usage': metrics.memory_usage,
            'disk_usage': metrics.disk_usage,
            'network_sent': metrics.network_sent,
            'network_received': metrics.network_received,
            'active_threads': metrics.active_threads,
            'active_connections': metrics.active_connections,
            'timestamp': metrics.timestamp
        }
        
        # 更新串口数据
        self.real_time_data['serial_ports'] = self.serial_manager.get_all_ports_status()
        
        # 更新插件数据
        self.real_time_data['plugins'] = self.plugin_manager.get_plugin_stats()
        
        # 更新性能数据
        self.real_time_data['performance'] = self.serial_manager.get_performance_metrics()
    
    def _generate_chart_data(self, chart_type: str) -> Dict[str, Any]:
        """生成图表数据"""
        try:
            if chart_type == 'cpu_usage':
                data = self.history_data['cpu_usage']
                return {
                    'type': 'line',
                    'data': data,
                    'layout': {
                        'title': 'CPU使用率',
                        'xaxis': {'title': '时间'},
                        'yaxis': {'title': '使用率 (%)'}
                    }
                }
            elif chart_type == 'memory_usage':
                data = self.history_data['memory_usage']
                return {
                    'type': 'line',
                    'data': data,
                    'layout': {
                        'title': '内存使用率',
                        'xaxis': {'title': '时间'},
                        'yaxis': {'title': '使用率 (%)'}
                    }
                }
            elif chart_type == 'network':
                sent_data = self.history_data['network_sent']
                received_data = self.history_data['network_received']
                return {
                    'type': 'line',
                    'data': {
                        'sent': sent_data,
                        'received': received_data
                    },
                    'layout': {
                        'title': '网络流量',
                        'xaxis': {'title': '时间'},
                        'yaxis': {'title': '字节'}
                    }
                }
            else:
                return {'error': f'Unknown chart type: {chart_type}'}
        except Exception as e:
            self.logger.error(f"生成图表数据失败: {e}")
            return {'error': str(e)}
    
    def _metrics_loop(self):
        """指标收集循环"""
        while self.is_running:
            try:
                self._collect_system_metrics()
                time.sleep(self.config.update_interval)
            except Exception as e:
                self.logger.error(f"指标收集循环错误: {e}")
                time.sleep(5.0)
    
    def _websocket_loop(self):
        """WebSocket推送循环"""
        while self.is_running:
            try:
                # 推送实时数据
                with self.connection_lock:
                    if self.websocket_connections:
                        self.socketio.emit('real_time_update', self.real_time_data)
                
                time.sleep(self.config.update_interval)
            except Exception as e:
                self.logger.error(f"WebSocket推送循环错误: {e}")
                time.sleep(5.0)
    
    def start_monitoring(self):
        """启动监控"""
        if self.is_running:
            return
        
        self.is_running = True
        
        # 启动指标收集线程
        self.metrics_thread = threading.Thread(target=self._metrics_loop)
        self.metrics_thread.daemon = True
        self.metrics_thread.start()
        
        # 启动WebSocket推送线程
        self.websocket_thread = threading.Thread(target=self._websocket_loop)
        self.websocket_thread.daemon = True
        self.websocket_thread.start()
        
        self.logger.info("Web监控已启动")
    
    def stop_monitoring(self):
        """停止监控"""
        self.is_running = False
        
        # 等待线程结束
        if self.metrics_thread:
            self.metrics_thread.join(timeout=5.0)
        
        if self.websocket_thread:
            self.websocket_thread.join(timeout=5.0)
        
        self.logger.info("Web监控已停止")
    
    def run(self, host: str = None, port: int = None, debug: bool = None):
        """运行Web监控"""
        host = host or self.config.host
        port = port or self.config.port
        debug = debug if debug is not None else self.config.debug
        
        self.logger.info(f"启动Web监控: {host}:{port}")
        
        # 启动监控
        self.start_monitoring()
        
        # 运行Flask应用
        self.socketio.run(self.app, host=host, port=port, debug=debug)
    
    def get_status(self) -> Dict[str, Any]:
        """获取监控状态"""
        return {
            'is_running': self.is_running,
            'host': self.config.host,
            'port': self.config.port,
            'websocket_connections': len(self.websocket_connections),
            'system_metrics_count': len(self.system_metrics),
            'history_data_points': {
                metric: len(data) for metric, data in self.history_data.items()
            }
        }
    
    def shutdown(self):
        """关闭Web监控"""
        self.logger.info("开始关闭Web监控...")
        
        # 停止监控
        self.stop_monitoring()
        
        # 清理连接
        with self.connection_lock:
            self.websocket_connections.clear()
        
        # 清理缓存
        self._template_cache.clear()
        
        self.logger.info("Web监控已关闭")


# 创建简单的HTML模板
def create_html_templates():
    """创建HTML模板"""
    templates_dir = os.path.join(os.path.dirname(__file__), 'templates')
    os.makedirs(templates_dir, exist_ok=True)
    
    # 创建主页模板
    index_html = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>串口协议解析器监控</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.socket.io/4.0.0/socket.io.min.js"></script>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark">
        <div class="container">
            <a class="navbar-brand" href="/">串口协议解析器监控</a>
            <div class="navbar-nav">
                <a class="nav-link" href="/dashboard">仪表板</a>
                <a class="nav-link" href="/serial-ports">串口管理</a>
                <a class="nav-link" href="/plugins">插件管理</a>
                <a class="nav-link" href="/system">系统监控</a>
            </div>
        </div>
    </nav>
    
    <div class="container mt-4">
        <div class="row">
            <div class="col-md-12">
                <div class="card">
                    <div class="card-header">
                        <h5>系统状态</h5>
                    </div>
                    <div class="card-body">
                        <div class="row">
                            <div class="col-md-3">
                                <div class="card text-center">
                                    <div class="card-body">
                                        <h5 class="card-title">CPU使用率</h5>
                                        <p class="card-text display-4" id="cpu-usage">0%</p>
                                    </div>
                                </div>
                            </div>
                            <div class="col-md-3">
                                <div class="card text-center">
                                    <div class="card-body">
                                        <h5 class="card-title">内存使用率</h5>
                                        <p class="card-text display-4" id="memory-usage">0%</p>
                                    </div>
                                </div>
                            </div>
                            <div class="col-md-3">
                                <div class="card text-center">
                                    <div class="card-body">
                                        <h5 class="card-title">活跃连接</h5>
                                        <p class="card-text display-4" id="active-connections">0</p>
                                    </div>
                                </div>
                            </div>
                            <div class="col-md-3">
                                <div class="card text-center">
                                    <div class="card-body">
                                        <h5 class="card-title">运行时间</h5>
                                        <p class="card-text display-4" id="uptime">0s</p>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <div class="row mt-4">
            <div class="col-md-6">
                <div class="card">
                    <div class="card-header">
                        <h5>CPU使用率趋势</h5>
                    </div>
                    <div class="card-body">
                        <canvas id="cpu-chart"></canvas>
                    </div>
                </div>
            </div>
            <div class="col-md-6">
                <div class="card">
                    <div class="card-header">
                        <h5>内存使用率趋势</h5>
                    </div>
                    <div class="card-body">
                        <canvas id="memory-chart"></canvas>
                    </div>
                </div>
            </div>
        </div>
        
        <div class="row mt-4">
            <div class="col-md-12">
                <div class="card">
                    <div class="card-header">
                        <h5>串口状态</h5>
                    </div>
                    <div class="card-body">
                        <div id="serial-ports-status"></div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        // Socket.IO连接
        const socket = io();
        
        // 图表配置
        const cpuChart = new Chart(document.getElementById('cpu-chart'), {
            type: 'line',
            data: {
                labels: [],
                datasets: [{
                    label: 'CPU使用率',
                    data: [],
                    borderColor: 'rgb(75, 192, 192)',
                    tension: 0.1
                }]
            },
            options: {
                responsive: true,
                scales: {
                    y: {
                        beginAtZero: true,
                        max: 100
                    }
                }
            }
        });
        
        const memoryChart = new Chart(document.getElementById('memory-chart'), {
            type: 'line',
            data: {
                labels: [],
                datasets: [{
                    label: '内存使用率',
                    data: [],
                    borderColor: 'rgb(255, 99, 132)',
                    tension: 0.1
                }]
            },
            options: {
                responsive: true,
                scales: {
                    y: {
                        beginAtZero: true,
                        max: 100
                    }
                }
            }
        });
        
        // 实时数据更新
        socket.on('real_time_update', function(data) {
            // 更新系统指标
            if (data.system) {
                document.getElementById('cpu-usage').textContent = data.system.cpu_usage.toFixed(1) + '%';
                document.getElementById('memory-usage').textContent = data.system.memory_usage.toFixed(1) + '%';
                document.getElementById('active-connections').textContent = data.system.active_connections;
                
                // 更新运行时间
                const uptime = Math.floor(data.system.timestamp - data.system.start_time);
                document.getElementById('uptime').textContent = uptime + 's';
            }
            
            // 更新CPU图表
            if (data.system) {
                const now = new Date(data.system.timestamp * 1000).toLocaleTimeString();
                cpuChart.data.labels.push(now);
                cpuChart.data.datasets[0].data.push(data.system.cpu_usage);
                
                // 限制数据点数量
                if (cpuChart.data.labels.length > 50) {
                    cpuChart.data.labels.shift();
                    cpuChart.data.datasets[0].data.shift();
                }
                
                cpuChart.update();
            }
            
            // 更新内存图表
            if (data.system) {
                const now = new Date(data.system.timestamp * 1000).toLocaleTimeString();
                memoryChart.data.labels.push(now);
                memoryChart.data.datasets[0].data.push(data.system.memory_usage);
                
                // 限制数据点数量
                if (memoryChart.data.labels.length > 50) {
                    memoryChart.data.labels.shift();
                    memoryChart.data.datasets[0].data.shift();
                }
                
                memoryChart.update();
            }
            
            // 更新串口状态
            if (data.serial_ports) {
                const statusDiv = document.getElementById('serial-ports-status');
                statusDiv.innerHTML = '';
                
                for (const [portId, portStatus] of Object.entries(data.serial_ports)) {
                    const portCard = document.createElement('div');
                    portCard.className = 'card mb-2';
                    portCard.innerHTML = `
                        <div class="card-body">
                            <h6 class="card-title">串口 ${portId}</h6>
                            <p class="card-text">
                                状态: ${portStatus.is_connected ? '已连接' : '未连接'} | 
                                运行: ${portStatus.is_running ? '运行中' : '已停止'} | 
                                数据量: ${portStatus.data_received} 字节
                            </p>
                        </div>
                    `;
                    statusDiv.appendChild(portCard);
                }
            }
        });
        
        // 页面加载时获取初始数据
        window.onload = function() {
            fetch('/api/system/metrics')
                .then(response => response.json())
                .then(data => {
                    document.getElementById('cpu-usage').textContent = data.cpu_usage.toFixed(1) + '%';
                    document.getElementById('memory-usage').textContent = data.memory_usage.toFixed(1) + '%';
                });
            
            fetch('/api/serial-ports/status')
                .then(response => response.json())
                .then(data => {
                    const statusDiv = document.getElementById('serial-ports-status');
                    statusDiv.innerHTML = '';
                    
                    for (const [portId, portStatus] of Object.entries(data)) {
                        const portCard = document.createElement('div');
                        portCard.className = 'card mb-2';
                        portCard.innerHTML = `
                            <div class="card-body">
                                <h6 class="card-title">串口 ${portId}</h6>
                                <p class="card-text">
                                    状态: ${portStatus.is_connected ? '已连接' : '未连接'} | 
                                    运行: ${portStatus.is_running ? '运行中' : '已停止'} | 
                                    数据量: ${portStatus.data_received} 字节
                                </p>
                            </div>
                        `;
                        statusDiv.appendChild(portCard);
                    }
                });
        };
    </script>
</body>
</html>
    """
    
    # 写入模板文件
    with open(os.path.join(templates_dir, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(index_html)