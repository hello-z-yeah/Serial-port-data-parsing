"""Application-specific exception hierarchy."""

from __future__ import annotations
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

class ProtocolParserError(Exception):
    """基础协议解析异常"""
    
    def __init__(self, message: str, context: Dict[str, Any] = None, 
                 error_code: int = 0, severity: str = "error"):
        super().__init__(message)
        self.context = context or {}
        self.error_code = error_code
        self.severity = severity
        self.timestamp = datetime.now()
        self.traceback = traceback.format_exc()
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式，便于日志记录"""
        return {
            "timestamp": self.timestamp.isoformat(),
            "error_type": type(self).__name__,
            "error_message": str(self),
            "error_code": self.error_code,
            "severity": self.severity,
            "context": self.context,
            "traceback": self.traceback
        }


class FrameParsingError(ProtocolParserError):
    """帧解析异常"""
    def __init__(self, message: str, frame_data: bytes = None, 
                 protocol: str = None, **kwargs):
        context = kwargs.get("context", {})
        context.update({
            "frame_data": frame_data.hex() if frame_data else None,
            "protocol": protocol
        })
        super().__init__(message, context, severity="error", **kwargs)


class ConfigurationError(ProtocolParserError):
    """配置错误"""
    def __init__(self, message: str, config_path: str = None, **kwargs):
        context = kwargs.get("context", {})
        context.update({"config_path": config_path})
        super().__init__(message, context, severity="error", **kwargs)


class InputValidationError(ProtocolParserError):
    """输入验证错误"""
    def __init__(self, message: str, input_value: Any = None, **kwargs):
        context = kwargs.get("context", {})
        context.update({"input_value": str(input_value)})
        super().__init__(message, context, severity="warning", **kwargs)


class ResourceError(ProtocolParserError):
    """资源访问错误"""
    def __init__(self, message: str, resource_type: str = None, 
                 resource_name: str = None, **kwargs):
        context = kwargs.get("context", {})
        context.update({
            "resource_type": resource_type,
            "resource_name": resource_name
        })
        super().__init__(message, context, severity="error", **kwargs)


# 保持向后兼容性
class AppError(ProtocolParserError):
    """Base class for application-defined failures."""
    pass

class UserCorrectableError(AppError):
    """A condition the user can correct without changing program code."""
    pass

class ValidationError(UserCorrectableError, ValueError):
    """Invalid value or malformed user-supplied content."""
    pass

class AttributeValidationError(ValidationError):
    """Attribute value, range, enum, length, or permission violation."""
    pass

class ProductConfigError(ValidationError):
    """Invalid imported product or protocol configuration."""
    pass

class CommandValidationError(ValidationError):
    """Invalid command payload or command-library entry."""
    pass

class EnvironmentStateError(UserCorrectableError, RuntimeError):
    """The current console/runtime environment cannot perform an operation."""
    pass

class SerialOperationError(UserCorrectableError, OSError):
    """Serial port or driver operation failed."""
    pass

class SerialStateError(UserCorrectableError, RuntimeError):
    """Operation requested while the serial connection is unavailable."""
    pass

class TxQueueFullError(SerialOperationError):
    """The asynchronous transmit queue cannot accept more frames."""
    pass

class StorageOperationError(UserCorrectableError, OSError):
    """Raw data/log/session storage failed."""
    pass

class StorageQueueFullError(StorageOperationError):
    """Raw-data writer queue is full and records had to be dropped."""
    pass

class SnapshotError(StorageOperationError):
    """Session snapshot could not be saved or loaded safely."""
    pass


class PluginError(ProtocolParserError):
    """插件相关错误"""
    def __init__(self, message: str, plugin_name: str = None, **kwargs):
        context = kwargs.get("context", {})
        context.update({"plugin_name": plugin_name})
        super().__init__(message, context, severity="error", **kwargs)


class ConnectionError(ProtocolParserError):
    """连接相关错误"""
    def __init__(self, message: str, port: str = None, **kwargs):
        context = kwargs.get("context", {})
        context.update({"port": port})
        super().__init__(message, context, severity="error", **kwargs)


def log_protocol_error(exc: Exception, context: str = "") -> Path:
    """统一记录协议错误到日志文件
    
    参数:
        exc: 异常对象
        context: 错误上下文信息
        
    返回:
        日志文件路径
    """
    from .paths import write_crash_log
    
    # 确保异常有足够的上下文信息
    if not hasattr(exc, 'to_dict'):
        # 为普通异常添加上下文
        exc_context = {
            "type": type(exc).__name__,
            "message": str(exc),
            "context": context,
            "traceback": traceback.format_exc()
        }
        exc.to_dict = lambda: exc_context
    
    # 记录到日志
    log_path = write_crash_log(exc)
    
    # 打印用户友好的错误信息
    severity = getattr(exc, 'severity', 'error')
    error_code = getattr(exc, 'error_code', 0)
    
    if severity == "error":
        print(f"[错误] {context}: {exc} (错误码: {error_code})")
    elif severity == "warning":
        print(f"[警告] {context}: {exc} (错误码: {error_code})")
    else:
        print(f"[信息] {context}: {exc} (错误码: {error_code})")
    
    if log_path:
        print(f"       详情已写入: {log_path}")
    
    return log_path


def classify_protocol_error(exc: Exception) -> tuple[str, str]:
    """分类协议错误，返回友好消息和调试信息
    
    参数:
        exc: 异常对象
        
    返回:
        (friendly_message, debug_info)
    """
    error_type = type(exc).__name__
    message = str(exc)
    
    # 根据异常类型提供不同的友好消息
    if isinstance(exc, ProtocolParserError):
        friendly = exc.__class__.__doc__ or message
        debug = exc.to_dict()
    elif isinstance(exc, (OSError, RuntimeError)):
        friendly = f"系统错误: {message}"
        debug = {"error_type": error_type, "message": message}
    elif isinstance(exc, ValueError):
        friendly = f"参数错误: {message}"
        debug = {"error_type": error_type, "message": message}
    else:
        friendly = f"未知错误: {message}"
        debug = {"error_type": error_type, "message": message}
    
    return friendly, str(debug)


def handle_protocol_error(exc: Exception, context: str = "") -> int:
    """统一处理协议错误，返回退出码
    
    参数:
        exc: 异常对象
        context: 错误上下文
        
    返回:
        退出码 (0=成功, 1=未知错误, 2=可纠正错误)
    """
    log_protocol_error(exc, context)
    
    if isinstance(exc, UserCorrectableError):
        return 2  # 用户可纠正错误
    elif isinstance(exc, ProtocolParserError):
        return 2  # 协议特定错误
    else:
        return 1  # 未知错误