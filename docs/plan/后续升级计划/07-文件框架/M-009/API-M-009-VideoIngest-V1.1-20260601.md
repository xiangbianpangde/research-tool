# 接口注释清单 — M-009 截图器 V1.1

> **生成方**：DD-M-009
> **日期**：2026-06-01
> **接口契约数**：1（IC-025）
> **函数签名注释数**：4 顶层 + 11 类方法 = 15

---

## [接口编号] IC-025（来自 DD-001）
## [关联契约] IC-025（来自 DD-001）
## [实现文件] research_tool/ffmpeg_wrapper.py

### [函数签名注释 1] capture_screenshots

```python
def capture_screenshots(
    video_path: str,        # [必填] 视频文件绝对路径
    video_id: str,          # [必填] 视频唯一 ID
    count: int = 5          # [可选] 期望截图数量（1-10）
) -> list[ScreenshotFrame]:  # [返回] 截图帧对象列表（失败时为 []）
    """
    M-009 对外主入口：截取视频的 I 帧并压缩到 ≤200KB。
    
    Args:
        video_path: 视频文件绝对路径；须存在且格式为 mp4/webm/mkv
        video_id: 视频唯一 ID；须非空且不含路径分隔符
        count: 期望截图数量；范围 1-10，默认 5
    
    Returns:
        ScreenshotFrame 列表（path/timestamp_sec/size_kb/idx）
        失败时（ffmpeg 异常）静默返回空列表
    
    Raises:
        不抛错（E_FM_001 通过 register_error 登记，主链不阻塞）
    
    Example:
        >>> frames = capture_screenshots("/path/to/abc123.mp4", "abc123", count=5)
        >>> len(frames) == 5
        True
    """
```

### [函数签名注释 2] invoke_ffmpeg

```python
def invoke_ffmpeg(
    args: list[str]   # [必填] ffmpeg 命令参数列表
) -> int:              # [返回] ffmpeg exit code（0=成功）
    """
    模块级 ffmpeg 调用快捷函数（FFmpegInvoker.invoke 包装）。
    
    Args:
        args: ffmpeg 参数列表（不含 ffmpeg 自身）
    
    Returns:
        ffmpeg 进程 exit code（0=成功，非零=失败）
    
    Raises:
        不抛错（异常内部捕获 + 登记 E_FM_001）
    """
```

### [函数签名注释 3] select_i_frames

```python
def select_i_frames(
    video_path: str,  # [必填] 视频文件绝对路径
    count: int        # [必填] 期望 I 帧数（1-10）
) -> list[str]:        # [返回] I 帧临时文件路径列表
    """
    模块级 I 帧选择快捷函数（IFrameSelector.select 包装）。
    
    Args:
        video_path: 视频文件绝对路径
        count: 期望帧数
    
    Returns:
        I 帧临时文件绝对路径列表
        ffmpeg 失败时返回 []
    """
```

### [函数签名注释 4] compress_image

```python
def compress_image(
    input_path: str,   # [必填] 输入图像路径
    output_path: str   # [必填] 输出图像路径
) -> int:               # [返回] 输出文件字节数
    """
    模块级图像压缩快捷函数（ImageCompressor.compress 包装）。
    
    Args:
        input_path: 输入 I 帧路径
        output_path: 输出压缩图像路径
    
    Returns:
        输出文件字节数；ffmpeg 失败时返回 0
    """
```

---

## [类方法签名注释清单]

### FFmpegInvoker

| 方法 | 签名 | 职责 |
|------|------|------|
| `__init__` | `(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", timeout_sec=30)` | 构造调用器 |
| `invoke` | `(args: list[str]) -> int` | 执行 ffmpeg |
| `parse_progress` | `(stderr_line: str) -> Optional[float]` | 解析进度行 |
| `probe_duration` | `(video_path: str) -> int` | 探测视频时长 |

### IFrameSelector

| 方法 | 签名 | 职责 |
|------|------|------|
| `__init__` | `(filter_args=None, count=5)` | 构造选择器 |
| `select` | `(video_path: str, count: int) -> list[str]` | 抽取 I 帧 |
| `count_frames` | `(video_path: str) -> int` | 探测 I 帧数 |
| `build_select_filter` | `(count: int) -> str` | 构造 filter 表达式 |

### ImageCompressor

| 方法 | 签名 | 职责 |
|------|------|------|
| `__init__` | `(max_size_kb=200, default_qscale=2)` | 构造压缩器 |
| `compress` | `(input_path: str, output_path: str) -> int` | 压缩图像 |
| `adjust_qscale` | `(current_size: int, current_q: int) -> int` | 自适应 qscale |
| `needs_recompress` | `(size_bytes: int) -> bool` | 判断是否需重压 |

### OutputNamer

| 方法 | 签名 | 职责 |
|------|------|------|
| `__init__` | `(base_dir="screenshots", pattern="{video_id}_{timestamp}_{idx:03d}.jpg")` | 构造命名器 |
| `generate_name` | `(video_id: str, timestamp_sec: int, idx: int) -> str` | 生成文件名 |
| `ensure_unique` | `(name: str) -> str` | 唯一性保证 |

---

## [接口契约覆盖验收]

| 契约 | 实现函数 | 入参完整 | 出参完整 | 错误码完整 | 注释完整 |
|------|---------|---------|---------|-----------|---------|
| IC-025 | capture_screenshots | ✓ | ✓ | ✓ | ✓ |

**1/1 接口契约 100% 注释化**（D4 = 100%）

## [来源标注]

- 接口契约：[DD-001:IC-025]
- 函数签名：[DD-001:MD-009]
- 类型注解：[DD-001:CS-001 类型注解规范]
