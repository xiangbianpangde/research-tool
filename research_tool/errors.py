"""统一异常体系。所有 research_tool 抛出的错误继承自 ResearchToolError。

设计依据：03-Python库接口设计.md §7「错误使用异常传播（自定义 ResearchToolError 基类）」。
"""

from __future__ import annotations


class ResearchToolError(Exception):
    """所有本工具异常的基类。"""


class ConfigValidationError(ResearchToolError):
    """配置加载/校验失败。附带详细错误信息与修复建议。

    依据：04-配置结构设计.md §5。
    """


class StageError(ResearchToolError):
    """某个 Stage 执行失败。"""

    def __init__(self, stage: str, message: str) -> None:
        self.stage = stage
        super().__init__(f"[{stage}] {message}")


class LLMError(ResearchToolError):
    """LLM 调用失败。"""


class SearchError(ResearchToolError):
    """搜索后端失败。"""


class CollectError(StageError):
    """采集阶段失败。"""

    def __init__(self, message: str) -> None:
        super().__init__("collect", message)
