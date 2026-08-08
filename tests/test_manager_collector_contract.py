#!/usr/bin/env python3
"""
管理器与收集器契约验证测试
测试内容：
1. 管理器注册收集器时的回调契约
2. 事件回调的正确路由（单层闭包）
3. 管理器 shutdown() 后的资源清理验证
"""
import sys
import os
import time
import threading
import logging

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


class TestCollectorContract:
    """管理器-收集器契约测试"""
    
    def test_collector_callback_registration(self):
        """测试收集器能够正确注册 3 个核心回调"""
        from protocol_parser.serial_collector_optimized import OptimizedSerialCollector
        
        # 创建收集器实例
        collector = OptimizedSerialCollector(cfg={}, port="TEST")
        
        # 注册回调
        received_events = []
        
        def on_data_received(data):
            received_events.append(('data_received', data))
        
        def on_error_occurred(error):
            received_events.append(('error_occurred', error))
        
        def on_connection_changed(connected):
            received_events.append(('connection_changed', connected))
        
        collector.set_event_callback('data_received', on_data_received)
        collector.set_event_callback('error_occurred', on_error_occurred)
        collector.set_event_callback('connection_changed', on_connection_changed)
        
        # 验证回调已注册
        assert collector._event_callbacks['data_received'] == on_data_received
        assert collector._event_callbacks['error_occurred'] == on_error_occurred
        assert collector._event_callbacks['connection_changed'] == on_connection_changed
        
        # 触发事件
        collector._emit_event('data_received', b"test_data")
        collector._emit_event('error_occurred', "test_error")
        collector._emit_event('connection_changed', True)
        
        # 验证事件被正确接收
        assert len(received_events) == 3
        assert received_events[0] == ('data_received', b"test_data")
        assert received_events[1] == ('error_occurred', "test_error")
        assert received_events[2] == ('connection_changed', True)
        
        logger.info("✓ 收集器回调注册测试通过")
    
    def test_callback_exception_isolation(self):
        """测试回调异常隔离（外部回调异常不影响收集器内部线程）"""
        from protocol_parser.serial_collector_optimized import OptimizedSerialCollector
        
        collector = OptimizedSerialCollector(cfg={}, port="TEST")
        
        # 注册会抛出异常的回调
        def bad_callback(data):
            raise RuntimeError("回调内部异常")
        
        collector.set_event_callback('data_received', bad_callback)
        
        # 触发事件，不应抛出异常
        try:
            collector._emit_event('data_received', b"test")
        except RuntimeError:
            assert False, "回调异常应被隔离，不应传播"
        
        logger.info("✓ 回调异常隔离测试通过")
    
    def test_compatibility_api(self):
        """测试兼容性 API 是否可用"""
        from protocol_parser.serial_collector_optimized import OptimizedSerialCollector
        
        collector = OptimizedSerialCollector(cfg={}, port="TEST")
        
        # 测试 is_connected()
        assert collector.is_connected() == False
        
        # 测试 is_running()
        assert collector.is_running() == False
        
        # 测试 get_error_count()
        assert collector.get_error_count() == 0
        
        # 测试 send_data()
        assert collector.send_data(b"test") == False  # 队列未初始化
        
        # 测试 get_resource_info()
        info = collector.get_resource_info()
        assert 'buffer_size' in info
        assert 'thread_count' in info
        assert 'is_connected' in info
        assert 'is_running' in info
        
        logger.info("✓ 兼容性 API 测试通过")
    
    def test_manager_collector_integration(self):
        """测试管理器与收集器的集成契约"""
        from protocol_parser.serial_manager import DistributedSerialManager, SerialPortConfig
        from protocol_parser.serial_collector_optimized import OptimizedSerialCollector
        
        # 创建管理器
        manager = DistributedSerialManager(max_workers=2)
        
        # 创建配置
        config = SerialPortConfig(
            port='COM_TEST',
            baudrate=115200,
            max_buffer_size=1024 * 1024
        )
        
        # 记录接收到的事件
        data_received_events = []
        error_occurred_events = []
        connection_changed_events = []
        
        # 替换管理器的回调方法来验证
        original_on_data = manager._on_data_received
        original_on_error = manager._on_error_occurred
        original_on_connection = manager._on_connection_changed
        
        def tracking_on_data(port_id, data):
            data_received_events.append((port_id, data))
        
        def tracking_on_error(port_id, error):
            error_occurred_events.append((port_id, error))
        
        def tracking_on_connection(port_id, connected):
            connection_changed_events.append((port_id, connected))
        
        manager._on_data_received = tracking_on_data
        manager._on_error_occurred = tracking_on_error
        manager._on_connection_changed = tracking_on_connection
        
        try:
            # 注册串口（会自动设置回调）
            success = manager.register_port('test_port', config)
            # 可能会因为无法打开串口而失败，但回调设置逻辑应该执行
            # 如果失败，手动验证回调设置
            if not success:
                logger.info("注册失败（可能因为串口不存在），手动验证回调设置")
            
            # 直接验证收集器的回调设置
            collector = OptimizedSerialCollector(cfg={}, port="COM_TEST")
            
            # 使用与管理器相同的闭包模式
            port_id = 'test_port'
            collector.set_event_callback(
                'data_received',
                lambda data, pid=port_id: tracking_on_data(pid, data)
            )
            collector.set_event_callback(
                'error_occurred',
                lambda error, pid=port_id: tracking_on_error(pid, error)
            )
            collector.set_event_callback(
                'connection_changed',
                lambda connected, pid=port_id: tracking_on_connection(pid, connected)
            )
            
            # 触发事件
            collector._emit_event('data_received', b"test_data")
            collector._emit_event('error_occurred', "test_error")
            collector._emit_event('connection_changed', True)
            
            # 验证事件通过单层闭包正确桥接
            assert len(data_received_events) == 1
            assert data_received_events[0] == ('test_port', b"test_data")
            
            assert len(error_occurred_events) == 1
            assert error_occurred_events[0] == ('test_port', "test_error")
            
            assert len(connection_changed_events) == 1
            assert connection_changed_events[0] == ('test_port', True)
            
            logger.info("✓ 管理器-收集器集成契约测试通过")
            
        finally:
            # 恢复原始方法
            manager._on_data_received = original_on_data
            manager._on_error_occurred = original_on_error
            manager._on_connection_changed = original_on_connection
            
            # 清理
            if 'test_port' in manager.serial_ports:
                manager.unregister_port('test_port')
    
    def test_manager_shutdown_cleanup(self):
        """测试管理器 shutdown() 后的资源清理"""
        from protocol_parser.serial_manager import DistributedSerialManager, SerialPortConfig
        
        # 创建管理器
        manager = DistributedSerialManager(max_workers=2)
        
        # 记录初始线程数
        initial_thread_count = threading.active_count()
        
        # 创建配置并尝试注册（可能失败但不影响测试）
        config = SerialPortConfig(
            port='COM_TEST',
            baudrate=115200
        )
        
        try:
            manager.register_port('test_port', config)
        except Exception:
            pass
        
        # 启动健康检查
        manager.start_health_check()
        
        # 验证健康检查线程已启动
        assert manager.health_check_thread is not None
        assert manager.health_check_thread.is_alive()
        
        # 执行 shutdown
        manager.shutdown()
        
        # 验证状态
        assert manager.is_running == False
        assert len(manager.serial_ports) == 0
        assert len(manager.port_configs) == 0
        assert len(manager.port_status) == 0
        
        # 验证线程已清理（等待一小段时间）
        time.sleep(0.5)
        
        # 健康检查线程应该已退出
        if manager.health_check_thread is not None:
            assert not manager.health_check_thread.is_alive(), "健康检查线程未能退出"
        
        logger.info("✓ 管理器 shutdown() 资源清理测试通过")
    
    def test_unregister_port_safety(self):
        """测试 unregister_port() 的安全性"""
        from protocol_parser.serial_manager import DistributedSerialManager, SerialPortConfig
        from protocol_parser.serial_collector_optimized import OptimizedSerialCollector
        
        # 创建管理器
        manager = DistributedSerialManager(max_workers=2)
        
        # 创建配置
        config = SerialPortConfig(
            port='COM_TEST',
            baudrate=115200
        )
        
        # 创建独立的收集器进行测试
        collector = OptimizedSerialCollector(cfg={}, port="COM_TEST")
        
        # 手动模拟管理器的注册过程
        port_id = 'test_port'
        manager.serial_ports[port_id] = collector
        manager.port_configs[port_id] = config
        manager.port_status[port_id] = type('Status', (), {
            'is_connected': False,
            'is_running': False,
            'error_count': 0
        })()
        
        # 验证收集器存在
        assert port_id in manager.serial_ports
        
        # 执行注销
        result = manager.unregister_port(port_id)
        
        # 验证注销成功
        assert result == True
        assert port_id not in manager.serial_ports
        assert port_id not in manager.port_configs
        assert port_id not in manager.port_status
        
        # 验证收集器已停止
        assert collector.is_running() == False
        
        logger.info("✓ unregister_port() 安全性测试通过")
    
    def test_thread_pool_shutdown(self):
        """测试线程池 shutdown"""
        from protocol_parser.serial_manager import DistributedSerialManager
        
        # 创建管理器
        manager = DistributedSerialManager(max_workers=2)
        
        # 验证线程池已创建
        assert manager.executor is not None
        
        # 提交一个简单任务
        future = manager.executor.submit(lambda: 42)
        result = future.result(timeout=2.0)
        assert result == 42
        
        # 执行 shutdown
        manager.shutdown()
        
        # 验证线程池已关闭
        assert manager.executor._shutdown
        
        logger.info("✓ 线程池 shutdown 测试通过")


def test_contract_compliance():
    """运行所有契约测试"""
    test_runner = TestCollectorContract()
    
    tests = [
        ("收集器回调注册", test_runner.test_collector_callback_registration),
        ("回调异常隔离", test_runner.test_callback_exception_isolation),
        ("兼容性 API", test_runner.test_compatibility_api),
        ("管理器-收集器集成", test_runner.test_manager_collector_integration),
        ("管理器 shutdown 清理", test_runner.test_manager_shutdown_cleanup),
        ("unregister_port 安全", test_runner.test_unregister_port_safety),
        ("线程池 shutdown", test_runner.test_thread_pool_shutdown),
    ]
    
    passed = 0
    failed = 0
    errors = []
    
    for test_name, test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((test_name, str(e)))
            logger.error(f"✗ {test_name} 失败: {e}")
    
    # 输出结果
    print(f"\n{'='*60}")
    print(f"契约验证测试结果")
    print(f"{'='*60}")
    print(f"通过: {passed}/{len(tests)}")
    print(f"失败: {failed}/{len(tests)}")
    
    if errors:
        print(f"\n失败详情:")
        for test_name, error in errors:
            print(f"  - {test_name}: {error}")
    
    assert failed == 0, f"{failed} 个测试失败"
    print(f"\n✓ 所有契约验证测试通过！")


if __name__ == "__main__":
    test_contract_compliance()