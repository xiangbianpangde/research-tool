"""
[文件路径] research_tool/tests/test_pipeline_adapter.py
[文件职责] M-008 管道适配器单元测试（落盘 / 管道触发 / tags 合并 / Collect 配置注入）
[所属模块] M-008（来自 DD-001）
[关联设计规范] FS-008 / MD-008 / IC-022 / IC-023 / IC-024 + CS-001 测试规范
[功能描述]
  功能1: 覆盖 4 个子模块的核心 / 边界 / 异常场景
  功能2: 覆盖率目标：行 ≥ 80% / 分支 ≥ 70%（MD-008 测试策略）
  功能3: Mock 策略：subprocess.run 用 unittest.mock.patch 替换；文件 I/O 用 pytest tmp_path
[输入输出]
  输入: pytest fixtures（tests/fixtures/sample_markdown.md, sample_tags.json）
  输出: pytest 测试报告 + 覆盖率 XML
[依赖关系]
  依赖文件: research_tool.pipeline_adapter（M-008，被测对象）
  被依赖文件: 无（测试入口）
[注意事项]
  注意1: 集成测试标记 @pytest.mark.integration，pytest.ini 默认 deselect
  注意2: subprocess mock 必须在 M-008.PipelineTrigger 之前注入，避免真实管道触发
  注意3: 磁盘剩余空间测试必须 mock shutil.disk_usage，否则 CI 环境可能误判
[代码风格] 遵循 CS-001（Python 测试规范，pytest）
[创建日期] 2026-06-01
[修改历史]
  2026-06-01: DD-M-008 - 初始测试文件框架（测试场景注释 + 函数签名）
[作者] DD-M-008-20260601
[来源标注] [DD-001:FS-008/MD-008 测试策略] [DD-001:CS-001 测试规范]
"""

# 1. 标准库
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

# 2. 第三方
import pytest

# 3. 本地
from research_tool.pipeline_adapter import (
    MarkdownWriter,
    PipelineTrigger,
    TagsMerger,
    CollectConfigInjector,
    StagesResult,
    write_markdown,
    trigger_pipeline,
    merge_tags,
    inject_collect_config,
    RAW_OUTPUT_ROOT,
    FILE_PERMS_MARKDOWN,
    DISK_FREE_MIN_BYTES,
    PIPELINE_MAX_RETRY,
    COLLECT_CONFIG_VERSION,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def sample_markdown() -> str:
    """[Fixture] sample_markdown —— 来自 tests/fixtures/sample_markdown.md 的样例 Markdown。"""
    fixture_path = Path("research_tool/tests/fixtures/sample_markdown.md")
    return fixture_path.read_text(encoding="utf-8")


@pytest.fixture
def sample_tags() -> list[str]:
    """[Fixture] sample_tags —— 来自 tests/fixtures/sample_tags.json 的样例 tags。"""
    fixture_path = Path("research_tool/tests/fixtures/sample_tags.json")
    return json.loads(fixture_path.read_text(encoding="utf-8"))


@pytest.fixture
def tmp_raw_dir(tmp_path: Path) -> Path:
    """[Fixture] tmp_raw_dir —— 临时 raw/ 目录，模拟 FS-002 部署拓扑。"""
    raw_dir = tmp_path / "raw" / "general"
    raw_dir.mkdir(parents=True, exist_ok=True)
    return raw_dir


# =============================================================================
# M-008.1 MarkdownWriter 落盘测试（核心 3 + 边界 2 + 异常 2 = 7 用例）
# =============================================================================

class TestMarkdownWriter:
    """[测试类] MarkdownWriter 落盘测试

    [职责] 覆盖 IC-022 落盘契约：磁盘检查 / 写文件 / 权限设置 / 错误码登记。
    """

    def test_write_normal_creates_file_with_correct_perms(
        self, sample_markdown: str, tmp_raw_dir: Path
    ) -> None:
        """[测试场景1: 正常落盘] 写 Markdown 到 raw/<topic>/<video-id>.md，权限 0o644。"""
        pass  # 业务测试代码由开发工程师填充

    def test_write_returns_absolute_path(
        self, sample_markdown: str, tmp_raw_dir: Path
    ) -> None:
        """[测试场景2: 返回绝对路径] write_markdown 返回的 file_path 是绝对路径。"""
        pass  # 业务测试代码由开发工程师填充

    def test_ensure_dir_creates_nested_dirs(self, tmp_raw_dir: Path) -> None:
        """[测试场景3: 目录创建] ensure_dir 递归创建 raw/<topic>/ 目录。"""
        pass  # 业务测试代码由开发工程师填充

    def test_write_overwrites_existing_file(
        self, sample_markdown: str, tmp_raw_dir: Path
    ) -> None:
        """[测试场景4: 边界-覆盖写入] 重复调用 write_markdown 覆盖既有文件（幂等性）。"""
        pass  # 业务测试代码由开发工程师填充

    def test_write_with_empty_markdown_creates_empty_file(
        self, tmp_raw_dir: Path
    ) -> None:
        """[测试场景5: 边界-空 Markdown] 空字符串也能成功落盘（0 字节文件）。"""
        pass  # 业务测试代码由开发工程师填充

    @patch("research_tool.pipeline_adapter.shutil.disk_usage")
    def test_write_raises_when_disk_full(
        self, mock_disk_usage: MagicMock, sample_markdown: str
    ) -> None:
        """[测试场景6: 异常-磁盘满] 磁盘剩余 < 100MB 时抛出 E_PIPE_DISK_FULL。"""
        mock_disk_usage.return_value = MagicMock(free=50 * 1024 * 1024)  # 50MB
        # 业务测试代码由开发工程师填充

    @patch("research_tool.pipeline_adapter.open", side_effect=PermissionError("denied"))
    def test_write_raises_after_retry(self, mock_open: MagicMock) -> None:
        """[测试场景7: 异常-权限拒绝 + 重试] PermissionError 重试 1 次后仍失败 → E_PIPE_001。"""
        # 业务测试代码由开发工程师填充
        pass  # 占位


# =============================================================================
# M-008.2 PipelineTrigger 管道触发测试（核心 2 + 边界 1 + 异常 1 = 4 用例）
# =============================================================================

class TestPipelineTrigger:
    """[测试类] PipelineTrigger 5 阶段管道触发测试

    [职责] 覆盖 IC-023 管道触发契约：subprocess 调用 / 阶段解析 / 重试策略。
    """

    @patch("research_tool.pipeline_adapter.subprocess.run")
    def test_trigger_normal_executes_pipeline(
        self, mock_run: MagicMock, tmp_raw_dir: Path
    ) -> None:
        """[测试场景1: 正常触发] subprocess.run exit=0，返回 StagesResult(stages_run=[...], success=True)。"""
        mock_run.return_value = MagicMock(returncode=0, stdout="stage1 stage2 stage3 stage4 stage5")
        # 业务测试代码由开发工程师填充
        pass  # 占位

    @patch("research_tool.pipeline_adapter.subprocess.run")
    def test_trigger_parses_stages_from_stdout(
        self, mock_run: MagicMock
    ) -> None:
        """[测试场景2: 阶段解析] parse_stages 从 stdout 正确提取阶段列表。"""
        mock_run.return_value = MagicMock(returncode=0, stdout="ingest analyze index notify cleanup")
        # 业务测试代码由开发工程师填充
        pass  # 占位

    @patch("research_tool.pipeline_adapter.subprocess.run")
    def test_trigger_retry_on_first_failure(
        self, mock_run: MagicMock
    ) -> None:
        """[测试场景3: 边界-重试] 第 1 次失败后重试 1 次，第 2 次成功 → retried=True。"""
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout=""),  # 第 1 次失败
            MagicMock(returncode=0, stdout="stage1"),  # 第 2 次成功
        ]
        # 业务测试代码由开发工程师填充
        pass  # 占位

    @patch("research_tool.pipeline_adapter.subprocess.run")
    def test_trigger_raises_after_max_retry(
        self, mock_run: MagicMock
    ) -> None:
        """[测试场景4: 异常-重试仍失败] PIPELINE_MAX_RETRY (1) 次后仍失败 → E_PIPE_001。"""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        # 业务测试代码由开发工程师填充
        pass  # 占位


# =============================================================================
# M-008.3 TagsMerger tags 合并测试（核心 2 + 边界 1 + 异常 0 = 3 用例）
# =============================================================================

class TestTagsMerger:
    """[测试类] TagsMerger tags 合并测试

    [职责] 覆盖 IC-024 tags 合并契约：并集 + 去重 + 冲突保留两版。
    """

    def test_merge_creates_union(self) -> None:
        """[测试场景1: 正常合并] new=["a","b"] + existing=["b","c"] → ["a","b","c"]。"""
        # 业务测试代码由开发工程师填充
        pass  # 占位

    def test_merge_is_case_insensitive(self) -> None:
        """[测试场景2: 边界-大小写不敏感] "AI" 和 "ai" 视为同一 tag（默认 case_sensitive=False）。"""
        # 业务测试代码由开发工程师填充
        pass  # 占位

    def test_merge_preserves_first_occurrence_order(self) -> None:
        """[测试场景3: 边界-顺序保持] 合并结果保持首次出现顺序。"""
        # 业务测试代码由开发工程师填充
        pass  # 占位


# =============================================================================
# M-008.4 CollectConfigInjector 配置注入测试（核心 1 + 边界 0 + 异常 0 = 1 用例）
# =============================================================================

class TestCollectConfigInjector:
    """[测试类] CollectConfigInjector 配置注入测试

    [职责] 覆盖 CE-009 Collect 配置契约：版本校验 + 注入成功路径。
    """

    def test_inject_verifies_config_version(self, tmp_path: Path) -> None:
        """[测试场景1: 正常注入] Collect 配置 schema 版本 = COLLECT_CONFIG_VERSION → inject()=True。"""
        config_file = tmp_path / "collect.toml"
        config_file.write_text(f'version = "{COLLECT_CONFIG_VERSION}"', encoding="utf-8")
        # 业务测试代码由开发工程师填充
        pass  # 占位


# =============================================================================
# 模块边界守护测试（D7=100 验证）
# =============================================================================

class TestModuleBoundaryCompliance:
    """[测试类] 模块边界合规测试

    [职责] 验证 M-008 文件不依赖 / 不调用其他模块的业务实现（仅依赖允许的 import）。
    """

    def test_m008_does_not_import_other_modules_business(self) -> None:
        """[测试场景1: 模块边界] 验证 M-008 不直接 import M-001/M-002/M-003/M-004/M-005/M-006/M-007/M-009 的业务实现。"""
        # 业务测试代码由开发工程师填充：解析 pipeline_adapter.py 的 import 列表，断言仅含允许的依赖
        pass  # 占位
