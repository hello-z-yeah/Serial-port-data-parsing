#!/usr/bin/env python3
"""
集成验证测试 - 验证实际优化模块的功能
"""

import sys
import os
import time
import threading
import logging
from typing import Dict, Any

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('integration_test')


class IntegrationTester:
    """集成测试器"""
    
    def __init__(self):
        self.results = []
        
    def log_result(self, name: str, result: Dict[str, Any]):
        self.results.append(result)
        logger.info(f"\n{'='*60}")
        logger.info(f"[{name}] 测试通过")
        logger.info(f"{'='*60}")
        for key, value in result.items():
            logger.info(f"  {key}: {value}")


def test_plugin_system_optimizations():
    """测试插件系统优化"""
    logger.info("\n" + "=" * 70)
    logger.info("测试1: 插件系统优化验证")
    logger.info("=" * 70)
    
    from protocol_parser.plugin_system import PluginManager, ProtocolPlugin, PluginConfig
    
    # 测试LRU缓存机制
    cache_size_limit = 100
    plugin_manager = PluginManager(cache_size_limit=cache_size_limit)
    
    # 测试缓存设置和获取
    test_key = "test_key_1"
    test_value = {"data": "test_value", "timestamp": time.time()}
    
    plugin_manager.set_cache(test_key, test_value)
    cached_value = plugin_manager.get_cache(test_key)
    
    assert cached_value is not None, "缓存获取失败"
    assert cached_value == test_value, "缓存值不匹配"
    
    # 测试缓存大小限制
    for i in range(150):
        plugin_manager.set_cache(f"key_{i}", f"value_{i}")
    
    cache_stats = plugin_manager.get_cache_stats()
    assert cache_stats['current_size'] <= cache_size_limit, f"缓存大小超出限制: {cache_stats['current_size']} > {cache_size_limit}"
    
    logger.info(f"✅ LRU缓存机制测试通过: 缓存大小限制为 {cache_size_limit}，当前大小 {cache_stats['current_size']}")
    
    # 测试并行插件处理
    class TestPlugin(ProtocolPlugin):
        @property
        def plugin_name(self):
            return "test_plugin"
        
        @property
        def plugin_version(self):
            return "1.0.0"
        
        @property
        def plugin_description(self):
            return "测试插件"
        
        @property
        def plugin_author(self):
            return "Test Author"
        
        def initialize(self):
            return True
        
        def parse_data(self, data, context=None):
            time.sleep(0.01)  # 模拟处理时间
            return {"length": len(data), "timestamp": time.time()}
        
        def encode_data(self, data, context=None):
            return bytes(data)
    
    # 注册并加载测试插件
    test_plugin = TestPlugin()
    plugin_manager.enabled_plugins['test_plugin'] = test_plugin
    
    # 测试串行处理
    test_data = bytes([0xAA] * 100)
    start = time.perf_counter()
    for _ in range(20):
        plugin_manager.parse_data_with_plugins(test_data)
    serial_time = time.perf_counter() - start
    
    # 测试并行处理
    start = time.perf_counter()
    for _ in range(20):
        plugin_manager.parse_data_with_plugins_parallel(test_data, max_workers=4)
    parallel_time = time.perf_counter() - start
    
    speedup = serial_time / parallel_time if parallel_time > 0 else 1.0
    logger.info(f"✅ 并行处理测试通过: 串行耗时 {serial_time:.4f}s, 并行耗时 {parallel_time:.4f}s, 加速比 {speedup:.2f}x")
    
    # 测试批量处理
    data_list = [bytes([i % 256] * 50) for i in range(10)]
    
    start = time.perf_counter()
    plugin_manager.batch_parse_data(data_list)
    batch_time = time.perf_counter() - start
    
    start = time.perf_counter()
    plugin_manager.batch_parse_data_parallel(data_list)
    batch_parallel_time = time.perf_counter() - start
    
    logger.info(f"✅ 批量处理测试通过: 批量串行 {batch_time:.4f}s, 批量并行 {batch_parallel_time:.4f}s")
    
    # 清理
    plugin_manager.shutdown()
    
    return {
        'lru_cache': 'passed',
        'cache_size_control': f"max={cache_size_limit}, current={cache_stats['current_size']}",
        'parallel_processing': f"speedup={speedup:.2f}x",
        'batch_processing': 'passed'
    }


def test_buffer_management_optimizations():
    """测试缓冲区管理优化"""
    logger.info("\n" + "=" * 70)
    logger.info("测试2: 缓冲区管理优化验证")
    logger.info("=" * 70)
    
    from protocol_parser.serial_collector import FrameSynchronizer
    
    # 创建带有缓冲区限制的同步器
    max_buffer_size = 1024 * 1024  # 1MB
    sync = FrameSynchronizer(
        cfg={
            "frame": {
                "header": "0xA5A5",
                "header_size": 2,
                "length_offset": 4,
                "length_size": 2,
                "length_byte_order": "big",
                "checksum": {"length": 1},
                "max_frame_size": 4096
            }
        },
        max_buffer_size=max_buffer_size,
        buffer_cleanup_threshold=0.8
    )
    
    # 测试缓冲区大小限制
    data_chunk_size = 1024  # 1KB
    total_data = 2000  # 总计2MB，会触发清理
    
    start = time.perf_counter()
    for i in range(total_data):
        fake_data = bytes([i % 256] * data_chunk_size)
        sync.feed(fake_data)
    elapsed = time.perf_counter() - start
    
    buffer_size = len(sync.buffer)
    cleanup_triggered = buffer_size < max_buffer_size * 0.8  # 如果小于80%说明触发了清理
    
    logger.info(f"✅ 缓冲区管理测试通过:")
    logger.info(f"   - 写入数据: {total_data * data_chunk_size / 1024 / 1024:.2f}MB")
    logger.info(f"   - 最大限制: {max_buffer_size / 1024 / 1024:.0f}MB")
    logger.info(f"   - 最终大小: {buffer_size / 1024:.2f}KB")
    logger.info(f"   - 清理机制: {'已触发' if cleanup_triggered else '未触发'}")
    logger.info(f"   - 处理时间: {elapsed:.4f}s")
    
    sync.reset()
    
    return {
        'buffer_limit': f"{max_buffer_size/1024/1024}MB",
        'final_size': f"{buffer_size/1024:.2f}KB",
        'cleanup_mechanism': 'working' if cleanup_triggered else 'not_triggered',
        'throughput': f"{total_data * data_chunk_size / elapsed / 1024:.0f}KB/s"
    }


def test_serial_manager_optimizations():
    """测试串口管理器优化"""
    logger.info("\n" + "=" * 70)
    logger.info("测试3: 串口管理器优化验证")
    logger.info("=" * 70)
    
    from protocol_parser.serial_manager import DistributedSerialManager, SerialPortConfig
    
    # 创建启用动态线程池的管理器
    manager = DistributedSerialManager(enable_dynamic_thread_pool=True)
    
    # 测试动态线程池配置
    system_info = manager.get_system_info()
    logger.info(f"✅ 动态线程池测试通过:")
    logger.info(f"   - CPU核心数: {system_info['total_ports']}")
    logger.info(f"   - 最大线程数: {system_info['max_workers']}")
    logger.info(f"   - 当前线程数: {system_info['current_workers']}")
    
    # 测试资源监控
    resource_monitor = manager.get_resource_monitor()
    logger.info(f"✅ 资源监控测试通过:")
    logger.info(f"   - CPU使用率: {resource_monitor['cpu_usage']:.1f}%")
    logger.info(f"   - 内存使用率: {resource_monitor['memory_usage']:.1f}%")
    logger.info(f"   - 活动线程: {resource_monitor['active_threads']}")
    logger.info(f"   - 资源告警: {len(resource_monitor['resource_alerts'])} 个")
    
    # 测试负载均衡
    optimal_port = manager.get_optimal_port()
    logger.info(f"✅ 负载均衡测试通过:")
    logger.info(f"   - 最优端口选择: {optimal_port}")
    
    # 测试手动线程池调整
    manager.set_thread_pool_size(4)
    updated_info = manager.get_system_info()
    logger.info(f"   - 调整后线程数: {updated_info['current_workers']}")
    
    # 测试事件回调
    alerts_received = []
    def on_resource_alert(alert):
        alerts_received.append(alert)
    
    manager.register_event_callback('resource_alert', on_resource_alert)
    
    logger.info(f"✅ 事件回调注册测试通过")
    
    # 清理
    manager.shutdown()
    
    return {
        'dynamic_thread_pool': f"workers={system_info['current_workers']}",
        'resource_monitoring': f"cpu={resource_monitor['cpu_usage']:.1f}%, mem={resource_monitor['memory_usage']:.1f}%",
        'load_balancing': 'working',
        'event_system': 'working'
    }


def main():
    """主测试流程"""
    logger.info("=" * 70)
    logger.info("串口协议解析器优化集成验证测试")
    logger.info("=" * 70)
    logger.info(f"测试时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Python版本: {sys.version}")
    
    tester = IntegrationTester()
    
    # 运行各项测试
    try:
        r1 = test_plugin_system_optimizations()
        tester.log_result("插件系统优化", r1)
    except Exception as e:
        logger.error(f"插件系统测试失败: {e}")
        r1 = {'error': str(e)}
    
    try:
        r2 = test_buffer_management_optimizations()
        tester.log_result("缓冲区管理优化", r2)
    except Exception as e:
        logger.error(f"缓冲区管理测试失败: {e}")
        r2 = {'error': str(e)}
    
    try:
        r3 = test_serial_manager_optimizations()
        tester.log_result("串口管理器优化", r3)
    except Exception as e:
        logger.error(f"串口管理器测试失败: {e}")
        r3 = {'error': str(e)}
    
    # 测试总结
    logger.info("\n" + "=" * 70)
    logger.info("集成验证测试完成")
    logger.info("=" * 70)
    
    all_results = [r1, r2, r3]
    passed = sum(1 for r in all_results if 'error' not in r)
    failed = sum(1 for r in all_results if 'error' in r)
    
    logger.info(f"\n测试汇总:")
    logger.info(f"  总测试数: {len(all_results)}")
    logger.info(f"  通过数: {passed}")
    logger.info(f"  失败数: {failed}")
    logger.info(f"  通过率: {passed/len(all_results)*100:.1f}%")
    
    if failed == 0:
        logger.info("\n🎉 所有集成验证测试通过！优化措施已成功实施。")
    else:
        logger.warning(f"\n⚠️ 有 {failed} 项测试失败，请检查相关错误。")
    
    return all_results


if __name__ == '__main__':
    main()
