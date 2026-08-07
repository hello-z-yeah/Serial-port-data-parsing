"""User-facing exception classification for GUI operations."""
from __future__ import annotations

import json
from dataclasses import dataclass

from .exceptions import UserCorrectableError
from .parser import ProtocolError, classify_protocol_error


@dataclass(frozen=True)
class UserErrorPresentation:
    title: str
    message: str


def is_expected_user_error(exc: BaseException) -> bool:
    """Only explicit domain/validation exceptions are normal prompts.

    Built-in ``ValueError``, ``TypeError`` and generic ``OSError`` are no longer
    broadly swallowed.  Unexpected defects therefore retain their traceback and
    reach ``error.log``.
    """
    return isinstance(
        exc,
        (
            UserCorrectableError,
            ProtocolError,
            json.JSONDecodeError,
            FileNotFoundError,
            PermissionError,
            IsADirectoryError,
            NotADirectoryError,
        ),
    )


def prompt_title_for_context(context_title: str) -> str:
    title = str(context_title or "操作提示").strip() or "操作提示"
    exact = {
        "产品JSON导入失败": "产品JSON内容不符合要求",
        "产品JSON修改失败": "产品JSON内容不符合要求",
        "协议加载失败": "协议内容不符合要求",
        "导入失败": "导入内容不符合要求",
        "读取当前产品失败": "产品文件读取提示",
        "读取当前协议失败": "协议文件读取提示",
        "保存协议失败": "协议保存提示",
        "删除协议失败": "协议删除提示",
        "删除产品JSON失败": "产品删除提示",
        "串口打开失败": "串口连接提示",
        "发送失败": "发送提示",
        "指令库发送失败": "指令库发送提示",
        "属性发送失败": "属性值不符合要求",
        "批量上报失败": "批量上报内容不符合要求",
        "预置命令发送失败": "预置命令提示",
        "上电流程发送失败": "上电流程提示",
        "原始数据保存失败": "原始数据保存提示",
        "日志保存失败": "日志保存提示",
        "启动新串口窗口失败": "新串口窗口启动提示",
    }
    if title in exact:
        return exact[title]
    if title.endswith("失败"):
        return title[:-2] + "提示"
    if "错误" in title:
        return title.replace("错误", "提示")
    return title


def format_expected_user_error(exc: BaseException, *, fallback: str = "当前操作未完成，请检查输入后重试。") -> str:
    if isinstance(exc, json.JSONDecodeError):
        return (
            "JSON 格式不正确。\n"
            f"位置：第 {exc.lineno} 行，第 {exc.colno} 列\n"
            f"原因：{exc.msg}\n\n请修改 JSON 后重试。"
        )
    if isinstance(exc, ProtocolError):
        friendly, debug = classify_protocol_error(exc)
        message = friendly or fallback
        if debug and debug != friendly:
            message += f"\n\n原因：{debug}"
        return message
    text = str(exc).strip()
    if isinstance(exc, FileNotFoundError):
        return f"找不到所需文件或目录。\n\n{text or fallback}"
    if isinstance(exc, PermissionError):
        return f"当前路径没有访问权限。\n\n{text or fallback}\n\n请更换目录或检查文件权限后重试。"
    if isinstance(exc, IsADirectoryError):
        return f"当前选择的是目录，不是文件。\n\n{text or fallback}"
    if isinstance(exc, NotADirectoryError):
        return f"路径中的某一级不是有效目录。\n\n{text or fallback}"
    return text or fallback


def build_user_error_presentation(context_title: str, exc: BaseException) -> UserErrorPresentation | None:
    if not is_expected_user_error(exc):
        return None
    return UserErrorPresentation(
        title=prompt_title_for_context(context_title),
        message=format_expected_user_error(exc),
    )
