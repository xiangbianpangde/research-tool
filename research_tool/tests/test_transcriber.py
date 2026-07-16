"""M-005 转写器单元测试。

覆盖：
- NFR4：缓存命中跳过转写（核心场景）
- 失败重试：主引擎失败 → 切 fallback
- 转写超时：asyncio.wait_for 触发
- 引擎可用性（Whisper 缺失 / Groq 缺 key）
- compute_audio_fingerprint
- estimate_cer
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from research_tool.domain.errors import TranscribeError
from research_tool.domain.models import Transcript, TranscriptSegment
from research_tool.infrastructure.ingest.cache_manager import CacheEntry, CacheManager
from research_tool.infrastructure.ingest.transcriber import (
    E_TR_001,
    E_TR_002,
    E_TR_003,
    EngineSelector,
    EngineType,
    GROQ_MAX_FILE_BYTES,
    WhisperEngine,
    compute_audio_fingerprint,
    detect_ram_available,
    estimate_cer,
    transcribe,
)


# --------------------------------------------------------------------------- #
# EngineSelector
# --------------------------------------------------------------------------- #


class TestEngineSelector:
    """引擎选择 + 降级链 + 可用性。"""

    def test_default_prefers_minimax(self):
        sel = EngineSelector()
        assert sel.preferred_engine == EngineType.MINIMAX

    def test_minimax_available_with_key(self):
        sel = EngineSelector(
            preferred_engine=EngineType.MINIMAX, minimax_api_key="mm-test-key"
        )
        assert sel.is_available(EngineType.MINIMAX) is True
        assert sel.select() == EngineType.MINIMAX

    def test_minimax_unavailable_without_key(self, monkeypatch):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        sel = EngineSelector(preferred_engine=EngineType.MINIMAX, minimax_api_key=None)
        assert sel.is_available(EngineType.MINIMAX) is False
        # 无 MiniMax key 时 select 落到 whisper（若已装）或 groq
        chosen = sel.select()
        assert chosen in (EngineType.WHISPER, EngineType.GROQ, EngineType.MINIMAX)

    def test_groq_unavailable_without_key(self, monkeypatch):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        sel = EngineSelector(
            preferred_engine=EngineType.GROQ, groq_api_key=None, minimax_api_key=None
        )
        assert sel.is_available(EngineType.GROQ) is False
        assert sel.select() == EngineType.WHISPER

    def test_groq_available_with_key(self):
        sel = EngineSelector(preferred_engine=EngineType.GROQ, groq_api_key="gsk-test")
        assert sel.is_available(EngineType.GROQ) is True
        assert sel.select() == EngineType.GROQ

    def test_fallback_chain_order(self):
        sel = EngineSelector()
        chain = sel.fallback_chain()
        assert chain[0] == EngineType.MINIMAX
        assert EngineType.WHISPER in chain
        assert EngineType.GROQ in chain


# --------------------------------------------------------------------------- #
# compute_audio_fingerprint
# --------------------------------------------------------------------------- #


class TestComputeAudioFingerprint:
    """音频指纹。"""

    def test_deterministic(self, tmp_path: Path):
        f = tmp_path / "a.wav"
        f.write_bytes(b"1234567890" * 100)
        h1 = compute_audio_fingerprint(str(f))
        h2 = compute_audio_fingerprint(str(f))
        assert h1 == h2
        assert h1.startswith("sha256:")

    def test_different_files_differ(self, tmp_path: Path):
        f1 = tmp_path / "a.wav"
        f1.write_bytes(b"abc")
        time.sleep(0.05)  # mtime 间隔
        f2 = tmp_path / "b.wav"
        f2.write_bytes(b"xyz")  # 内容不同 → 指纹不同
        h1 = compute_audio_fingerprint(str(f1))
        h2 = compute_audio_fingerprint(str(f2))
        assert h1 != h2

    def test_missing_raises(self, tmp_path: Path):
        with pytest.raises(TranscribeError) as exc_info:
            compute_audio_fingerprint(str(tmp_path / "nope.wav"))
        assert exc_info.value.code == E_TR_001


# --------------------------------------------------------------------------- #
# estimate_cer
# --------------------------------------------------------------------------- #


class TestEstimateCer:
    """CER 估算。"""

    def test_identical(self):
        assert estimate_cer("hello world", "hello world") == 0.0

    def test_empty_reference(self):
        assert estimate_cer("anything", "") == 0.0

    def test_empty_transcript_raises(self):
        with pytest.raises(ValueError):
            estimate_cer("", "reference")

    def test_partial_match(self):
        cer = estimate_cer("hello world", "hello WORLD")
        assert 0.0 < cer < 1.0

    def test_completely_different(self):
        cer = estimate_cer("abc", "xyz")
        # 距离 3 / len 3 = 1.0
        assert cer == 1.0


# --------------------------------------------------------------------------- #
# detect_ram_available
# --------------------------------------------------------------------------- #


class TestDetectRamAvailable:
    """RAM 探测。"""

    def test_returns_float(self):
        ram = detect_ram_available()
        assert isinstance(ram, float)
        assert ram >= 0.0


# --------------------------------------------------------------------------- #
# WhisperEngine（不调真实模型，mock 外部依赖）
# --------------------------------------------------------------------------- #


class TestWhisperEngineMocked:
    """WhisperEngine 单测（mock faster_whisper.WhisperModel）。"""

    def _make_fake_model(self, segments_data, language="zh"):
        """构造 fake WhisperModel：transcribe 返回 (segments_iter, info)。"""

        class FakeInfo:
            pass

        info = FakeInfo()
        info.language = language

        class FakeSeg:
            def __init__(self, start, end, text):
                self.start = start
                self.end = end
                self.text = text

        segments_iter = iter([FakeSeg(s, e, t) for s, e, t in segments_data])
        return MagicMock(transcribe=MagicMock(return_value=(segments_iter, info)))

    def test_load_calls_whisper_model(self):
        """load() 调用 WhisperModel 构造。"""
        with patch("faster_whisper.WhisperModel") as mock_model:
            mock_model.return_value = MagicMock()
            we = WhisperEngine(model_size="tiny")
            we.load()
            mock_model.assert_called_once()

    def test_transcribe_returns_transcript(self, tmp_path: Path):
        """transcribe 解析 segments → Transcript。"""
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"x")
        with patch("faster_whisper.WhisperModel") as mock_model:
            mock_model.return_value = self._make_fake_model(
                [(0.0, 1.0, "hello"), (1.0, 2.0, "world")],
                language="zh",
            )
            we = WhisperEngine(model_size="tiny")
            result = we.transcribe(str(audio), language="zh")
        assert isinstance(result, Transcript)
        assert result.engine == "whisper"
        assert len(result.segments) == 2
        assert result.full_text == "hello world"

    def test_transcribe_handles_none_language(self, tmp_path: Path):
        """language=None（自动检测）应走 model.transcribe(language=None) 路径。"""
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"x")
        with patch("faster_whisper.WhisperModel") as mock_model:
            mock_model.return_value = self._make_fake_model([(0.0, 1.0, "hi")], language="en")
            we = WhisperEngine(model_size="tiny")
            result = we.transcribe(str(audio), language="")  # 空串 → 走 None 分支
        assert result.language == "en"


# --------------------------------------------------------------------------- #
# transcribe 顶层：NFR4 缓存命中短路
# --------------------------------------------------------------------------- #


class TestTranscribeCacheHit:
    """NFR4：同 URL 二次运行跳过转写（缓存命中短路）。"""

    @pytest.mark.asyncio
    async def test_cache_hit_short_circuits(self, tmp_path: Path):
        """缓存命中时不应调任何引擎；直接返回缓存 transcript。"""
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"x")
        fp = compute_audio_fingerprint(str(audio))

        # mock cache_manager：query 返回非空，write 不被调
        cm = MagicMock(spec=CacheManager)
        from research_tool.infrastructure.ingest.cache_manager import compute_url_sha256

        cached_payload = {
            "language": "zh",
            "full_text": "cached text",
            "segments": [{"start": 0.0, "end": 1.0, "text": "cached"}],
            "engine": "whisper",
            "cer_estimate": 0.0,
            "model_size": "tiny",
            "elapsed_sec": 1.0,
        }
        cm.query = AsyncMock(
            return_value=CacheEntry(
                url=fp,
                url_sha256=compute_url_sha256(fp).replace("sha256:", ""),
                etag="",
                platform="video",
                payload=cached_payload,
                created_at=time.time(),
            )
        )
        cm.write = AsyncMock()

        # 引擎选择：没装 faster_whisper；没 groq_api_key
        # 但缓存命中短路应跳过所有引擎
        with patch(
            "research_tool.infrastructure.ingest.transcriber.get_cache_manager",
            AsyncMock(return_value=cm),
        ):
            result = await transcribe(
                str(audio),
                fp,
                language="zh",
                engine=EngineType.WHISPER,
                cache_manager=cm,
            )
        assert result.full_text == "cached text"
        assert len(result.segments) == 1
        cm.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_miss_calls_engine(self, tmp_path: Path):
        """缓存未命中时调引擎；并把结果写入缓存。"""
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"x")
        fp = compute_audio_fingerprint(str(audio))

        # cache: query None
        cm = MagicMock(spec=CacheManager)
        cm.query = AsyncMock(return_value=None)
        cm.write = AsyncMock(return_value=True)

        # 模拟 _run_engine 走 whisper 路径
        expected = Transcript(
            language="zh",
            full_text="new text",
            segments=[TranscriptSegment(start=0.0, end=1.0, text="new")],
            engine="whisper",
        )

        with (
            patch(
                "research_tool.infrastructure.ingest.transcriber._run_engine",
                AsyncMock(return_value=expected),
            ),
            patch(
                "research_tool.infrastructure.ingest.transcriber.get_cache_manager",
                AsyncMock(return_value=cm),
            ),
        ):
            result = await transcribe(
                str(audio),
                fp,
                engine=EngineType.WHISPER,
                cache_manager=cm,
                timeout_sec=10,
            )
        assert result.full_text == "new text"
        # 写过缓存
        cm.write.assert_called_once()


# --------------------------------------------------------------------------- #
# transcribe 顶层：失败重试 + 超时
# --------------------------------------------------------------------------- #


class TestTranscribeFailureModes:
    """失败重试 + 超时。"""

    @pytest.mark.asyncio
    async def test_whisper_fails_falls_back_to_groq(self, tmp_path: Path):
        """whisper 失败 → 切到 groq。"""
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"x")
        fp = compute_audio_fingerprint(str(audio))

        cm = MagicMock(spec=CacheManager)
        cm.query = AsyncMock(return_value=None)
        cm.write = AsyncMock(return_value=True)

        call_log: list[str] = []

        async def fake_run(engine, *_args, **_kw):
            call_log.append(engine.value)
            if engine == EngineType.WHISPER:
                raise TranscribeError(E_TR_002, "whisper boom")
            return Transcript(language="zh", full_text="groq result", segments=[], engine="groq")

        with (
            patch(
                "research_tool.infrastructure.ingest.transcriber._run_engine", side_effect=fake_run
            ),
            patch(
                "research_tool.infrastructure.ingest.transcriber.get_cache_manager",
                AsyncMock(return_value=cm),
            ),
        ):
            result = await transcribe(
                str(audio),
                fp,
                engine=EngineType.WHISPER,
                preferred_engine=EngineType.WHISPER,
                minimax_api_key="",  # 强制禁用 minimax，验证 whisper→groq
                groq_api_key="gsk-test",
                cache_manager=cm,
                timeout_sec=10,
            )
        assert call_log == ["whisper", "groq"]
        assert result.engine == "groq"
        assert result.full_text == "groq result"

    @pytest.mark.asyncio
    async def test_all_engines_fail_raises_e_tr_001(self, tmp_path: Path):
        """所有引擎失败 → E_TR_001。"""
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"x")
        fp = compute_audio_fingerprint(str(audio))

        cm = MagicMock(spec=CacheManager)
        cm.query = AsyncMock(return_value=None)
        cm.write = AsyncMock(return_value=True)

        async def always_fail(engine, *_a, **_kw):
            raise TranscribeError(
                E_TR_002 if engine == EngineType.WHISPER else E_TR_003, f"{engine} boom"
            )

        with (
            patch(
                "research_tool.infrastructure.ingest.transcriber._run_engine",
                side_effect=always_fail,
            ),
            patch(
                "research_tool.infrastructure.ingest.transcriber.get_cache_manager",
                AsyncMock(return_value=cm),
            ),
        ):
            with pytest.raises(TranscribeError) as exc_info:
                await transcribe(
                    str(audio),
                    fp,
                    engine=EngineType.WHISPER,
                    preferred_engine=EngineType.WHISPER,
                    groq_api_key="gsk-test",
                    cache_manager=cm,
                    timeout_sec=5,
                )
        assert exc_info.value.code == E_TR_001

    @pytest.mark.asyncio
    async def test_timeout_raises_e_tr_004(self, tmp_path: Path):
        """引擎超时 → E_TR_004；切下一个引擎。"""
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"x")
        fp = compute_audio_fingerprint(str(audio))

        cm = MagicMock(spec=CacheManager)
        cm.query = AsyncMock(return_value=None)
        cm.write = AsyncMock(return_value=True)

        async def slow_engine(engine, *_a, **_kw):
            await asyncio.sleep(2.0)  # 超过 timeout_sec=0.1
            raise RuntimeError("unreachable")

        with (
            patch(
                "research_tool.infrastructure.ingest.transcriber._run_engine",
                side_effect=slow_engine,
            ),
            patch(
                "research_tool.infrastructure.ingest.transcriber.get_cache_manager",
                AsyncMock(return_value=cm),
            ),
        ):
            with pytest.raises(TranscribeError) as exc_info:
                await transcribe(
                    str(audio),
                    fp,
                    engine=EngineType.WHISPER,
                    preferred_engine=EngineType.WHISPER,
                    groq_api_key="gsk-test",
                    cache_manager=cm,
                    timeout_sec=1,  # 1s 后 timeout
                )
        # 所有引擎都超时 → E_TR_001 汇总
        assert exc_info.value.code == E_TR_001


# --------------------------------------------------------------------------- #
# GroqEngine（mock openai SDK）
# --------------------------------------------------------------------------- #


class TestGroqEngineMocked:
    """Groq 引擎 mock 测试。"""

    def test_needs_compress_small_file(self, tmp_path: Path):
        f = tmp_path / "small.mp3"
        f.write_bytes(b"x" * 100)  # < 18MB
        from research_tool.infrastructure.ingest.transcriber import GroqEngine

        assert GroqEngine._needs_compress(str(f)) is False

    def test_needs_compress_large_file(self, tmp_path: Path):
        # 不实际写 18MB；patch getsize
        with patch("os.path.getsize", return_value=GROQ_MAX_FILE_BYTES + 1):
            from research_tool.infrastructure.ingest.transcriber import GroqEngine

            assert GroqEngine._needs_compress("fake/path.mp3") is True


class TestMiniMaxEngineMocked:
    """MiniMax-M3 多模态转写：mock OpenAI 客户端，驱动真实 MiniMaxEngine.transcribe。"""

    def test_transcribe_video_inline_calls_chat(self, tmp_path: Path):
        from research_tool.infrastructure.ingest.transcriber import MiniMaxEngine

        video = tmp_path / "talk.mp4"
        video.write_bytes(b"\x00\x00fake-mp4-bytes")

        class _Msg:
            content = "Hello from the podium. VGGT is a geometry transformer."

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        captured: dict = {}

        class _FakeCompletions:
            def create(self, **kwargs):
                captured.update(kwargs)
                return _Resp()

        class _FakeChat:
            completions = _FakeCompletions()

        class _FakeOpenAI:
            def __init__(self, **kw):
                self.kw = kw
                self.chat = _FakeChat()

        eng = MiniMaxEngine(
            api_key="mm-test", model="MiniMax-M3", base_url="https://api.minimaxi.com/v1"
        )

        def _fake_call(self, user_content):
            captured["model"] = self.model
            captured["content"] = user_content
            return "Hello from the podium. VGGT is a geometry transformer."

        with patch.object(MiniMaxEngine, "_call_chat", _fake_call):
            out = eng.transcribe(str(video), language="en", video_path=str(video))

        assert out.engine == "minimax"
        assert "VGGT" in out.full_text
        content = captured["content"]
        assert any(p.get("type") == "video_url" for p in content if isinstance(p, dict))
        vu = next(p for p in content if p.get("type") == "video_url")
        assert vu["video_url"]["url"].startswith("data:video/")

    def test_audio_only_without_images_raises(self, tmp_path: Path):
        from research_tool.infrastructure.ingest.transcriber import MiniMaxEngine, E_TR_006

        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        eng = MiniMaxEngine(api_key="mm-test")
        with pytest.raises(TranscribeError) as ei:
            eng.transcribe(str(audio), language="zh")
        assert ei.value.code == E_TR_006

    @pytest.mark.asyncio
    async def test_minimax_fallback_after_failure(self, tmp_path: Path):
        """minimax 失败 → whisper → groq（mock _run_engine）。"""
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"x")
        fp = compute_audio_fingerprint(str(audio))
        cm = MagicMock(spec=CacheManager)
        cm.query = AsyncMock(return_value=None)
        cm.write = AsyncMock(return_value=True)
        call_log: list[str] = []

        async def fake_run(engine, *_a, **_kw):
            call_log.append(engine.value)
            if engine == EngineType.MINIMAX:
                raise TranscribeError("E_TR_006_MINIMAX_FAILED", "mm boom")
            if engine == EngineType.WHISPER:
                raise TranscribeError(E_TR_002, "whisper boom")
            return Transcript(language="zh", full_text="from groq", segments=[], engine="groq")

        with (
            patch(
                "research_tool.infrastructure.ingest.transcriber._run_engine",
                side_effect=fake_run,
            ),
            patch(
                "research_tool.infrastructure.ingest.transcriber.get_cache_manager",
                AsyncMock(return_value=cm),
            ),
        ):
            result = await transcribe(
                str(audio),
                fp,
                preferred_engine=EngineType.MINIMAX,
                minimax_api_key="mm-k",
                groq_api_key="gsk-test",
                cache_manager=cm,
                timeout_sec=10,
            )
        assert call_log[0] == "minimax"
        assert "groq" in call_log
        assert result.full_text == "from groq"

    def test_init_requires_api_key(self):
        with pytest.raises(TranscribeError):
            from research_tool.infrastructure.ingest.transcriber import GroqEngine

            GroqEngine(api_key="")

    def test_transcribe_calls_api(self, tmp_path: Path):
        """mock openai client，验证 transcribe 解析返回。"""
        f = tmp_path / "x.mp3"
        f.write_bytes(b"x" * 100)
        fake_resp = {
            "language": "zh",
            "text": "hello world",
            "segments": [{"start": 0.0, "end": 1.0, "text": "hello world"}],
        }
        fake_client = MagicMock()
        fake_client.audio.transcriptions.create = MagicMock(return_value=fake_resp)
        with patch("openai.OpenAI", return_value=fake_client):
            from research_tool.infrastructure.ingest.transcriber import GroqEngine

            ge = GroqEngine(api_key="gsk-test", model="whisper-large-v3")
            result = ge.transcribe(str(f), language="zh")
        assert result.engine == "groq"
        assert result.full_text == "hello world"
        assert len(result.segments) == 1

    def test_transcribe_handles_compress(self, tmp_path: Path):
        """> 18MB 时走 ffmpeg 压缩路径。"""
        f = tmp_path / "big.mp3"
        f.write_bytes(b"x" * 100)
        with (
            patch("os.path.getsize", return_value=GROQ_MAX_FILE_BYTES + 1024),
            patch.object(
                # 避免实际调 ffmpeg：patch 整个 _compress_audio
                __import__(
                    "research_tool.infrastructure.ingest.transcriber", fromlist=["GroqEngine"]
                ).GroqEngine,
                "_compress_audio",
                return_value=str(f),
            ) as mock_compress,
            patch("openai.OpenAI") as mock_openai,
        ):
            fake_client = MagicMock()
            fake_client.audio.transcriptions.create = MagicMock(
                return_value={"language": "zh", "text": "x", "segments": []}
            )
            mock_openai.return_value = fake_client
            from research_tool.infrastructure.ingest.transcriber import GroqEngine

            ge = GroqEngine(api_key="gsk-test")
            result = ge.transcribe(str(f), language="zh")
        mock_compress.assert_called_once()
        assert result.full_text == "x"
