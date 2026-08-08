#!/usr/bin/env python3
"""
优化功能测试脚本
"""
import sys
import os
import time
import threading
import logging
from typing import Dict, Any

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_optimized_serial_collector():
    """测试优化的串口收集器"""
    logger.info("开始测试优化的串口收集器...")
    
    try:
        from protocol_parser.serial_collector_optimized import OptimizedSerialCollector
        
        # 创建配置
        config = {
            'port': 'COM1',
            'baudrate': 115200,
            'timeout': 1.0,
            'parity': 'N',
            'stopbits': 1,
            'bytesize': 8,
            'max_buffer_size': 1024 * 1024,
            'max_reconnect_attempts': 5,
            'reconnect_delay': 2.0
        }
        
        # 创建优化的串口收集器
        collector = OptimizedSerialCollector(**config)
        
        # 测试基本功能
        logger.info("测试基本功能...")
        
        # 测试配置
        assert collector.port == 'COM1'
        assert collector.baudrate == 115200
        assert collector.max_buffer_size == 1024 * 1024
        assert collector.max_reconnect_attempts == 5
        assert collector.reconnect_delay == 2.0
        
        # 测试性能监控
        logger.info("测试性能监控...")
        metrics = collector.get_performance_metrics()
        assert 'data_received' in metrics
        assert 'connection_time' in metrics
        assert 'error_count' in metrics
        
        # 测试健康检查
        logger.info("测试健康检查...")
        health_status = collector.check_health()
        assert isinstance(health_status, dict)
        assert 'status' in health_status
        assert 'cpu_usage' in health_status
        assert 'memory_usage' in health_status
        
        # 测试资源管理
        logger.info("测试资源管理...")
        resource_info = collector.get_resource_info()
        assert isinstance(resource_info, dict)
        assert 'buffer_size' in resource_info
        assert 'thread_count' in resource_info
        assert 'connection_count' in resource_info
        
        logger.info("优化的串口收集器测试通过")
        return True
        
    except Exception as e:
        logger.error(f"优化的串口收集器测试失败: {e}")
        return False

def test_distributed_serial_manager():
    """测试分布式串口管理器"""
    logger.info("开始测试分布式串口管理器...")
    
    try:
        from protocol_parser.serial_manager import DistributedSerialManager, SerialPortConfig
        
        # 创建分布式管理器
        manager = DistributedSerialManager(max_workers=4, health_check_interval=5.0)
        
        # 创建串口配置
        config1 = SerialPortConfig(
            port='COM1',
            baudrate=115200,
            max_buffer_size=1024 * 1024,
            max_reconnect_attempts=3,
            reconnect_delay=1.0
        )
        
        config2 = SerialPortConfig(
            port='COM2',
            baudrate=9600,
            max_buffer_size=512 * 1024,
            max_reconnect_attempts=3,
            reconnect_delay=1.0
        )
        
        # 测试注册串口
        logger.info("测试注册串口...")
        success1 = manager.register_port('port1', config1)
        success2 = manager.register_port('port2', config2)
        
        assert success1, "串口1注册失败"
        assert success2, "串口2注册失败"
        
        # 测试获取串口状态
        logger.info("测试获取串口状态...")
        status1 = manager.get_port_status('port1')
        status2 = manager.get_port_status('port2')
        
        assert status1 is not None, "无法获取串口1状态"
        assert status2 is not None, "无法获取串口2状态"
        
        # 测试负载均衡
        logger.info("测试负载均衡...")
        selected_port = manager._select_port_by_load_balancing()
        assert selected_port is not None, "负载均衡选择失败"
        
        # 测试分发数据
        logger.info("测试分发数据...")
        test_data = b"Hello, World!"
        success = manager.distribute_data(test_data, 'port1')
        assert success, "数据分发失败"
        
        # 测试性能指标
        logger.info("测试性能指标...")
        metrics = manager.get_performance_metrics()
        assert 'total_data_received' in metrics
        assert 'total_connections' in metrics
        assert 'total_errors' in metrics
        
        # 测试系统信息
        logger.info("测试系统信息...")
        system_info = manager.get_system_info()
        assert 'start_time' in system_info
        assert 'total_ports' in system_info
        assert 'active_ports' in system_info
        
        # 测试事件回调
        logger.info("测试事件回调...")
        event_received = []
        
        def test_callback(data):
            event_received.append(data)
        
        manager.register_event_callback('data_received', test_callback)
        
        # 触发事件
        manager._trigger_event('data_received', {'test': 'data'})
        assert len(event_received) == 1, "事件回调失败"
        
        # 清理
        manager.unregister_port('port1')
        manager.unregister_port('port2')
        
        logger.info("分布式串口管理器测试通过")
        return True
        
    except Exception as e:
        logger.error(f"分布式串口管理器测试失败: {e}")
        return False

def test_plugin_system():
    """测试插件系统"""
    logger.info("开始测试插件系统...")
    
    try:
        from protocol_parser.plugin_system import PluginManager, ProtocolPlugin, PluginConfig
        
        # 创建插件管理器
        plugin_manager = PluginManager()
        
        # 创建测试插件
        class TestPlugin(ProtocolPlugin):
            @property
            def plugin_name(self) -> str:
                return "test_plugin"
            
            @property
            def plugin_version(self) -> str:
                return "1.0.0"
            
            @property
            def plugin_description(self) -> str:
                return "测试插件"
            
            @property
            def plugin_author(self) -> str:
                return "Test Author"
            
            def initialize(self) -> bool:
                return True
            
            def parse_data(self, data: bytes, context: dict = None) -> dict:
                return {'parsed': True, 'length': len(data)}
            
            def encode_data(self, data: dict, context: dict = None) -> bytes:
                return b"encoded_data"
        
        # 创建测试插件文件
        test_plugin_content = '''
from protocol_parser.plugin_system import ProtocolPlugin

class TestPlugin(ProtocolPlugin):
    @property
    def plugin_name(self) -> str:
        return "test_plugin"
    
    @property
    def plugin_version(self) -> str:
        return "1.0.0"
    
    @property
    def plugin_description(self) -> str:
        return "测试插件"
    
    @property
    def plugin_author(self) -> str:
        return "Test Author"
    
    def initialize(self) -> bool:
        return True
    
    def parse_data(self, data: bytes, context: dict = None) -> dict:
        return {'parsed': True, 'length': len(data)}
    
    def encode_data(self, data: dict, context: dict = None) -> bytes:
        return b"encoded_data"
'''
        
        # 创建测试插件目录
        test_plugin_dir = os.path.join(os.path.dirname(__file__), 'test_plugins')
        os.makedirs(test_plugin_dir, exist_ok=True)
        
        # 写入测试插件文件
        test_plugin_file = os.path.join(test_plugin_dir, 'test_plugin.py')
        with open(test_plugin_file, 'w') as f:
            f.write(test_plugin_content)
        
        # 添加插件目录
        plugin_manager.add_plugin_dir(test_plugin_dir)
        
        # 扫描插件
        plugins = plugin_manager.scan_plugins()
        assert 'test_plugin' in plugins, "插件扫描失败"
        
        # 加载插件
        logger.info("测试加载插件...")
        success = plugin_manager.load_plugin('test_plugin')
        assert success, "插件加载失败"
        
        # 测试插件功能
        logger.info("测试插件功能...")
        test_data = b"test data"
        result = plugin_manager.parse_data_with_plugins(test_data)
        assert 'test_plugin' in result, "插件解析失败"
        assert result['test_plugin']['parsed'] is True, "插件解析结果错误"
        
        # 测试插件配置
        logger.info("测试插件配置...")
        config = PluginConfig(
            plugin_name='test_plugin',
            config={'key': 'value'},
            enabled=True,
            priority=1
        )
        plugin_manager.register_plugin_config('test_plugin', config)
        
        retrieved_config = plugin_manager.get_plugin_config('test_plugin')
        assert retrieved_config is not None, "插件配置获取失败"
        assert retrieved_config.config['key'] == 'value', "插件配置错误"
        
        # 测试插件统计
        logger.info("测试插件统计...")
        stats = plugin_manager.get_plugin_stats()
        assert 'total_plugins' in stats
        assert 'enabled_plugins' in stats
        assert stats['total_plugins'] >= 1
        
        # 清理
        plugin_manager.unload_plugin('test_plugin')
        
        # 删除测试插件文件
        if os.path.exists(test_plugin_file):
            os.remove(test_plugin_file)
        
        if os.path.exists(test_plugin_dir):
            os.rmdir(test_plugin_dir)
        
        logger.info("插件系统测试通过")
        return True
        
    except Exception as e:
        logger.error(f"插件系统测试失败: {e}")
        return False

def test_web_monitor():
    """测试Web监控"""
    logger.info("开始测试Web监控...")
    
    try:
        from protocol_parser.web_monitor import WebMonitor, WebMonitorConfig
        from protocol_parser.serial_manager import DistributedSerialManager
        from protocol_parser.plugin_system import PluginManager
        
        # 创建管理器
        serial_manager = DistributedSerialManager()
        plugin_manager = PluginManager()
        
        # 创建Web监控配置
        config = WebMonitorConfig(
            host='127.0.0.1',
            port=8080,
            debug=False,
            update_interval=1.0
        )
        
        # 创建Web监控
        web_monitor = WebMonitor(serial_manager, plugin_manager, config)
        
        # 测试状态获取
        logger.info("测试状态获取...")
        status = web_monitor.get_status()
        assert 'is_running' in status
        assert 'host' in status
        assert 'port' in status
        
        # 测试系统指标收集
        logger.info("测试系统指标收集...")
        web_monitor._collect_system_metrics()
        metrics = web_monitor._get_system_metrics()
        assert 'cpu_usage' in metrics
        assert 'memory_usage' in metrics
        
        # 测试历史数据更新
        logger.info("测试历史数据更新...")
        from protocol_parser.web_monitor import SystemMetrics
        metrics = SystemMetrics(
            timestamp=time.time(),
            cpu_usage=50.0,
            memory_usage=60.0,
            disk_usage=70.0,
            network_sent=1000,
            network_received=2000,
            active_threads=10,
            active_connections=5
        )
        web_monitor._update_history_data(metrics)
        assert len(web_monitor.history_data['cpu_usage']) > 0
        
        # 测试实时数据更新
        logger.info("测试实时数据更新...")
        web_monitor._update_real_time_data(metrics)
        assert 'system' in web_monitor.real_time_data
        
        # 测试图表数据生成
        logger.info("测试图表数据生成...")
        chart_data = web_monitor._generate_chart_data('cpu_usage')
        assert 'type' in chart_data
        assert 'data' in chart_data
        
        # 测试模板渲染
        logger.info("测试模板渲染...")
        content = web_monitor._render_template('index.html')
        assert isinstance(content, str)
        assert len(content) > 0
        
        logger.info("Web监控测试通过")
        return True
        
    except Exception as e:
        logger.error(f"Web监控测试失败: {e}")
        return False

def test_memory_optimization():
    """测试内存优化"""
    logger.info("开始测试内存优化...")
    
    try:
        from protocol_parser.serial_collector_optimized import OptimizedSerialCollector
        
        # 创建多个串口收集器来测试内存管理
        collectors = []
        
        for i in range(5):
            collector = OptimizedSerialCollector(
                port=f'COM{i+1}',
                max_buffer_size=1024 * 1024,  # 1MB缓冲区
                max_reconnect_attempts=3,
                reconnect_delay=1.0
            )
            collectors.append(collector)
        
        # 测试缓冲区管理
        logger.info("测试缓冲区管理...")
        for i, collector in enumerate(collectors):
            # 模拟数据接收
            test_data = b'A' * 1024  # 1KB数据
            collector._buffer.append(test_data)
            
            # 检查缓冲区大小
            buffer_size = len(collector._buffer)
            assert buffer_size <= collector.max_buffer_size, f"缓冲区溢出: {buffer_size} > {collector.max_buffer_size}"
        
        # 测试资源清理
        logger.info("测试资源清理...")
        for collector in collectors:
            resource_info = collector.get_resource_info()
            assert 'buffer_size' in resource_info
            assert 'thread_count' in resource_info
        
        # 清理
        for collector in collectors:
            collector.stop()
        
        logger.info("内存优化测试通过")
        return True
        
    except Exception as e:
        logger.error(f"内存优化测试失败: {e}")
        return False

def test_thread_safety():
    """测试线程安全"""
    logger.info("开始测试线程安全...")
    
    try:
        from protocol_parser.serial_manager import DistributedSerialManager, SerialPortConfig
        
        # 创建分布式管理器
        manager = DistributedSerialManager(max_workers=4)
        
        # 创建串口配置
        config = SerialPortConfig(
            port='COM1',
            baudrate=115200,
            max_buffer_size=1024 * 1024
        )
        
        # 注册串口
        success = manager.register_port('test_port', config)
        assert success, "串口注册失败"
        
        # 测试并发访问
        logger.info("测试并发访问...")
        
        def worker(worker_id):
            for i in range(10):
                status = manager.get_port_status('test_port')
                assert status is not None, f"Worker {worker_id} 获取状态失败"
                time.sleep(0.1)
        
        # 创建多个线程
        threads = []
        for i in range(5):
            thread = threading.Thread(target=worker, args=(i,))
            threads.append(thread)
        
        # 启动所有线程
        for thread in threads:
            thread.start()
        
        # 等待所有线程完成
        for thread in threads:
            thread.join()
        
        # 测试事件回调的线程安全
        logger.info("测试事件回调线程安全...")
        
        event_results = []
        event_lock = threading.Lock()
        
        def safe_callback(data):
            with event_lock:
                event_results.append(data)
        
        manager.register_event_callback('data_received', safe_callback)
        
        # 并发触发事件
        for i in range(10):
            manager._trigger_event('data_received', {'thread_id': i})
        
        # 等待事件处理
        time.sleep(1.0)
        
        assert len(event_results) == 10, f"事件处理数量错误: {len(event_results)} != 10"
        
        # 清理
        manager.unregister_port('test_port')
        
        logger.info("线程安全测试通过")
        return True
        
    except Exception as e:
        logger.error(f"线程安全测试失败: {e}")
        return False

def run_all_tests():
    """运行所有测试"""
    logger.info("开始运行所有优化功能测试...")
    
    tests = [
        ("优化串口收集器", test_optimized_serial_collector),
        ("分布式串口管理器", test_distributed_serial_manager),
        ("插件系统", test_plugin_system),
        ("Web监控", test_web_monitor),
        ("内存优化", test_memory_optimization),
        ("线程安全", test_thread_safety)
    ]
    
    results = {}
    
    for test_name, test_func in tests:
        logger.info(f"\n{'='*50}")
        logger.info(f"运行测试: {test_name}")
        logger.info(f"{'='*50}")
        
        try:
            result = test_func()
            results[test_name] = result
            if result:
                logger.info(f"✓ {test_name} 测试通过")
            else:
                logger.error(f"✗ {test_name} 测试失败")
        except Exception as e:
            logger.error(f"✗ {test_name} 测试异常: {e}")
            results[test_name] = False
    
    # 输出测试结果
    logger.info(f"\n{'='*50}")
    logger.info("测试结果汇总")
    logger.info(f"{'='*50}")
    
    passed = 0
    total = len(tests)
    
    for test_name, result in results.items():
        status = "通过" if result else "失败"
        logger.info(f"{test_name}: {status}")
        if result:
            passed += 1
    
    logger.info(f"\n总计: {passed}/{total} 测试通过")
    
    if passed == total:
        logger.info("🎉 所有测试通过！优化功能实现成功！")
        return True
    else:
        logger.error("❌ 部分测试失败，需要修复问题")
        return False

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)