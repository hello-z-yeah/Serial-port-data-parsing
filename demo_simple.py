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
    
    logger.info("2. 完整集成示例:")
    logger.info("""
    # 创建分布式管理器
    from protocol_parser.serial_manager import DistributedSerialManager
    manager = DistributedSerialManager(max_workers=4)
    
    # 注册串口
    from protocol_parser.serial_manager import SerialPortConfig
    config = SerialPortConfig(port='COM1', baudrate=115200)
    manager.register_port('port1', config)
    """)

def main():
    """主函数"""
    logger.info("[*] 插件系统演示")
    logger.info("=" * 60)
    
    # 演示插件系统
    demo_plugin_system_only()
    print()
    
    # 使用示例
    demo_usage_examples()
    
    logger.info("\n[+] 演示完成！")
    logger.info("=" * 60)
    logger.info("[+] 功能说明:")
    logger.info("[+] 插件系统: 支持动态加载、配置管理、热插拔")
    logger.info("[+] 完整集成: 支持分布式架构和插件扩展")

if __name__ == "__main__":
    main()