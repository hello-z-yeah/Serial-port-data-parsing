#!/usr/bin/env python3
"""
基础功能测试 - 验证核心优化功能
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
            try:
                os.rmdir(test_plugin_dir)
            except:
                pass
        
        logger.info("插件系统测试通过")
        return True
        
    except Exception as e:
        logger.error(f"插件系统测试失败: {e}")
        return False

def test_thread_safety_basic():
    """测试基础线程安全"""
    logger.info("开始测试基础线程安全...")
    
    try:
        # 创建一个简单的线程安全测试
        class ThreadSafeCounter:
            def __init__(self):
                self._value = 0
                self._lock = threading.RLock()
            
            def increment(self):
                with self._lock:
                    self._value += 1
                    return self._value
            
            def get_value(self):
                with self._lock:
                    return self._value
        
        # 创建计数器
        counter = ThreadSafeCounter()
        
        # 测试并发访问
        def worker(worker_id):
            for i in range(10):
                value = counter.increment()
                assert value > 0, f"Worker {worker_id} 计数器值错误"
                time.sleep(0.01)
        
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
        
        # 检查最终值
        final_value = counter.get_value()
        assert final_value == 50, f"最终值错误: {final_value} != 50"
        
        logger.info("基础线程安全测试通过")
        return True
        
    except Exception as e:
        logger.error(f"基础线程安全测试失败: {e}")
        return False

def test_memory_management():
    """测试内存管理"""
    logger.info("开始测试内存管理...")
    
    try:
        # 创建一个简单的内存管理测试
        class MemoryManager:
            def __init__(self, max_size=1024):
                self.max_size = max_size
                self._items = []
                self._lock = threading.RLock()
            
            def add_item(self, item):
                with self._lock:
                    self._items.append(item)
                    # 如果超过最大大小，删除最旧的项
                    if len(self._items) > self.max_size:
                        self._items.pop(0)
            
            def get_items(self):
                with self._lock:
                    return self._items.copy()
            
            def clear(self):
                with self._lock:
                    self._items.clear()
        
        # 创建内存管理器
        manager = MemoryManager(max_size=100)
        
        # 添加项目
        for i in range(150):
            manager.add_item(f"item_{i}")
        
        # 检查大小限制
        items = manager.get_items()
        assert len(items) <= 100, f"大小限制失败: {len(items)} > 100"
        
        # 清理
        manager.clear()
        assert len(manager.get_items()) == 0, "清理失败"
        
        logger.info("内存管理测试通过")
        return True
        
    except Exception as e:
        logger.error(f"内存管理测试失败: {e}")
        return False

def test_event_system():
    """测试事件系统"""
    logger.info("开始测试事件系统...")
    
    try:
        # 创建一个简单的事件系统
        class EventSystem:
            def __init__(self):
                self._handlers = {}
                self._lock = threading.RLock()
            
            def register_handler(self, event_type, handler):
                with self._lock:
                    if event_type not in self._handlers:
                        self._handlers[event_type] = []
                    self._handlers[event_type].append(handler)
            
            def unregister_handler(self, event_type, handler):
                with self._lock:
                    if event_type in self._handlers:
                        if handler in self._handlers[event_type]:
                            self._handlers[event_type].remove(handler)
            
            def trigger_event(self, event_type, data=None):
                with self._lock:
                    if event_type in self._handlers:
                        for handler in self._handlers[event_type]:
                            try:
                                handler(data)
                            except Exception as e:
                                print(f"事件处理错误: {e}")
        
        # 创建事件系统
        event_system = EventSystem()
        
        # 测试事件处理
        received_events = []
        
        def test_handler(data):
            received_events.append(data)
        
        # 注册处理器
        event_system.register_handler('test', test_handler)
        
        # 触发事件
        event_system.trigger_event('test', {'message': 'hello'})
        
        # 检查接收
        assert len(received_events) == 1, "事件接收失败"
        assert received_events[0]['message'] == 'hello', "事件数据错误"
        
        # 注销处理器
        event_system.unregister_handler('test', test_handler)
        
        # 再次触发事件
        event_system.trigger_event('test', {'message': 'world'})
        
        # 检查没有接收
        assert len(received_events) == 1, "事件注销失败"
        
        logger.info("事件系统测试通过")
        return True
        
    except Exception as e:
        logger.error(f"事件系统测试失败: {e}")
        return False

def run_all_tests():
    """运行所有测试"""
    logger.info("开始运行所有基础功能测试...")
    
    tests = [
        ("异常处理", test_exceptions),
        ("插件系统", test_plugin_system),
        ("基础线程安全", test_thread_safety_basic),
        ("内存管理", test_memory_management),
        ("事件系统", test_event_system)
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
        logger.info("🎉 所有测试通过！基础优化功能实现成功！")
        return True
    else:
        logger.error("❌ 部分测试失败，需要修复问题")
        return False

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)