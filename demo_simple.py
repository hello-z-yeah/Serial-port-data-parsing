#!/usr/bin/env python3
"""
简化的Web监控和插件系统演示脚本
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

def demo_plugin_system_only():
    """仅演示插件系统功能"""
    logger.info("[+] 演示插件系统功能")
    logger.info("=" * 50)
    
    try:
        from protocol_parser.plugin_system import PluginManager, ProtocolPlugin, PluginConfig
        
        # 创建插件管理器
        plugin_manager = PluginManager()
        
        # 创建演示插件
        class DemoPlugin(ProtocolPlugin):
            @property
            def plugin_name(self) -> str:
                return "demo_plugin"
            
            @property
            def plugin_version(self) -> str:
                return "1.0.0"
            
            @property
            def plugin_description(self) -> str:
                return "演示插件 - 数据解析和处理"
            
            @property
            def plugin_author(self) -> str:
                return "Demo Author"
            
            def initialize(self) -> bool:
                print("[+] 演示插件初始化成功")
                return True
            
            def parse_data(self, data: bytes, context: dict = None) -> dict:
                logger.info(f"📝 解析数据: {data}")
                return {
                    'parsed': True,
                    'length': len(data),
                    'data': data.hex(),
                    'timestamp': time.time(),
                    'features': ['hex_encoding', 'timestamp', 'length']
                }
            
            def encode_data(self, data: dict, context: dict = None) -> bytes:
                logger.info(f"🔤 编码数据: {data}")
                return bytes.fromhex(data.get('data', ''))
        
        # 创建插件目录
        demo_plugin_dir = os.path.join(os.path.dirname(__file__), 'demo_plugins')
        os.makedirs(demo_plugin_dir, exist_ok=True)
        
        # 创建演示插件文件
        demo_plugin_content = '''
from protocol_parser.plugin_system import ProtocolPlugin
import time
import logging

logger = logging.getLogger(__name__)

class DemoPlugin(ProtocolPlugin):
    @property
    def plugin_name(self) -> str:
        return "demo_plugin"
    
    @property
    def plugin_version(self) -> str:
        return "1.0.0"
    
    @property
    def plugin_description(self) -> str:
        return "演示插件 - 数据解析和处理"
    
    @property
    def plugin_author(self) -> str:
        return "Demo Author"
    
    def initialize(self) -> bool:
        logger.info("[+] 演示插件初始化成功")
        return True
    
    def parse_data(self, data: bytes, context: dict = None) -> dict:
        logger.info(f"[+] 解析数据: {data}")
        return {
            'parsed': True,
            'length': len(data),
            'data': data.hex(),
            'timestamp': time.time(),
            'features': ['hex_encoding', 'timestamp', 'length']
        }
    
    def encode_data(self, data: dict, context: dict = None) -> bytes:
        logger.info(f"[+] 编码数据: {data}")
        return bytes.fromhex(data.get('data', ''))
'''
        
        # 写入演示插件文件
        demo_plugin_file = os.path.join(demo_plugin_dir, 'demo_plugin.py')
        with open(demo_plugin_file, 'w') as f:
            f.write(demo_plugin_content)
        
        # 添加插件目录
        plugin_manager.add_plugin_dir(demo_plugin_dir)
        
        logger.info("🔍 步骤1: 扫描插件")
        plugins = plugin_manager.scan_plugins()
        logger.info(f"发现插件: {plugins}")
        
        logger.info("📦 步骤2: 加载插件")
        success = plugin_manager.load_plugin('demo_plugin')
        logger.info(f"插件加载: {'成功' if success else '失败'}")
        
        logger.info("🔍 步骤3: 测试插件功能")
        test_data_list = [
            b"Hello, World!",
            b"Serial Data",
            b"Protocol Parser",
            b"Plugin System"
        ]
        
        for i, test_data in enumerate(test_data_list):
            logger.info(f"测试数据 {i+1}: {test_data}")
            result = plugin_manager.parse_data_with_plugins(test_data)
            logger.info(f"解析结果: {result}")
            time.sleep(1)
        
        logger.info("⚙️ 步骤4: 配置管理")
        config = PluginConfig(
            plugin_name='demo_plugin',
            config={'timeout': 30, 'retry': 3},
            enabled=True,
            priority=1
        )
        plugin_manager.register_plugin_config('demo_plugin', config)
        
        retrieved_config = plugin_manager.get_plugin_config('demo_plugin')
        logger.info(f"插件配置: {retrieved_config.config}")
        
        logger.info("📊 步骤5: 统计信息")
        stats = plugin_manager.get_plugin_stats()
        logger.info(f"插件统计: {stats}")
        
        logger.info("🔄 步骤6: 热插拔测试")
        logger.info("禁用插件...")
        plugin_manager.disable_plugin('demo_plugin')
        time.sleep(1)
        
        logger.info("重新启用插件...")
        plugin_manager.enable_plugin('demo_plugin')
        time.sleep(1)
        
        logger.info("🧹 步骤7: 清理资源")
        plugin_manager.unload_plugin('demo_plugin')
        
        # 删除演示插件文件
        if os.path.exists(demo_plugin_file):
            os.remove(demo_plugin_file)
        
        if os.path.exists(demo_plugin_dir):
            try:
                os.rmdir(demo_plugin_dir)
            except:
                pass
        
        logger.info("🎉 插件系统演示完成")
        
    except Exception as e:
        logger.error(f"插件系统演示失败: {e}")

def demo_web_monitor_basic():
    """演示基础Web监控功能"""
    logger.info("[+] 演示基础Web监控功能")
    logger.info("=" * 50)
    
    try:
        from protocol_parser.web_monitor import WebMonitor, WebMonitorConfig
        
        # 创建模拟的管理器
        class MockSerialManager:
            def get_all_ports_status(self):
                return {
                    'COM1': {'is_connected': True, 'is_running': True, 'data_received': 1024},
                    'COM2': {'is_connected': False, 'is_running': False, 'data_received': 0}
                }
            
            def get_performance_metrics(self):
                return {
                    'total_data_received': 2048,
                    'total_connections': 10,
                    'total_errors': 2
                }
            
            def get_system_info(self):
                return {
                    'start_time': time.time(),
                    'total_ports': 2,
                    'active_ports': 1
                }
        
        class MockPluginManager:
            def get_plugin_stats(self):
                return {
                    'total_plugins': 3,
                    'enabled_plugins': 2,
                    'disabled_plugins': 1
                }
        
        # 创建Web监控配置
        config = WebMonitorConfig(
            host='127.0.0.1',
            port=8080,
            debug=False,
            update_interval=1.0
        )
        
        # 创建Web监控
        web_monitor = WebMonitor(MockSerialManager(), MockPluginManager(), config)
        
        logger.info("🌐 Web监控已创建")
        logger.info("📍 访问地址: http://127.0.0.1:8080")
        logger.info("📊 可用页面:")
        logger.info("   - 主页: /")
        logger.info("   - 仪表板: /dashboard")
        logger.info("   - 串口管理: /serial-ports")
        logger.info("   - 插件管理: /plugins")
        logger.info("   - 系统监控: /system")
        
        # 启动Web监控
        web_thread = threading.Thread(target=web_monitor.run, args=('127.0.0.1', 8080))
        web_thread.daemon = True
        web_thread.start()
        
        logger.info("🚀 Web服务已启动")
        logger.info("⏳ 运行30秒供您访问...")
        
        # 模拟数据更新
        for i in range(30):
            web_monitor._collect_system_metrics()
            time.sleep(1)
        
        logger.info("🛑 停止Web监控")
        web_monitor.stop_monitoring()
        
        logger.info("🎉 Web监控演示完成")
        
    except Exception as e:
        logger.error(f"Web监控演示失败: {e}")

def demo_usage_examples():
    """演示使用示例"""
    logger.info("[+] 使用示例")
    logger.info("=" * 50)
    
    logger.info("1. 插件系统使用示例:")
    logger.info("""
    # 创建插件管理器
    from protocol_parser.plugin_system import PluginManager
    plugin_manager = PluginManager()
    
    # 扫描插件
    plugins = plugin_manager.scan_plugins()
    
    # 加载插件
    plugin_manager.load_plugin('my_plugin')
    
    # 使用插件
    result = plugin_manager.parse_data_with_plugins(b"test data")
    
    # 管理插件
    plugin_manager.enable_plugin('my_plugin')
    plugin_manager.disable_plugin('my_plugin')
    """)
    
    logger.info("2. Web监控使用示例:")
    logger.info("""
    # 创建Web监控
    from protocol_parser.web_monitor import WebMonitor, WebMonitorConfig
    
    config = WebMonitorConfig(host='0.0.0.0', port=8080)
    web_monitor = WebMonitor(serial_manager, plugin_manager, config)
    
    # 启动Web服务
    web_monitor.run(host='0.0.0.0', port=8080)
    
    # 访问 http://localhost:8080 查看监控界面
    """)
    
    logger.info("3. 完整集成示例:")
    logger.info("""
    # 创建分布式管理器
    from protocol_parser.serial_manager import DistributedSerialManager
    manager = DistributedSerialManager(max_workers=4)
    
    # 注册串口
    from protocol_parser.serial_manager import SerialPortConfig
    config = SerialPortConfig(port='COM1', baudrate=115200)
    manager.register_port('port1', config)
    
    # 创建Web监控
    web_monitor = WebMonitor(manager, plugin_manager, config)
    
    # 启动服务
    web_monitor.run(host='0.0.0.0', port=8080)
    """)

def main():
    """主函数"""
    logger.info("[*] Web监控和插件系统演示")
    logger.info("=" * 60)
    
    # 演示插件系统
    demo_plugin_system_only()
    print()
    
    # 演示Web监控
    demo_web_monitor_basic()
    print()
    
    # 使用示例
    demo_usage_examples()
    
    logger.info("\n[+] 演示完成！")
    logger.info("=" * 60)
    logger.info("[+] 功能说明:")
    logger.info("[+] 插件系统: 支持动态加载、配置管理、热插拔")
    logger.info("[+] Web监控: 提供实时数据监控、可视化界面")
    logger.info("[+] 完整集成: 支持分布式架构和插件扩展")
    logger.info("")
    logger.info("[+] 访问地址:")
    logger.info("   - Web监控: http://127.0.0.1:8080")
    logger.info("   - 串口管理: http://127.0.0.1:8080/serial-ports")
    logger.info("   - 插件管理: http://127.0.0.1:8080/plugins")
    logger.info("   - 系统监控: http://127.0.0.1:8080/system")

if __name__ == "__main__":
    main()