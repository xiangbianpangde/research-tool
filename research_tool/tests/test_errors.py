"""M-010 错误处理器单元测试。"""

from __future__ import annotations

import ast

import pytest

from research_tool.domain.errors import (
    CacheError,
    ConfigError,
    DownloadError,
    ErrorCode,
    ErrorInfo,
    ErrorRecord,
    FFmpegError,
    PreflightError,
    TranscribeError,
    VideoIngestError,
    format_error,
    lookup_code,
    register_error,
    resolve_exit_code,
)


class TestErrorCodeEnum:
    """26 个错误码常量。"""

    def test_all_26_codes_present(self):
        assert len(list(ErrorCode)) == 26

    def test_code_string_values(self):
        assert ErrorCode.E_VID_001_VIDEO_NOT_FOUND.value == "E_VID_001_VIDEO_NOT_FOUND"
        assert ErrorCode.E_VID_003_PIPELINE_FAIL.value == "E_VID_003_PIPELINE_FAIL"
        assert ErrorCode.E_DL_001_NETWORK_TIMEOUT.value == "E_DL_001_NETWORK_TIMEOUT"
        assert ErrorCode.E_LLM_001_LLM_CALL_FAILED.value == "E_LLM_001_LLM_CALL_FAILED"
        assert ErrorCode.E_SYS_001_UNKNOWN_ERROR_CODE.value == "E_SYS_001_UNKNOWN_ERROR_CODE"
        # R10 新增（13 个遗留 raise 站点错误码）
        assert ErrorCode.E_VID_URL_REJECTED.value == "E_VID_URL_REJECTED"
        assert ErrorCode.E_DL_002_VERSION_TOO_OLD.value == "E_DL_002_VERSION_TOO_OLD"
        assert ErrorCode.E_DL_BILI_403.value == "E_DL_BILI_403"
        assert ErrorCode.E_DL_LOCAL_001.value == "E_DL_LOCAL_001"
        assert ErrorCode.E_DL_LOCAL_002.value == "E_DL_LOCAL_002"
        assert ErrorCode.E_TR_001.value == "E_TR_001"
        assert ErrorCode.E_TR_003_GROQ_FAILED.value == "E_TR_003_GROQ_FAILED"
        assert ErrorCode.E_TR_004_TIMEOUT.value == "E_TR_004_TIMEOUT"
        assert ErrorCode.E_PIPE_001.value == "E_PIPE_001"
        assert ErrorCode.E_PIPE_DISK_FULL.value == "E_PIPE_DISK_FULL"
        assert ErrorCode.E_PIPE_CONFIG_MISMATCH.value == "E_PIPE_CONFIG_MISMATCH"
        assert ErrorCode.E_LIM_001.value == "E_LIM_001"
        assert ErrorCode.E_LIM_002.value == "E_LIM_002"

    def test_codes_unique(self):
        values = [c.value for c in ErrorCode]
        assert len(values) == len(set(values))

    def test_e_vid_003_registered(self):
        """E_VID_003_PIPELINE_FAIL 已注册（R9：原 cli.py:475 E_VID_PIPELINE_FAIL）。"""
        info = lookup_code(ErrorCode.E_VID_003_PIPELINE_FAIL.value)
        assert info.exit_code_hint == 500
        assert info.category == "VID"
        assert "视频管道处理失败" in info.default_scene


# R10 新增：13 个遗留 raise 站点错误码注册后的 exit_code_hint 期望。
_R10_NEW_CODE_HINTS = {
    ErrorCode.E_VID_URL_REJECTED.value: 400,
    ErrorCode.E_DL_002_VERSION_TOO_OLD.value: 403,
    ErrorCode.E_DL_BILI_403.value: 403,
    ErrorCode.E_DL_LOCAL_001.value: 400,
    ErrorCode.E_DL_LOCAL_002.value: 400,
    ErrorCode.E_TR_001.value: 500,
    ErrorCode.E_TR_003_GROQ_FAILED.value: 500,
    ErrorCode.E_TR_004_TIMEOUT.value: 500,
    ErrorCode.E_PIPE_001.value: 500,
    ErrorCode.E_PIPE_DISK_FULL.value: 500,
    ErrorCode.E_PIPE_CONFIG_MISMATCH.value: 401,
    ErrorCode.E_LIM_001.value: 400,
    ErrorCode.E_LIM_002.value: 400,
}


class TestR10RegisteredCodes:
    """R10：13 个遗留 raise 站点错误码注册后，lookup_code 全部可解析 + 仲裁退出码正确。"""

    @pytest.mark.parametrize("code,hint", list(_R10_NEW_CODE_HINTS.items()))
    def test_new_code_registered(self, code, hint):
        info = lookup_code(code)
        assert info.code == code
        assert info.exit_code_hint == hint
        assert info.default_scene
        assert info.default_cause
        assert info.default_suggestion

    @pytest.mark.parametrize("code,hint", list(_R10_NEW_CODE_HINTS.items()))
    def test_new_code_resolve_exit_code(self, code, hint):
        rec = register_error(code)
        assert resolve_exit_code([rec]) == hint


class TestRaiseSitesRegistered:
    """R10 验收测试：每个 raise <X>Error(code, ...) 的 code 必须在 _ERROR_REGISTRY。

    用 AST 解析生产模块，提取所有 raise 站点的错误码（常量名或 ErrorCode.E_X.value），
    解析为字符串后断言 lookup_code 成功——确保 M-010 不再是装饰性的（无 ConfigError 兜底）。
    """

    _MODULES = [
        "presentation/cli.py",
        "application/video_pipeline.py",
        "application/video_concurrent_orchestrator.py",
        "infrastructure/ingest/downloader.py",
        "infrastructure/ingest/transcriber.py",
        "infrastructure/ingest/ffmpeg_wrapper.py",
        "infrastructure/ingest/pipeline_adapter.py",
    ]
    _ERROR_CLASSES = {
        "VideoIngestError",
        "DownloadError",
        "TranscribeError",
        "FFmpegError",
        "CacheError",
        "PreflightError",
        "ConfigError",
    }

    def test_every_raise_code_is_registered(self):
        import ast
        import importlib
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent  # research_tool/
        codes: set[str] = set()
        for rel in self._MODULES:
            mod_path = root / rel
            tree = ast.parse(mod_path.read_text(encoding="utf-8"))
            mod = importlib.import_module(
                "research_tool." + rel.replace("/", ".").removesuffix(".py")
            )
            for node in ast.walk(tree):
                if not isinstance(node, ast.Raise):
                    continue
                exc = node.exc
                if not isinstance(exc, ast.Call):
                    continue
                func = exc.func
                if not (isinstance(func, ast.Name) and func.id in self._ERROR_CLASSES):
                    continue
                if not exc.args:
                    continue
                resolved = self._resolve_code(exc.args[0], mod)
                if resolved is not None:
                    codes.add(resolved)

        assert codes, "AST 未找到任何 raise 站点（解析异常）"
        unregistered = sorted(c for c in codes if self._lookup_fails(c))
        assert not unregistered, f"未注册的 raise 错误码: {unregistered}"

    @staticmethod
    def _resolve_code(arg, mod) -> str | None:
        # 形如 E_DL_BILI_403（模块级常量，可能别名到 ErrorCode.E_X.value）
        if isinstance(arg, ast.Name):
            val = getattr(mod, arg.id, None)
            return val if isinstance(val, str) else None
        # 形如 ErrorCode.E_PF_001_TOOL_MISSING.value
        if isinstance(arg, ast.Attribute) and arg.attr == "value":
            inner = arg.value
            if isinstance(inner, ast.Attribute) and isinstance(inner.value, ast.Name):
                if inner.value.id == "ErrorCode":
                    member = getattr(ErrorCode, inner.attr, None)
                    return member.value if member is not None else None
        return None

    @staticmethod
    def _lookup_fails(code: str) -> bool:
        try:
            lookup_code(code)
            return False
        except ConfigError:
            return True


class TestErrorInfo:
    """ErrorInfo 数据类。"""

    def test_error_info_immutable(self):
        info = ErrorInfo(
            code="E_TEST",
            category="TST",
            exit_code_hint=500,
            default_scene="s",
            default_cause="c",
            default_suggestion="g",
        )
        with pytest.raises(Exception):  # FrozenInstanceError 或 AttributeError
            info.code = "modified"  # type: ignore[misc]


class TestErrorRecord:
    """DE-010 ErrorRecord 数据类。"""

    def test_default_construction(self):
        rec = ErrorRecord(
            code="E_DL_001",
            scene="s",
            cause="c",
            suggestion="g",
        )
        assert rec.timestamp > 0
        assert rec.context == {}
        assert rec.stack == ""

    def test_to_3section_chinese(self):
        rec = ErrorRecord(
            code="E_DL_001",
            scene="下载超时",
            cause="网络不稳定",
            suggestion="检查网络后重试",
        )
        out = rec.to_3section()
        assert "场景: 下载超时" in out
        assert "原因: 网络不稳定" in out
        assert "建议: 检查网络后重试" in out


class TestLookupCode:
    """lookup_code 公共 API。"""

    def test_lookup_registered(self):
        info = lookup_code(ErrorCode.E_DL_001_NETWORK_TIMEOUT.value)
        assert info.code == ErrorCode.E_DL_001_NETWORK_TIMEOUT.value
        assert info.category == "DL"
        assert info.exit_code_hint == 500
        assert "网络" in info.default_scene or "下载" in info.default_scene

    def test_lookup_unregistered_raises(self):
        with pytest.raises(ConfigError) as exc:
            lookup_code("E_NOT_REAL_CODE_999")
        assert exc.value.code == ErrorCode.E_SYS_001_UNKNOWN_ERROR_CODE.value

    def test_cfg_001_suggestion_points_to_docs(self):
        # 回归：E_CFG_001 建议须指向 docs/config.example.yaml（实位于 docs/，非仓库根）。
        info = lookup_code(ErrorCode.E_CFG_001_CONFIG_MISSING.value)
        assert "docs/config.example.yaml" in info.default_suggestion


class TestRegisterError:
    """register_error 公共 API。"""

    def test_register_returns_record(self):
        rec = register_error(
            ErrorCode.E_DL_001_NETWORK_TIMEOUT.value,
            context={"url": "https://example.com/video"},
        )
        assert isinstance(rec, ErrorRecord)
        assert rec.code == ErrorCode.E_DL_001_NETWORK_TIMEOUT.value
        assert rec.context["url"] == "https://example.com/video"

    def test_register_unknown_raises(self):
        with pytest.raises(ConfigError):
            register_error("E_NOT_REAL_CODE_999")

    def test_register_with_overrides(self):
        rec = register_error(
            ErrorCode.E_LLM_001_LLM_CALL_FAILED.value,
            scene="自定义场景",
            cause="自定义原因",
            suggestion="自定义建议",
        )
        assert rec.scene == "自定义场景"
        assert rec.cause == "自定义原因"
        assert rec.suggestion == "自定义建议"

    def test_register_includes_stack_by_default(self):
        rec = register_error(ErrorCode.E_FM_001_FFMPEG_INVOKE_FAILED.value)
        assert rec.stack  # 非空

    def test_register_no_stack(self):
        rec = register_error(
            ErrorCode.E_FM_001_FFMPEG_INVOKE_FAILED.value,
            include_stack=False,
        )
        assert rec.stack == ""


class TestFormatError:
    """3 段式格式化。"""

    def test_format_3section(self):
        rec = register_error(ErrorCode.E_DL_001_NETWORK_TIMEOUT.value)
        out = format_error(rec)
        assert "E_DL_001_NETWORK_TIMEOUT" in out
        assert "场景" in out
        assert "原因" in out
        assert "建议" in out

    def test_format_with_context(self):
        rec = register_error(
            ErrorCode.E_CK_001_CACHE_DB_UNAVAILABLE.value,
            context={"db": "/tmp/cache.db", "errno": 13},
        )
        out = format_error(rec)
        assert "db=" in out
        assert "errno=" in out


class TestResolveExitCode:
    """进程退出码仲裁（403 > 401 > 404 > 400 > 500 > 0）。"""

    def test_empty_returns_zero(self):
        assert resolve_exit_code([]) == 0

    def test_single_500(self):
        rec = register_error(ErrorCode.E_DL_001_NETWORK_TIMEOUT.value)
        assert resolve_exit_code([rec]) == 500

    def test_403_priority(self):
        rec_403 = register_error(ErrorCode.E_PF_001_TOOL_MISSING.value)
        rec_500 = register_error(ErrorCode.E_DL_001_NETWORK_TIMEOUT.value)
        # 403 应优先
        assert resolve_exit_code([rec_500, rec_403]) == 403
        assert resolve_exit_code([rec_403, rec_500]) == 403

    def test_401_priority_over_500(self):
        rec_401 = register_error(ErrorCode.E_CFG_001_CONFIG_MISSING.value)
        rec_500 = register_error(ErrorCode.E_DL_001_NETWORK_TIMEOUT.value)
        assert resolve_exit_code([rec_500, rec_401]) == 401

    def test_mixed_priority(self):
        rec_403 = register_error(ErrorCode.E_PF_001_TOOL_MISSING.value)
        rec_401 = register_error(ErrorCode.E_CFG_001_CONFIG_MISSING.value)
        rec_500 = register_error(ErrorCode.E_DL_001_NETWORK_TIMEOUT.value)
        assert resolve_exit_code([rec_403, rec_401, rec_500]) == 403

    def test_404_only(self):
        # E_VID_001 (404) previously fell through to 0 — the bug.
        rec = register_error(ErrorCode.E_VID_001_VIDEO_NOT_FOUND.value)
        assert resolve_exit_code([rec]) == 404

    def test_400_only(self):
        # E_VID_002 (400) previously fell through to 0 — the bug.
        rec = register_error(ErrorCode.E_VID_002_INVALID_URL.value)
        assert resolve_exit_code([rec]) == 400

    def test_404_priority_over_500(self):
        # 确定性错误（404 用户需换 URL）应排在瞬时错误（500 可重试）之前。
        rec_404 = register_error(ErrorCode.E_VID_001_VIDEO_NOT_FOUND.value)
        rec_500 = register_error(ErrorCode.E_DL_001_NETWORK_TIMEOUT.value)
        assert resolve_exit_code([rec_500, rec_404]) == 404
        assert resolve_exit_code([rec_404, rec_500]) == 404

    def test_401_priority_over_404(self):
        # 配置缺失（401）排在用户输入错误（404）之前。
        rec_401 = register_error(ErrorCode.E_CFG_001_CONFIG_MISSING.value)
        rec_404 = register_error(ErrorCode.E_VID_001_VIDEO_NOT_FOUND.value)
        assert resolve_exit_code([rec_404, rec_401]) == 401

    def test_fallback_unknown_nonzero_hint(self, monkeypatch):
        # 未列入优先级序列的非零 hint 仍须退出非零（防御性兜底）。
        fake_info = ErrorInfo(
            code="E_SYNTHETIC_418",
            category="TST",
            exit_code_hint=418,
            default_scene="s",
            default_cause="c",
            default_suggestion="g",
        )
        rec = ErrorRecord(code="E_SYNTHETIC_418", scene="s", cause="c", suggestion="g")
        monkeypatch.setattr("research_tool.domain.errors.lookup_code", lambda _code: fake_info)
        assert resolve_exit_code([rec]) == 418


class TestExceptionSubclasses:
    """VideoIngestError 家族。"""

    def test_download_error(self):
        e = DownloadError("E_DL_001", "msg")
        assert isinstance(e, VideoIngestError)
        assert e.code == "E_DL_001"
        assert "E_DL_001" in str(e)

    def test_transcribe_error(self):
        e = TranscribeError("E_TX_001", "msg")
        assert isinstance(e, VideoIngestError)

    def test_ffmpeg_error(self):
        e = FFmpegError("E_FM_001", "msg")
        assert isinstance(e, VideoIngestError)

    def test_cache_error(self):
        e = CacheError("E_CK_001", "msg")
        assert isinstance(e, VideoIngestError)

    def test_preflight_error(self):
        e = PreflightError("E_PF_001", "msg")
        assert isinstance(e, VideoIngestError)

    def test_config_error(self):
        e = ConfigError("E_CFG_001", "msg")
        assert isinstance(e, VideoIngestError)
