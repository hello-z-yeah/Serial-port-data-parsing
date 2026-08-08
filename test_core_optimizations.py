#!/usr/bin/env python3
"""
核心优化功能测试脚本
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

def test_exceptions():
    """测试异常处理"""
    logger.info("开始测试异常处理...")
    
    try:
        from protocol_parser.exceptions import ProtocolParserError, PluginError
        
        # 测试基础异常
        error = ProtocolParserError("测试错误", {"key": "value"}, error_code=100)
        assert error.error_code == 100
        assert error.context["key"] == "value"
        
        # 测试插件异常
        plugin_error = PluginError("插件错误", plugin_name="test_plugin")
        assert plugin_error.context["plugin_name"] == "test_plugin"
        
        logger.info("异常处理测试通过")
        return True
        
    except Exception as e:
        logger.error(f"异常处理测试失败: {e}")
        return False

def test_serial_collector_basic():
    """测试基础串口收集器功能"""
    logger.info("开始测试基础串口收集器功能...")
    
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
        collector = OptimizedSerialCollector(config, **config)
        
        # 测试基本属性
        assert collector.port == 'COM1'
        assert collector.baudrate == 115200
        assert collector.max_buffer_size == 1024 * 1024
        assert collector.max_reconnect_attempts == 5
        assert collector.reconnect_delay == 2.0
        
        # 测试性能监控
        metrics = collector.get_performance_metrics()
        assert 'data_received' in metrics
        assert 'connection_time' in metrics
        assert 'error_count' in metrics
        
        # 测试健康检查
        health_status = collector.check_health()
        assert isinstance(health_status, dict)
        assert 'status' in health_status
        assert 'cpu_usage' in health_status
        assert 'memory_usage' in health_status
        
        # 测试资源管理
        resource_info = collector.get_resource_info()
        assert isinstance(resource_info, dict)
        assert 'buffer_size' in resource_info
        assert 'thread_count' in resource_info
        assert 'connection_count' in resource_info
        
        logger.info("基础串口收集器功能测试通过")
        return True
        
    except Exception as e:
        logger.error(f"基础串口收集器功能测试失败: {e}")
        return False

def test_distributed_serial_manager_basic():
    """测试基础分布式串口管理器功能"""
    logger.info("开始测试基础分布式串口管理器功能...")
    
    try:
        from protocol_parser.serial_manager import DistributedSerialManager, SerialPortConfig
        
        # 创建分布式管理器
        manager = DistributedSerialManager(max_workers=2, health_check_interval=5.0)
        
        # 创建串口配置
        config = SerialPortConfig(
            port='COM1',
            baudrate=115200,
            max_buffer_size=1024 * 1024,
            max_reconnect_attempts=3,
            reconnect_delay=1.0
        )
        
        # 测试注册串口
        success = manager.register_port('test_port', config)
        assert success, "串口注册失败"
        
        # 测试获取串口状态
        status = manager.get_port_status('test_port')
        assert status is not None, "无法获取串口状态"
        
        # 测试性能指标
        metrics = manager.get_performance_metrics()
        assert 'total_data_received' in metrics
        assert 'total_connections' in metrics
        assert 'total_errors' in metrics
        
        # 测试系统信息
        system_info = manager.get_system_info()
        assert 'start_time' in system_info
        assert 'total_ports' in system_info
        assert 'active_ports' in system_info
        
        # 测试事件回调
        event_received = []
        
        def test_callback(data):
            event_received.append(data)
        
        manager.register_event_callback('data_received', test_callback)
        manager._trigger_event('data_received', {'test': 'data'})
        
        assert len(event_received) == 1, "事件回调失败"
        
        # 清理
        manager.unregister_port('test_port')
        
        logger.info("基础分布式串口管理器功能测试通过")
        return True
        
    except Exception as e:
        logger.error(f"基础分布式串口管理器功能测试失败: {e}")
        return False

def test_plugin_system_basic():
    """测试基础插件系统功能"""
    logger.info("开始测试基础插件系统功能...")
    
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
        
        # 创建测试插件目录
        test_plugin_dir = os.path.join(os.path.dirname(__file__), 'test_plugins')
        os.makedirs(test_plugin_dir, exist_ok=True)
        
        # 写入测试插件文件
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
        
        test_plugin_file = os.path.join(test_plugin_dir, 'test_plugin.py')
        with open(test_plugin_file, 'w') as f:
            f.write(test_plugin_content)
        
        # 添加插件目录
        plugin_manager.add_plugin_dir(test_plugin_dir)
        
        # 扫描插件
        plugins = plugin_manager.scan_plugins()
        assert 'test_plugin' in plugins, "插件扫描失败"
        
        # 加载插件
        success = plugin_manager.load_plugin('test_plugin')
        assert success, "插件加载失败"
        
        # 测试插件功能
        test_data = b"test data"
        result = plugin_manager.parse_data_with_plugins(test_data)
        assert 'test_plugin' in result, "插件解析失败"
        assert result['test_plugin']['parsed'] is True, "插件解析结果错误"
        
        # 测试插件配置
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
        
        logger.info("基础插件系统功能测试通过")
        return True
        
    except Exception as e:
        logger.error(f"基础插件系统功能测试失败: {e}")
        return False

def test_thread_safety():
    """测试线程安全"""
    logger.info("开始测试线程安全...")
    
    try:
        from protocol_parser.serial_manager import DistributedSerialManager, SerialPortConfig
        
        # 创建分布式管理器
        manager = DistributedSerialManager(max_workers=2)
        
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
        def worker(worker_id):
            for i in range(5):
                status = manager.get_port_status('test_port')
                assert status is not None, f"Worker {worker_id} 获取状态失败"
                time.sleep(0.1)
        
        # 创建多个线程
        threads = []
        for i in range(3):
            thread = threading.Thread(target=worker, args=(i,))
            threads.append(thread)
        
        # 启动所有线程
        for thread in threads:
            thread.start()
        
        # 等待所有线程完成
        for thread in threads:
            thread.join()
        
        # 测试事件回调的线程安全
        event_results = []
        event_lock = threading.Lock()
        
        def safe_callback(data):
            with event_lock:
                event_results.append(data)
        
        manager.register_event_callback('data_received', safe_callback)
        
        # 并发触发事件
        for i in range(5):
            manager._trigger_event('data_received', {'thread_id': i})
        
        # 等待事件处理
        time.sleep(1.0)
        
        assert len(event_results) == 5, f"事件处理数量错误: {len(event_results)} != 5"
        
        # 清理
        manager.unregister_port('test_port')
        
        logger.info("线程安全测试通过")
        return True
        
    except Exception as e:
        logger.error(f"线程安全测试失败: {e}")
        return False

def test_memory_optimization():
    """测试内存优化"""
    logger.info("开始测试内存优化...")
    
    try:
        from protocol_parser.serial_collector_optimized import OptimizedSerialCollector
        
        # 创建多个串口收集器来测试内存管理
        collectors = []
        
        for i in range(3):
            config = {
                'port': f'COM{i+1}',
                'baudrate': 115200,
                'timeout': 1.0,
                'parity': 'N',
                'stopbits': 1,
                'bytesize': 8,
                'max_buffer_size': 1024 * 1024,  # 1MB缓冲区
                'max_reconnect_attempts': 3,
                'reconnect_delay': 1.0
            }
            collector = OptimizedSerialCollector(config, **config)
            collectors.append(collector)
        
        # 测试缓冲区管理
        for i, collector in enumerate(collectors):
            # 模拟数据接收
            test_data = b'A' * 1024  # 1KB数据
            collector._buffer.append(test_data)
            
            # 检查缓冲区大小
            buffer_size = len(collector._buffer)
            assert buffer_size <= collector.max_buffer_size, f"缓冲区溢出: {buffer_size} > {collector.max_buffer_size}"
        
        # 测试资源清理
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

def run_all_tests():
    """运行所有测试"""
    logger.info("开始运行所有核心优化功能测试...")
    
    tests = [
        ("异常处理", test_exceptions),
        ("基础串口收集器", test_serial_collector_basic),
        ("基础分布式串口管理器", test_distributed_serial_manager_basic),
        ("基础插件系统", test_plugin_system_basic),
        ("线程安全", test_thread_safety),
        ("内存优化", test_memory_optimization)
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
        logger.info("🎉 所有测试通过！核心优化功能实现成功！")
        return True
    else:
        logger.error("❌ 部分测试失败，需要修复问题")
        return False

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)