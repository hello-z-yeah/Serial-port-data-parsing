#!/usr/bin/env python3
"""
Web监控和插件系统演示脚本
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

def demo_web_monitor():
    """演示Web监控功能"""
    logger.info("开始演示Web监控功能...")
    
    try:
        from protocol_parser.web_monitor import WebMonitor, WebMonitorConfig
        from protocol_parser.serial_manager import DistributedSerialManager, SerialPortConfig
        
        # 创建分布式串口管理器
        serial_manager = DistributedSerialManager(max_workers=2)
        
        # 创建插件管理器
        from protocol_parser.plugin_system import PluginManager
        plugin_manager = PluginManager()
        
        # 创建串口配置
        config1 = SerialPortConfig(
            port='COM1',
            baudrate=115200,
            max_buffer_size=1024 * 1024,
            max_reconnect_attempts=3,
            reconnect_delay=1.0
        )
        
        # 注册串口
        serial_manager.register_port('demo_port', config1)
        
        # 创建Web监控配置
        web_config = WebMonitorConfig(
            host='127.0.0.1',
            port=8080,
            debug=False,
            update_interval=1.0
        )
        
        # 创建Web监控
        web_monitor = WebMonitor(serial_manager, plugin_manager, web_config)
        
        logger.info("✅ Web监控已创建")
        logger.info("🌐 访问地址: http://127.0.0.1:8080")
        logger.info("📊 功能页面:")
        logger.info("   - 主页: /")
        logger.info("   - 仪表板: /dashboard")
        logger.info("   - 串口管理: /serial-ports")
        logger.info("   - 插件管理: /plugins")
        logger.info("   - 系统监控: /system")
        
        # 启动Web监控（不阻塞）
        web_thread = threading.Thread(target=web_monitor.run, args=('127.0.0.1', 8080))
        web_thread.daemon = True
        web_thread.start()
        
        logger.info("🚀 Web监控服务已启动")
        
        # 模拟一些数据
        for i in range(5):
            serial_manager._trigger_event('data_received', {
                'port_id': 'demo_port',
                'data': f'测试数据_{i}'.encode(),
                'size': len(f'测试数据_{i}')
            })
            time.sleep(1)
        
        # 等待一段时间让用户查看
        logger.info("⏳ 等待30秒供您查看Web界面...")
        time.sleep(30)
        
        # 停止监控
        web_monitor.stop_monitoring()
        serial_manager.unregister_port('demo_port')
        
        logger.info("🎉 Web监控演示完成")
        
    except Exception as e:
        logger.error(f"Web监控演示失败: {e}")

def demo_plugin_system():
    """演示插件系统功能"""
    logger.info("开始演示插件系统功能...")
    
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
                return "演示插件"
            
            @property
            def plugin_author(self) -> str:
                return "Demo Author"
            
            def initialize(self) -> bool:
                logger.info("🔧 演示插件初始化成功")
                return True
            
            def parse_data(self, data: bytes, context: dict = None) -> dict:
                logger.info(f"📝 演示插件解析数据: {data}")
                return {
                    'parsed': True,
                    'length': len(data),
                    'data': data.hex(),
                    'timestamp': time.time()
                }
            
            def encode_data(self, data: dict, context: dict = None) -> bytes:
                logger.info(f"🔤 演示插件编码数据: {data}")
                return bytes.fromhex(data.get('data', ''))
        
        # 创建插件目录
        demo_plugin_dir = os.path.join(os.path.dirname(__file__), 'demo_plugins')
        os.makedirs(demo_plugin_dir, exist_ok=True)
        
        # 创建演示插件文件
        demo_plugin_content = '''
from protocol_parser.plugin_system import ProtocolPlugin
import time

class DemoPlugin(ProtocolPlugin):
    @property
    def plugin_name(self) -> str:
        return "demo_plugin"
    
    @property
    def plugin_version(self) -> str:
        return "1.0.0"
    
    @property
    def plugin_description(self) -> str:
        return "演示插件"
    
    @property
    def plugin_author(self) -> str:
        return "Demo Author"
    
    def initialize(self) -> bool:
        print("🔧 演示插件初始化成功")
        return True
    
    def parse_data(self, data: bytes, context: dict = None) -> dict:
        print(f"📝 演示插件解析数据: {data}")
        return {
            'parsed': True,
            'length': len(data),
            'data': data.hex(),
            'timestamp': time.time()
        }
    
    def encode_data(self, data: dict, context: dict = None) -> bytes:
        print(f"🔤 演示插件编码数据: {data}")
        return bytes.fromhex(data.get('data', ''))
'''
        
        # 写入演示插件文件
        demo_plugin_file = os.path.join(demo_plugin_dir, 'demo_plugin.py')
        with open(demo_plugin_file, 'w') as f:
            f.write(demo_plugin_content)
        
        # 添加插件目录
        plugin_manager.add_plugin_dir(demo_plugin_dir)
        
        # 扫描插件
        plugins = plugin_manager.scan_plugins()
        logger.info(f"🔍 发现插件: {plugins}")
        
        # 加载插件
        success = plugin_manager.load_plugin('demo_plugin')
        logger.info(f"📦 插件加载: {'成功' if success else '失败'}")
        
        # 测试插件功能
        test_data = b"Hello, Demo!"
        result = plugin_manager.parse_data_with_plugins(test_data)
        logger.info(f"🔍 插件解析结果: {result}")
        
        # 测试插件配置
        config = PluginConfig(
            plugin_name='demo_plugin',
            config={'key': 'value'},
            enabled=True,
            priority=1
        )
        plugin_manager.register_plugin_config('demo_plugin', config)
        
        retrieved_config = plugin_manager.get_plugin_config('demo_plugin')
        logger.info(f"⚙️ 插件配置: {retrieved_config.config}")
        
        # 测试插件统计
        stats = plugin_manager.get_plugin_stats()
        logger.info(f"📊 插件统计: {stats}")
        
        # 清理
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

def demo_complete_integration():
    """演示完整集成功能"""
    logger.info("开始演示完整集成功能...")
    
    try:
        from protocol_parser.web_monitor import WebMonitor, WebMonitorConfig
        from protocol_parser.serial_manager import DistributedSerialManager, SerialPortConfig
        from protocol_plugin.plugin_system import PluginManager
        
        # 创建管理器
        serial_manager = DistributedSerialManager(max_workers=2)
        plugin_manager = PluginManager()
        
        # 创建演示插件
        class IntegrationPlugin(ProtocolPlugin):
            @property
            def plugin_name(self) -> str:
                return "integration_plugin"
            
            @property
            def plugin_version(self) -> str:
                return "1.0.0"
            
            @property
            def plugin_description(self) -> str:
                return "集成演示插件"
            
            @property
            def plugin_author(self) -> str:
                return "Integration Author"
            
            def initialize(self) -> bool:
                return True
            
            def parse_data(self, data: bytes, context: dict = None) -> dict:
                return {
                    'parsed': True,
                    'length': len(data),
                    'integration': True,
                    'timestamp': time.time()
                }
        
        # 创建插件目录
        integration_plugin_dir = os.path.join(os.path.dirname(__file__), 'integration_plugins')
        os.makedirs(integration_plugin_dir, exist_ok=True)
        
        # 写入集成插件文件
        integration_plugin_content = '''
from protocol_parser.plugin_system import ProtocolPlugin
import time

class IntegrationPlugin(ProtocolPlugin):
    @property
    def plugin_name(self) -> str:
        return "integration_plugin"
    
    @property
    def plugin_version(self) -> str:
        return "1.0.0"
    
    @property
    def plugin_description(self) -> str:
        return "集成演示插件"
    
    @property
    def plugin_author(self) -> str:
        return "Integration Author"
    
    def initialize(self) -> bool:
        return True
    
    def parse_data(self, data: bytes, context: dict = None) -> dict:
        return {
            'parsed': True,
            'length': len(data),
            'integration': True,
            'timestamp': time.time()
        }
'''
        
        integration_plugin_file = os.path.join(integration_plugin_dir, 'integration_plugin.py')
        with open(integration_plugin_file, 'w') as f:
            f.write(integration_plugin_content)
        
        # 添加插件目录
        plugin_manager.add_plugin_dir(integration_plugin_dir)
        
        # 加载插件
        plugin_manager.load_plugin('integration_plugin')
        
        # 创建Web监控
        web_config = WebMonitorConfig(
            host='127.0.0.1',
            port=8081,
            debug=False,
            update_interval=1.0
        )
        
        web_monitor = WebMonitor(serial_manager, plugin_manager, web_config)
        
        logger.info("🚀 完整集成演示已启动")
        logger.info("🌐 访问地址: http://127.0.0.1:8081")
        logger.info("📊 演示内容包括:")
        logger.info("   - 分布式串口管理")
        logger.info("   - 动态插件加载")
        logger.info("   - Web实时监控")
        logger.info("   - 数据集成处理")
        
        # 启动Web监控
        web_thread = threading.Thread(target=web_monitor.run, args=('127.0.0.1', 8081))
        web_thread.daemon = True
        web_thread.start()
        
        # 模拟数据流
        for i in range(10):
            # 串口数据
            serial_manager._trigger_event('data_received', {
                'port_id': 'integration_port',
                'data': f'集成数据_{i}'.encode(),
                'size': len(f'集成数据_{i}')
            })
            
            # 插件处理
            test_data = b"Integration Test"
            result = plugin_manager.parse_data_with_plugins(test_data)
            logger.info(f"🔄 集成处理结果: {result}")
            
            time.sleep(2)
        
        # 等待用户查看
        logger.info("⏳ 等待60秒供您查看完整集成演示...")
        time.sleep(60)
        
        # 清理
        web_monitor.stop_monitoring()
        plugin_manager.unload_plugin('integration_plugin')
        
        if os.path.exists(integration_plugin_file):
            os.remove(integration_plugin_file)
        
        if os.path.exists(integration_plugin_dir):
            try:
                os.rmdir(integration_plugin_dir)
            except:
                pass
        
        logger.info("🎉 完整集成演示完成")
        
    except Exception as e:
        logger.error(f"完整集成演示失败: {e}")

def main():
    """主函数"""
    logger.info("🎯 Web监控和插件系统演示开始")
    logger.info("=" * 60)
    
    # 演示1: Web监控
    logger.info("\n📊 演示1: Web监控功能")
    logger.info("-" * 40)
    demo_web_monitor()
    
    # 演示2: 插件系统
    logger.info("\n🔌 演示2: 插件系统功能")
    logger.info("-" * 40)
    demo_plugin_system()
    
    # 演示3: 完整集成
    logger.info("\n🚀 演示3: 完整集成功能")
    logger.info("-" * 40)
    demo_complete_integration()
    
    logger.info("\n🎉 所有演示完成！")
    logger.info("=" * 60)
    logger.info("📝 使用说明:")
    logger.info("1. Web监控: 访问 http://127.0.0.1:8080 或 http://127.0.0.1:8081")
    logger.info("2. 插件系统: 通过代码动态加载和管理插件")
    logger.info("3. 完整集成: 结合Web监控和插件系统的综合演示")

if __name__ == "__main__":
    main()