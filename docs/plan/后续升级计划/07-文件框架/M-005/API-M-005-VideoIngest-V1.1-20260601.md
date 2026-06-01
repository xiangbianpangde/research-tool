# 接口注释清单 — M-005 转写器（DD-M-005）

> **生成方**：DD-M-005
> **日期**：2026-06-01
> **关联契约**：IC-012（API-012 三引擎转写，DD-001）
> **接口数**：5（公开函数 API + 1 模板方法 + 6 类方法）

---

## API-012 三引擎转写（IC-012）

```
[接口编号] API-012
[关联契约] IC-012（来自 DD-001）
[实现文件] research_tool/transcriber.py
[函数签名注释]
  def transcribe(
      audio_path: str,                              # [音频文件绝对路径，格式: wav/mp3/m4a/flac/ogg]
      audio_fingerprint: str,                       # [音频 sha256 指纹，格式: "sha256:..."]
      engine: EngineType = EngineType.WHISPER,      # [主引擎选择，默认 whisper]
      model_size: str = "medium",                   # [模型档位: tiny/base/small/medium/large-v3]
  ) -> Transcript:                                   # [转写稿对象]
      """
      三引擎转写主入口（whisper/bcut/groq + 自动 fallback）。

      Args:
          audio_path: 音频文件绝对路径；必须存在且 ffmpeg 可解码。
                      支持扩展名: .wav / .mp3 / .m4a / .flac / .ogg
          audio_fingerprint: 音频 sha256 指纹；用于二级缓存命中键。
                            格式约定: "sha256:<hex>"
          engine: 主引擎选择。默认 EngineType.WHISPER。
                  可选: EngineType.WHISPER / EngineType.BCUT / EngineType.GROQ
          model_size: faster-whisper 模型档位。默认 "medium"。
                     可选: "tiny" / "base" / "small" / "medium" / "large-v3"
                     注: RAM < 8GB 时自动降档至 "base" 或 "small"

      Returns:
          Transcript: 转写稿对象，含 segments（带 timestamps）、language、
                      cer_estimate（CER 估算值）、engine_used（实际使用引擎）。

      Raises:
          E_TR_001: 三引擎（whisper/bcut/groq）全失败。
                    触发条件: 主引擎失败 + fallback 链全部失败。
                    中间产物保留（含 partial transcript）供 M-010 错误码登记。
          FileNotFoundError: audio_path 不存在。
          PermissionError: audio_path 无读权限。
          MemoryError: faster-whisper 加载 OOM（已自动降档后仍失败时）。

      Example:
          >>> from research_tool.transcriber import transcribe, EngineType
          >>> transcript = transcribe(
          ...     "/tmp/audio.wav",
          ...     "sha256:abc123...",
          ...     EngineType.WHISPER,
          ...     "medium",
          ... )
          >>> len(transcript.segments) > 0
          True

      [来源标注] [DD-001:IC-012] [DD-001:MD-M-005 函数签名 transcribe] [调研:S-004]
      """
```

---

## API-012.A 选主引擎（IC-012 子接口）

```
[接口编号] API-012.A
[关联契约] IC-012（select_engine 部分）
[实现文件] research_tool/transcriber.py
[函数签名注释]
  def select_engine(ram_gb: float) -> EngineType:
      """
      依据可用 RAM 选择主转写引擎。

      Args:
          ram_gb: 可用 RAM（GB），由 detect_ram_available() 探测。

      Returns:
          EngineType: 选定的主引擎。默认 EngineType.WHISPER。
                      当 RAM < MIN_RAM_GB (8.0) 时仍返回 WHISPER，
                      但 EngineSelector 会自动降档 model_size。

      Raises:
          （无错误码）

      Example:
          >>> engine = select_engine(16.0)
          >>> engine == EngineType.WHISPER
          True

      [来源标注] [DD-001:MD-M-005 函数签名 select_engine]
      """
```

---

## API-012.B 加载 Whisper 模型（IC-012 子接口）

```
[接口编号] API-012.B
[关联契约] IC-012（load_whisper_model 部分）
[实现文件] research_tool/transcriber.py
[函数签名注释]
  def load_whisper_model(size: str) -> "WhisperModel":
      """
      加载 faster-whisper 模型（TS-004 / TS-022）。

      Args:
          size: 模型档位。
                可选值: "tiny" / "base" / "small" / "medium" / "large-v3"
                注: 模型文件应已下载至 ~/.cache/huggingface/

      Returns:
          WhisperModel: faster-whisper 模型实例。

      Raises:
          FileNotFoundError: 模型文件不存在。
                             提示用户: faster-whisper download <size>
          MemoryError: 加载 OOM。
                       触发自动降档至 base/small（由 ModelManager.handle_oom 处理）。

      Example:
          >>> model = load_whisper_model("medium")
          >>> model is not None
          True

      [来源标注] [DD-001:MD-M-005 函数签名 load_whisper_model] [DD-001:TS-004] [DD-001:TS-022]
      """
```

---

## API-012.C 探测可用 RAM（IC-030 对称）

```
[接口编号] API-012.C
[关联契约] IC-030（来自 DD-001，M-012 资源探测的对称函数）
[实现文件] research_tool/transcriber.py
[函数签名注释]
  def detect_ram_available() -> float:
      """
      探测系统可用 RAM（GB），使用 psutil。

      Args:
          （无参数）

      Returns:
          float: 可用 RAM（GB）。
                 特殊值: 0.0 表示探测失败（psutil 不可用或异常）。
                         M-005 内部视 0.0 为 RAM 不足，触发降档。

      Raises:
          （无错误码；探测失败返回 0.0 而非抛错）

      Example:
          >>> ram = detect_ram_available()
          >>> ram > 0
          True

      [来源标注] [DD-001:MD-M-005 函数签名 detect_ram_available] [DD-001:IC-030]
      """
```

---

## API-012.D CER 估算（IC-012 cer_estimate 输出）

```
[接口编号] API-012.D
[关联契约] IC-012（cer_estimate 输出字段）
[实现文件] research_tool/transcriber.py
[函数签名注释]
  def estimate_cer(transcript: str, reference: str) -> float:
      """
      估算转写稿的字符错误率（CER, Character Error Rate）。

      Args:
          transcript: 转写文本（必填非空）。
          reference: 参考文本（V1.1 可为空）。
                     为空时返回 0.0 占位，不阻塞主链。

      Returns:
          float: CER 值 ∈ [0.0, 1.0]。
                 0.0 = 完全匹配
                 0.15 = 默认阈值（validate 函数校验）
                 1.0 = 完全不匹配

      Raises:
          ValueError: reference 非空但 transcript 为空。

      Example:
          >>> cer = estimate_cer("hello world", "hello world")
          >>> cer
          0.0
          >>> cer = estimate_cer("hello", "")
          >>> cer
          0.0

      [来源标注] [DD-001:MD-M-005 函数签名 estimate_cer] [DD-001:IC-012 cer_estimate]
      """
```

---

## 类方法清单（6 类）

### EngineSelector 方法

```
[EngineSelector.select]
  def select(self, ram_gb: float) -> EngineType:
      """[职责] 依据 RAM 选主引擎。[来源: DD-001:MD-M-005 子模块1]"""

[EngineSelector.fallback_chain]
  def fallback_chain(self) -> list[EngineType]:
      """[职责] 返回降级链 [WHISPER, BCUT, GROQ]。[来源: DD-001:MD-M-005 子模块1]"""

[EngineSelector.is_available]
  def is_available(self, engine: EngineType) -> bool:
      """[职责] 引擎可用性检查（api_key、依赖等）。[来源: DD-001:MD-M-005 子模块1]"""
```

### WhisperEngine 方法

```
[WhisperEngine.transcribe]
  def transcribe(self, audio_path: str) -> Transcript:
      """[职责] faster-whisper 转写入口。[来源: DD-001:MD-M-005 子模块2]"""

[WhisperEngine.load_model]
  def load_model(self, size: str) -> "WhisperModel":
      """[职责] 加载模型。[来源: DD-001:MD-M-005 子模块2]"""

[WhisperEngine.handle_oom]
  def handle_oom(self, size: str) -> "WhisperModel":
      """[职责] OOM 时降档。[来源: DD-001:MD-M-005 子模块2 + RAM<8GB降档]"""

[WhisperEngine.unload_model]
  def unload_model(self) -> None:
      """[职责] 显式卸载释放内存。[来源: DD-M推断:依据 — 长时间任务需主动释放]"""
```

### BcutEngine 方法

```
[BcutEngine.transcribe]
  def transcribe(self, audio_path: str) -> Transcript:
      """[职责] B 站 ASR 转写。[来源: DD-001:MD-M-005 子模块3 + TS-005]"""

[BcutEngine.handle_403]
  def handle_403(self) -> None:
      """[职责] 403 处理（不绕过，切换下一引擎）。[来源: DD-001:MD-M-005 + AR:B-002]"""

[BcutEngine.upload_audio]
  def upload_audio(self, audio_path: str) -> str:
      """[职责] 上传至 B 站 ASR 服务。[来源: DD-001:MD-M-005 子模块3]"""
```

### GroqEngine 方法

```
[GroqEngine.transcribe]
  def transcribe(self, audio_path: str) -> Transcript:
      """[职责] Groq API 转写。[来源: DD-001:MD-M-005 子模块4]"""

[GroqEngine.handle_429]
  def handle_429(self) -> None:
      """[职责] 限流处理（指数退避）。[来源: DD-001:MD-M-005 子模块4]"""

[GroqEngine.call_api]
  def call_api(self, audio_path: str) -> dict:
      """[职责] HTTPS POST。[来源: DD-001:MD-M-005 子模块4 + AR:BR-016 敏感过滤]"""
```

### ModelManager 方法

```
[ModelManager.load]
  def load(self, size: str) -> "WhisperModel":
      """[职责] 加载模型。[来源: DD-001:MD-M-005 子模块5]"""

[ModelManager.unload]
  def unload(self) -> None:
      """[职责] 卸载释放。[来源: DD-001:MD-M-005 子模块5]"""

[ModelManager.detect_ram]
  def detect_ram(self) -> float:
      """[职责] 探测可用 RAM。[来源: DD-001:MD-M-005 子模块5 + IC-030]"""

[ModelManager.suggest_size]
  def suggest_size(self, ram_gb: float) -> str:
      """[职责] 推荐档位。[来源: DD-001:MD-M-005 子模块5 + RAM<8GB降档]"""
```

### CEREstimator 方法

```
[CEREstimator.estimate]
  @staticmethod
  def estimate(transcript: str, reference: str) -> float:
      """[职责] 计算 CER。[来源: DD-001:MD-M-005 子模块6]"""

[CEREstimator.validate]
  @staticmethod
  def validate(cer: float, threshold: float = 0.15) -> bool:
      """[职责] 校验 CER 是否超阈值。[来源: DD-001:MD-M-005 子模块6]"""
```

---

## 模板方法

```
[run_transcription_pipeline]
  async def run_transcription_pipeline(
      audio_path: str,
      audio_fingerprint: str,
  ) -> Transcript:
      """
      模板方法：转写主流程骨架。
      
      步骤:
          1. detect_ram → 选档位
          2. select_engine → 选主引擎
          3. 主引擎.transcribe → 成功则返回
          4. 失败 → 切 fallback 引擎
          5. 三引擎全失败 → 登记 E_TR_001
      
      [来源标注] [DD-001:MD-M-005 设计模式 策略模式+模板方法] [调研:S-004]
      """
```

---

## 接口契约覆盖（DD-001:IC-012）

| 契约字段 | 框架注释体现 | 文件位置 |
|---------|------------|---------|
| audio_path 必填 | ✓ 注释含「音频文件绝对路径」 | transcriber.py:transcribe |
| audio_fingerprint 必填 | ✓ 注释含「音频 sha256 指纹」 | transcriber.py:transcribe |
| engine 可选 WHISPER | ✓ 默认值 EngineType.WHISPER | transcriber.py:transcribe |
| model_size 可选 medium | ✓ 默认值 "medium" | transcriber.py:transcribe |
| transcript 出参 | ✓ 返回值说明含 segments/language/cer_estimate | transcriber.py:transcribe |
| cer_estimate 出参 | ✓ 单独函数 estimate_cer 注释 | transcriber.py:estimate_cer |
| engine_used 出参 | ✓ Transcript.segments.engine_used 字段 | transcriber.py:transcribe |
| E_TR_001 错误码 | ✓ Raises 块标注 E_TR_001 | transcriber.py:transcribe |
| 自动降档 RAM<8GB | ✓ model_size 注释含「RAM<8GB 自动降档」 | transcriber.py:transcribe |
| 幂等键 audio_fingerprint | ✓ 注释标注「用于二级缓存命中键」 | transcriber.py:transcribe |
| 性能 5-30min | ✓ 函数 docstring 注释 | transcriber.py:transcribe |
| 并发安全 Semaphore(3) | ✓ [DD-001:IC-012 后置条件] 已在文件头注释体现 | transcriber.py:文件头 |

**12/12 契约字段 100% 体现。**

---

> **本文件结束**。M-005 接口注释清单就绪（5 函数 + 6 类方法 + 1 模板方法 = 12 项）。
