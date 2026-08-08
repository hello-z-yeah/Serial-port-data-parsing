"""
插件系统 - 支持协议插件扩展
"""
import importlib.util
import importlib
import inspect
import os
import sys
import logging
import json
import threading
import time
from typing import Dict, List, Optional, Type, Any, Callable
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import pickle
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from .exceptions import ProtocolParserError, PluginError


@dataclass
class PluginInfo:
    """插件信息类"""
    name: str
    version: str
    description: str
    author: str
    plugin_class: Type
    file_path: str
    dependencies: List[str] = field(default_factory=list)
    config_schema: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    load_time: float = 0.0
    last_error: Optional[str] = None


@dataclass
class PluginConfig:
    """插件配置类"""
    plugin_name: str
    config: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    auto_load: bool = True
    priority: int = 0


class ProtocolPlugin(ABC):
    """协议插件基类"""
    
    def __init__(self, config: Dict[str, Any] = None):
        """
        初始化插件
        
        Args:
            config: 插件配置
        """
        self.config = config or {}
        self.logger = logging.getLogger(f"plugin.{self.__class__.__name__}")
        self._event_handlers = {}
        self._cache = {}
        self._cache_ttl = 300.0  # 缓存过期时间（秒）
        
    @property
    @abstractmethod
    def plugin_name(self) -> str:
        """插件名称"""
        pass
    
    @property
    @abstractmethod
    def plugin_version(self) -> str:
        """插件版本"""
        pass
    
    @property
    @abstractmethod
    def plugin_description(self) -> str:
        """插件描述"""
        pass
    
    @property
    @abstractmethod
    def plugin_author(self) -> str:
        """插件作者"""
        pass
    
    @abstractmethod
    def initialize(self) -> bool:
        """
        初始化插件
        
        Returns:
            bool: 初始化是否成功
        """
        pass
    
    @abstractmethod
    def parse_data(self, data: bytes, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        解析数据
        
        Args:
            data: 要解析的数据
            context: 解析上下文
            
        Returns:
            Dict[str, Any]: 解析结果
        """
        pass
    
    @abstractmethod
    def encode_data(self, data: Dict[str, Any], context: Dict[str, Any] = None) -> bytes:
        """
        编码数据
        
        Args:
            data: 要编码的数据
            context: 编码上下文
            
        Returns:
            bytes: 编码后的数据
        """
        pass
    
    def register_event_handler(self, event_type: str, handler: Callable) -> None:
        """
        注册事件处理器
        
        Args:
            event_type: 事件类型
            handler: 事件处理器
        """
        if event_type not in self._event_handlers:
            self._event_handlers[event_type] = []
        self._event_handlers[event_type].append(handler)
    
    def unregister_event_handler(self, event_type: str, handler: Callable) -> None:
        """
        注销事件处理器
        
        Args:
            event_type: 事件类型
            handler: 事件处理器
        """
        if event_type in self._event_handlers:
            if handler in self._event_handlers[event_type]:
                self._event_handlers[event_type].remove(handler)
    
    def trigger_event(self, event_type: str, data: Any = None) -> None:
        """
        触发事件
        
        Args:
            event_type: 事件类型
            data: 事件数据
        """
        if event_type in self._event_handlers:
            for handler in self._event_handlers[event_type]:
                try:
                    handler(data)
                except Exception as e:
                    self.logger.error(f"事件处理器错误: {e}")
    
    def get_cache(self, key: str) -> Optional[Any]:
        """
        获取缓存数据
        
        Args:
            key: 缓存键
            
        Returns:
            Optional[Any]: 缓存数据
        """
        if key in self._cache:
            cache_data = self._cache[key]
            if time.time() - cache_data['timestamp'] < self._cache_ttl:
                return cache_data['data']
            else:
                del self._cache[key]
        return None
    
    def set_cache(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """
        设置缓存数据
        
        Args:
            key: 缓存键
            value: 缓存值
            ttl: 过期时间（秒），如果为None则使用默认值
        """
        self._cache[key] = {
            'data': value,
            'timestamp': time.time(),
            'ttl': ttl or self._cache_ttl
        }
    
    def clear_cache(self) -> None:
        """清空缓存"""
        self._cache.clear()
    
    def get_config(self, key: str, default: Any = None) -> Any:
        """
        获取配置值
        
        Args:
            key: 配置键
            default: 默认值
            
        Returns:
            Any: 配置值
        """
        return self.config.get(key, default)
    
    def set_config(self, key: str, value: Any) -> None:
        """
        设置配置值
        
        Args:
            key: 配置键
            value: 配置值
        """
        self.config[key] = value
    
    def validate_config(self) -> bool:
        """
        验证配置
        
        Returns:
            bool: 配置是否有效
        """
        return True
    
    def shutdown(self) -> None:
        """
        关闭插件（释放后台连接、资源或定时器）
        
        子类应重写此方法以释放持有的资源。
        """
        pass


class PluginManager:
    """插件管理器"""
    
    def __init__(self, plugin_dirs: List[str] = None, cache_dir: str = None, cache_size_limit: int = 1000):
        """
        初始化插件管理器
        
        Args:
            plugin_dirs: 插件目录列表
            cache_dir: 缓存目录
            cache_size_limit: 缓存大小限制
        """
        self.plugin_dirs = plugin_dirs or []
        self.cache_dir = cache_dir or os.path.join(os.path.dirname(__file__), 'plugin_cache')
        self.plugins: Dict[str, PluginInfo] = {}
        self.plugin_configs: Dict[str, PluginConfig] = {}
        self.enabled_plugins: Dict[str, ProtocolPlugin] = {}
        
        # 线程安全
        self._lock = threading.RLock()
        
        # 日志
        self.logger = logging.getLogger(__name__)
        
        # 缓存 - 使用 OrderedDict 实现 LRU 缓存
        self._plugin_cache: OrderedDict[str, tuple] = OrderedDict()  # key -> (value, timestamp)
        self._cache_ttl = 60.0  # 缓存过期时间（秒）
        self._cache_size_limit = cache_size_limit  # 缓存大小限制
        
        # 创建缓存目录
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # 初始化插件目录
        self._init_plugin_dirs()
        
    def _init_plugin_dirs(self) -> None:
        """初始化插件目录"""
        for plugin_dir in self.plugin_dirs:
            if not os.path.exists(plugin_dir):
                os.makedirs(plugin_dir, exist_ok=True)
                self.logger.info(f"创建插件目录: {plugin_dir}")
    
    def add_plugin_dir(self, plugin_dir: str) -> None:
        """
        添加插件目录
        
        Args:
            plugin_dir: 插件目录
        """
        if plugin_dir not in self.plugin_dirs:
            self.plugin_dirs.append(plugin_dir)
            if not os.path.exists(plugin_dir):
                os.makedirs(plugin_dir, exist_ok=True)
            self.logger.info(f"添加插件目录: {plugin_dir}")
    
    def scan_plugins(self) -> List[str]:
        """
        扫描插件目录，查找可用插件
        
        Returns:
            List[str]: 插件名称列表
        """
        found_plugins = []
        
        for plugin_dir in self.plugin_dirs:
            if not os.path.exists(plugin_dir):
                continue
                
            for file_path in os.listdir(plugin_dir):
                if file_path.endswith('.py') and not file_path.startswith('_'):
                    plugin_name = file_path[:-3]  # 去掉.py后缀
                    found_plugins.append(plugin_name)
        
        return found_plugins
    
    def load_plugin(self, plugin_name: str) -> bool:
        """
        加载插件
        
        Args:
            plugin_name: 插件名称
            
        Returns:
            bool: 加载是否成功
        """
        with self._lock:
            # 检查是否已加载
            if plugin_name in self.plugins:
                self.logger.info(f"插件 {plugin_name} 已加载")
                return True
            
            # 查找插件文件
            plugin_file = None
            for plugin_dir in self.plugin_dirs:
                potential_file = os.path.join(plugin_dir, f"{plugin_name}.py")
                if os.path.exists(potential_file):
                    plugin_file = potential_file
                    break
            
            if not plugin_file:
                self.logger.error(f"找不到插件 {plugin_name}")
                return False
            
            try:
                # 动态导入插件模块
                spec = importlib.util.spec_from_file_location(plugin_name, plugin_file)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                
                # 查找插件类
                plugin_class = None
                for name, obj in inspect.getmembers(module):
                    if (inspect.isclass(obj) and 
                        issubclass(obj, ProtocolPlugin) and 
                        obj != ProtocolPlugin):
                        plugin_class = obj
                        break
                
                if not plugin_class:
                    self.logger.error(f"插件 {plugin_name} 中没有找到ProtocolPlugin子类")
                    return False
                
                # 创建插件实例
                plugin_instance = plugin_class()
                
                # 初始化插件
                if not plugin_instance.initialize():
                    self.logger.error(f"插件 {plugin_name} 初始化失败")
                    return False
                
                # 创建插件信息
                plugin_info = PluginInfo(
                    name=plugin_instance.plugin_name,
                    version=plugin_instance.plugin_version,
                    description=plugin_instance.plugin_description,
                    author=plugin_instance.plugin_author,
                    plugin_class=plugin_class,
                    file_path=plugin_file,
                    load_time=time.time()
                )
                
                # 存储插件
                self.plugins[plugin_name] = plugin_info
                self.enabled_plugins[plugin_name] = plugin_instance
                
                # 加载插件配置
                if plugin_name in self.plugin_configs:
                    config = self.plugin_configs[plugin_name]
                    plugin_instance.config = config.config
                    plugin_instance.enabled = config.enabled
                
                self.logger.info(f"插件 {plugin_name} 加载成功")
                return True
                
            except Exception as e:
                self.logger.error(f"加载插件 {plugin_name} 失败: {e}")
                return False
    
    def unload_plugin(self, plugin_name: str) -> bool:
        """
        卸载插件（安全调用 shutdown 释放资源，清理模块缓存）
        
        Args:
            plugin_name: 插件名称
            
        Returns:
            bool: 卸载是否成功
        """
        with self._lock:
            if plugin_name not in self.plugins:
                self.logger.warning(f"插件 {plugin_name} 未加载")
                return False
            
            try:
                # 先获取插件实例
                plugin_instance = None
                if plugin_name in self.enabled_plugins:
                    plugin_instance = self.enabled_plugins[plugin_name]
                elif plugin_name in self.plugin_configs:
                    pass  # 插件可能已禁用
                
                # 调用 shutdown 释放资源
                if plugin_instance is not None:
                    try:
                        plugin_instance.shutdown()
                    except Exception as e:
                        self.logger.warning(f"插件 {plugin_name} shutdown() 异常: {e}")
                
                # 清理缓存
                if plugin_name in self.enabled_plugins:
                    self.enabled_plugins[plugin_name].clear_cache()
                    del self.enabled_plugins[plugin_name]
                
                # 从插件字典中移除
                del self.plugins[plugin_name]
                
                # 彻底斩断 dynamic module 的缓存
                sys.modules.pop(plugin_name, None)
                
                self.logger.info(f"插件 {plugin_name} 卸载成功")
                return True
                
            except Exception as e:
                self.logger.error(f"卸载插件 {plugin_name} 失败: {e}")
                return False
    
    def enable_plugin(self, plugin_name: str) -> bool:
        """
        启用插件
        
        Args:
            plugin_name: 插件名称
            
        Returns:
            bool: 启用是否成功
        """
        with self._lock:
            if plugin_name not in self.plugins:
                self.logger.error(f"插件 {plugin_name} 未加载")
                return False
            
            if plugin_name not in self.enabled_plugins:
                try:
                    # 重新创建插件实例
                    plugin_info = self.plugins[plugin_name]
                    plugin_instance = plugin_info.plugin_class()
                    
                    # 初始化插件
                    if not plugin_instance.initialize():
                        self.logger.error(f"插件 {plugin_name} 初始化失败")
                        return False
                    
                    # 设置配置
                    if plugin_name in self.plugin_configs:
                        config = self.plugin_configs[plugin_name]
                        plugin_instance.config = config.config
                        plugin_instance.enabled = config.enabled
                    
                    self.enabled_plugins[plugin_name] = plugin_instance
                    plugin_info.enabled = True
                    
                    self.logger.info(f"插件 {plugin_name} 启用成功")
                    return True
                    
                except Exception as e:
                    self.logger.error(f"启用插件 {plugin_name} 失败: {e}")
                    return False
            
            return True
    
    def disable_plugin(self, plugin_name: str) -> bool:
        """
        禁用插件（调用 shutdown 释放资源）
        
        Args:
            plugin_name: 插件名称
            
        Returns:
            bool: 禁用是否成功
        """
        with self._lock:
            if plugin_name not in self.enabled_plugins:
                self.logger.warning(f"插件 {plugin_name} 未启用")
                return True
            
            try:
                # 获取插件实例
                plugin_instance = self.enabled_plugins[plugin_name]
                
                # 调用 shutdown 释放资源
                try:
                    plugin_instance.shutdown()
                except Exception as e:
                    self.logger.warning(f"插件 {plugin_name} shutdown() 异常: {e}")
                
                # 清理缓存
                plugin_instance.clear_cache()
                del self.enabled_plugins[plugin_name]
                
                # 更新插件信息
                if plugin_name in self.plugins:
                    self.plugins[plugin_name].enabled = False
                
                self.logger.info(f"插件 {plugin_name} 禁用成功")
                return True
                
            except Exception as e:
                self.logger.error(f"禁用插件 {plugin_name} 失败: {e}")
                return False
    
    def get_plugin_info(self, plugin_name: str) -> Optional[PluginInfo]:
        """
        获取插件信息
        
        Args:
            plugin_name: 插件名称
            
        Returns:
            Optional[PluginInfo]: 插件信息
        """
        return self.plugins.get(plugin_name)
    
    def get_enabled_plugins(self) -> Dict[str, ProtocolPlugin]:
        """
        获取已启用的插件
        
        Returns:
            Dict[str, ProtocolPlugin]: 已启用的插件
        """
        return self.enabled_plugins.copy()
    
    def parse_data_with_plugins(self, data: bytes, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        使用插件解析数据
        
        Args:
            data: 要解析的数据
            context: 解析上下文
            
        Returns:
            Dict[str, Any]: 解析结果
        """
        results = {}
        
        for plugin_name, plugin_instance in self.enabled_plugins.items():
            try:
                result = plugin_instance.parse_data(data, context)
                results[plugin_name] = result
            except Exception as e:
                self.logger.error(f"插件 {plugin_name} 解析数据失败: {e}")
                results[plugin_name] = {'error': str(e)}
        
        return results
    
    def parse_data_with_plugins_parallel(self, data: bytes, context: Dict[str, Any] = None, max_workers: int = None) -> Dict[str, Any]:
        """
        使用插件并行解析数据
        
        Args:
            data: 要解析的数据
            context: 解析上下文
            max_workers: 最大工作线程数，默认为CPU核心数
            
        Returns:
            Dict[str, Any]: 解析结果
        """
        if max_workers is None:
            max_workers = min(len(self.enabled_plugins), os.cpu_count() or 4)
        
        if not self.enabled_plugins:
            return {}
        
        results = {}
        
        # 使用线程池并行处理插件
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 创建任务
            future_to_plugin = {
                executor.submit(self._safe_plugin_parse, plugin_name, plugin_instance, data, context): plugin_name
                for plugin_name, plugin_instance in self.enabled_plugins.items()
            }
            
            # 收集结果
            for future in as_completed(future_to_plugin):
                plugin_name = future_to_plugin[future]
                try:
                    result = future.result(timeout=5.0)
                    results[plugin_name] = result
                except Exception as e:
                    self.logger.error(f"插件 {plugin_name} 并行解析失败: {e}")
                    results[plugin_name] = {'error': str(e)}
        
        return results
    
    def _safe_plugin_parse(self, plugin_name: str, plugin_instance: ProtocolPlugin, data: bytes, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        安全的插件解析方法
        
        Args:
            plugin_name: 插件名称
            plugin_instance: 插件实例
            data: 要解析的数据
            context: 解析上下文
            
        Returns:
            Dict[str, Any]: 解析结果
        """
        try:
            return plugin_instance.parse_data(data, context)
        except Exception as e:
            self.logger.error(f"插件 {plugin_name} 解析数据失败: {e}")
            return {'error': str(e)}
    
    def batch_parse_data(self, data_list: List[bytes], context: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        批量数据解析
        
        Args:
            data_list: 数据列表
            context: 解析上下文
            
        Returns:
            List[Dict[str, Any]]: 解析结果列表
        """
        results = []
        
        for data in data_list:
            result = self.parse_data_with_plugins(data, context)
            results.append(result)
        
        return results
    
    def batch_parse_data_parallel(self, data_list: List[bytes], context: Dict[str, Any] = None, max_workers: int = None) -> List[Dict[str, Any]]:
        """
        批量并行数据解析
        
        Args:
            data_list: 数据列表
            context: 解析上下文
            max_workers: 最大工作线程数
            
        Returns:
            List[Dict[str, Any]]: 解析结果列表
        """
        if max_workers is None:
            max_workers = min(len(data_list), os.cpu_count() or 4)
        
        if not data_list:
            return []
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 创建任务
            futures = [
                executor.submit(self.parse_data_with_plugins, data, context)
                for data in data_list
            ]
            
            # 收集结果
            results = []
            for future in futures:
                try:
                    result = future.result(timeout=10.0)
                    results.append(result)
                except Exception as e:
                    self.logger.error(f"批量解析失败: {e}")
                    results.append({'error': str(e)})
        
        return results
    
    def encode_data_with_plugins(self, data: Dict[str, Any], context: Dict[str, Any] = None) -> bytes:
        """
        使用插件编码数据
        
        Args:
            data: 要编码的数据
            context: 编码上下文
            
        Returns:
            bytes: 编码后的数据
        """
        # 按优先级排序
        sorted_plugins = sorted(self.enabled_plugins.items(), 
                              key=lambda x: self.plugin_configs.get(x[0], PluginConfig(x[0])).priority)
        
        for plugin_name, plugin_instance in sorted_plugins:
            try:
                if plugin_name in data:
                    encoded_data = plugin_instance.encode_data(data[plugin_name], context)
                    return encoded_data
            except Exception as e:
                self.logger.error(f"插件 {plugin_name} 编码数据失败: {e}")
        
        return b""
    
    def register_plugin_config(self, plugin_name: str, config: PluginConfig) -> None:
        """
        注册插件配置
        
        Args:
            plugin_name: 插件名称
            config: 插件配置
        """
        self.plugin_configs[plugin_name] = config
        
        # 如果插件已加载，更新配置
        if plugin_name in self.enabled_plugins:
            plugin_instance = self.enabled_plugins[plugin_name]
            plugin_instance.config = config.config
            plugin_instance.enabled = config.enabled
    
    def get_plugin_config(self, plugin_name: str) -> Optional[PluginConfig]:
        """
        获取插件配置
        
        Args:
            plugin_name: 插件名称
            
        Returns:
            Optional[PluginConfig]: 插件配置
        """
        return self.plugin_configs.get(plugin_name)
    
    def save_plugin_configs(self, file_path: str) -> bool:
        """
        保存插件配置到文件
        
        Args:
            file_path: 配置文件路径
            
        Returns:
            bool: 保存是否成功
        """
        try:
            configs_data = {}
            for plugin_name, config in self.plugin_configs.items():
                configs_data[plugin_name] = {
                    'config': config.config,
                    'enabled': config.enabled,
                    'auto_load': config.auto_load,
                    'priority': config.priority
                }
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(configs_data, f, indent=2, ensure_ascii=False)
            
            self.logger.info(f"插件配置已保存到: {file_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"保存插件配置失败: {e}")
            return False
    
    def load_plugin_configs(self, file_path: str) -> bool:
        """
        从文件加载插件配置
        
        Args:
            file_path: 配置文件路径
            
        Returns:
            bool: 加载是否成功
        """
        try:
            if not os.path.exists(file_path):
                self.logger.warning(f"配置文件不存在: {file_path}")
                return False
            
            with open(file_path, 'r', encoding='utf-8') as f:
                configs_data = json.load(f)
            
            for plugin_name, config_data in configs_data.items():
                config = PluginConfig(
                    plugin_name=plugin_name,
                    config=config_data.get('config', {}),
                    enabled=config_data.get('enabled', True),
                    auto_load=config_data.get('auto_load', True),
                    priority=config_data.get('priority', 0)
                )
                self.plugin_configs[plugin_name] = config
            
            self.logger.info(f"插件配置已从 {file_path} 加载")
            return True
            
        except Exception as e:
            self.logger.error(f"加载插件配置失败: {e}")
            return False
    
    def get_plugin_cache_key(self, plugin_name: str, data: bytes) -> str:
        """
        生成插件缓存键
        
        Args:
            plugin_name: 插件名称
            data: 数据
            
        Returns:
            str: 缓存键
        """
        data_hash = hashlib.md5(data).hexdigest()
        return f"{plugin_name}_{data_hash}"
    
    def get_cache(self, key: str) -> Optional[Any]:
        """
        从 LRU 缓存获取值
        
        Args:
            key: 缓存键
            
        Returns:
            Optional[Any]: 缓存值，如果不存在或已过期返回 None
        """
        if key not in self._plugin_cache:
            return None
        
        value, timestamp = self._plugin_cache[key]
        
        # 检查缓存是否过期
        if time.time() - timestamp > self._cache_ttl:
            del self._plugin_cache[key]
            return None
        
        # LRU: 将访问的键移到末尾（标记为最近使用）
        self._plugin_cache.move_to_end(key)
        return value
    
    def set_cache(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """
        设置 LRU 缓存值
        
        Args:
            key: 缓存键
            value: 缓存值
            ttl: 缓存过期时间（秒），为 None 使用默认值
        """
        # 如果键已存在，先删除再重新插入
        if key in self._plugin_cache:
            del self._plugin_cache[key]
        
        # 检查缓存大小限制
        if len(self._plugin_cache) >= self._cache_size_limit:
            self._evict_cache()
        
        self._plugin_cache[key] = (value, time.time())
    
    def _evict_cache(self) -> None:
        """淘汰缓存：删除最久未使用的缓存项"""
        # 删除最早的键（OrderedDict 第一个元素是最久未使用的）
        if self._plugin_cache:
            # 先清理过期缓存
            self.cleanup_expired_cache()
            
            # 如果仍超过限制，删除最早的
            if len(self._plugin_cache) >= self._cache_size_limit:
                oldest_key = next(iter(self._plugin_cache))
                del self._plugin_cache[oldest_key]
                self.logger.debug(f"LRU 缓存淘汰: {oldest_key}")
    
    def cleanup_expired_cache(self) -> None:
        """清理过期缓存"""
        current_time = time.time()
        expired_keys = []
        
        for key, (value, timestamp) in self._plugin_cache.items():
            if current_time - timestamp > self._cache_ttl:
                expired_keys.append(key)
        
        for key in expired_keys:
            del self._plugin_cache[key]
        
        if expired_keys:
            self.logger.debug(f"清理 {len(expired_keys)} 个过期缓存项")
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """
        获取缓存统计信息
        
        Returns:
            Dict[str, Any]: 缓存统计信息
        """
        return {
            'current_size': len(self._plugin_cache),
            'max_size': self._cache_size_limit,
            'usage_percent': (len(self._plugin_cache) / self._cache_size_limit) * 100 if self._cache_size_limit > 0 else 0,
            'ttl': self._cache_ttl
        }
    
    def clear_plugin_cache(self, plugin_name: Optional[str] = None) -> None:
        """
        清理插件缓存
        
        Args:
            plugin_name: 插件名称，如果为None则清理所有缓存
        """
        if plugin_name:
            if plugin_name in self.enabled_plugins:
                self.enabled_plugins[plugin_name].clear_cache()
        else:
            for plugin_instance in self.enabled_plugins.values():
                plugin_instance.clear_cache()
    
    def get_plugin_stats(self) -> Dict[str, Any]:
        """
        获取插件统计信息
        
        Returns:
            Dict[str, Any]: 插件统计信息
        """
        total_plugins = len(self.plugins)
        enabled_plugins = len(self.enabled_plugins)
        
        # 计算缓存使用情况
        cache_sizes = {}
        for plugin_name, plugin_instance in self.enabled_plugins.items():
            cache_size = len(plugin_instance._cache)
            cache_sizes[plugin_name] = cache_size
        
        return {
            'total_plugins': total_plugins,
            'enabled_plugins': enabled_plugins,
            'disabled_plugins': total_plugins - enabled_plugins,
            'plugin_dirs': self.plugin_dirs,
            'cache_sizes': cache_sizes,
            'total_cache_items': sum(cache_sizes.values())
        }
    
    def shutdown(self) -> None:
        """
        关闭插件管理器
        """
        self.logger.info("开始关闭插件管理器...")
        
        # 卸载所有插件
        for plugin_name in list(self.enabled_plugins.keys()):
            self.disable_plugin(plugin_name)
        
        # 清理缓存
        self._plugin_cache.clear()
        
        self.logger.info("插件管理器已关闭")
    
    def __del__(self):
        """析构函数"""
        self.shutdown()