"""
ffmpeg_wrapper.py — M-009 截图器模块（M-009 唯一负责模块）

[文件路径] research_tool/ffmpeg_wrapper.py
[文件职责] ffmpeg I 帧抽取 + 压缩到 ≤200KB 产出视频截图列表
[所属模块] M-009（来自 DD-001 分配，唯一负责）
[关联设计规范] MD-009 / FS-009 / IC-025 / CS-001
[功能描述]
  功能1: ffmpeg CLI 子进程调用与进度解析
  功能2: I 帧选择（-vf select=eq(pict_type,I)）
  功能3: 图像压缩到 ≤200KB（双参数压缩 + qscale 调整）
  功能4: 输出文件命名（video_id + timestamp + 保证唯一）
[输入输出]
  输入: video_path (str) 视频文件绝对路径; video_id (str) 视频 ID; count (int) 截图数量
  输出: list[ScreenshotFrame] 截图帧对象列表; total_size_kb (int) 压缩后总大小
[依赖关系]
  依赖文件: research_tool.datatypes (DE-008 ScreenshotFrame), research_tool.error_handler (M-010), research_tool.structured_logger (M-011)
  被依赖文件: research_tool.notes_schema (M-007 调用本模块嵌入 Markdown)
[注意事项]
  注意1: ffmpeg 失败必须静默跳过（不阻塞主链，E_FM_001 错误码登记）
  注意2: 压缩采用策略模式（双参数 + qscale 自适应）保证 ≤200KB
  注意3: 文件命名必须包含 video_id + 时间戳，并发场景下 ensure_unique 防覆盖
  注意4: 资源释放：subprocess 必须 wait() + close() 防止僵尸进程
  注意5: 跨模块调用：仅允许 M-007 调用本模块的 capture_screenshots 入口
[代码风格] 遵循 CS-001（Python 风格指南，4 空格缩进，Google Docstring，snake_case）
[创建日期] 2026-06-01
[修改历史]
  2026-06-01: DD-M-009 - 初始文件头注释 + 4 子模块类注释框架
[作者] DD-M-009-20260601
[来源标注] [DD-001:FS-009/MD-009/IC-025] [DD-M推断:基于 M-009 职责与 ffmpeg-python 最佳实践]
"""
# 标准库
from __future__ import annotations

import asyncio
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Optional

# 第三方
# 注：ffmpeg-python ≥ 0.2.0（TS-011）作为可选回退导入 [DD-M推断:策略模式双实现]
# 注释：ffmpeg-python 可能在某些平台不可用，因此 ffmpeg CLI 是主路径

# 本地
from research_tool.datatypes import ScreenshotFrame  # DE-008
from research_tool.error_handler import register_error  # M-010
from research_tool.structured_logger import emit_log  # M-011


# ===== 模块级常量（CS-001 UPPER_SNAKE_CASE）=====
DEFAULT_SCREENSHOT_COUNT: int = 5
"""默认截图数量（IC-025 约定）。"""
MAX_SCREENSHOT_SIZE_KB: int = 200
"""单张截图压缩后最大字节（200 KB）—— 见 MD-009 image_compressor。"""
DEFAULT_QSCALE: int = 2
"""ffmpeg mjpeg q 默认值（q=2 对应 ~80% 质量）。"""
MIN_QSCALE: int = 1
"""qscale 最小值（最高质量）。"""
MAX_QSCALE: int = 31
"""qscale 最大值（最低质量，> 10 时画质严重劣化）。"""
FFMPEG_TIMEOUT_SEC: int = 30
"""单次 ffmpeg 调用超时（防止视频损坏导致的死等）。"""
SCREENSHOT_DIR: str = "screenshots"
"""截图输出子目录名（相对工作目录）。"""
E_FM_001: str = "E_FM_001"
"""ffmpeg 失败错误码常量（IC-025 约定，静默跳过不阻塞主链）。"""


# ===== 子模块1: ffmpeg CLI 调用器 =====

class FFmpegInvoker:
    """ffmpeg CLI 子进程调用与进度解析。

    [类名] FFmpegInvoker
    [职责] ffmpeg/ffprobe 子进程调用与结果解析
    [关联设计规范] MD-009 子模块1 ffmpeg_invoker（来自 DD-001）
    [属性]
      属性1: ffmpeg_path (str) ffmpeg 可执行路径，默认从 PATH 解析
      属性2: ffprobe_path (str) ffprobe 可执行路径
      属性3: timeout_sec (int) 单次调用超时秒数
    [方法列表]
      方法1: invoke(args: list[str]) -> int - 执行 ffmpeg 并返回 exit code
      方法2: parse_progress(stderr_line: str) -> Optional[float] - 解析 -progress 行
      方法3: probe_duration(video_path: str) -> int - ffprobe 探测时长（秒）
    [状态机] N/A（无状态函数式工具）
    [异常处理]
      异常1: subprocess.TimeoutExpired - 触发条件: ffmpeg 30s 内未结束
      异常2: FileNotFoundError - 触发条件: ffmpeg_path 不可执行
    [并发安全] 否（每次调用创建独立 subprocess 对象）
    [来源标注] [DD-001:MD-009] [DD-M推断:基于 ffmpeg ≥ 6.0 官方文档]
    """

    ffmpeg_path: str
    ffprobe_path: str
    timeout_sec: int

    def __init__(self, ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe", timeout_sec: int = FFMPEG_TIMEOUT_SEC) -> None:
        """初始化 ffmpeg 调用器。

        [函数名] __init__
        [职责] 校验 ffmpeg 可用性并保存路径配置
        [参数说明]
          参数1: ffmpeg_path (str) 可选 "ffmpeg" ffmpeg 可执行路径
          参数2: ffprobe_path (str) 可选 "ffprobe" ffprobe 可执行路径
          参数3: timeout_sec (int) 可选 FFMPEG_TIMEOUT_SEC 子进程超时秒数
        [返回值]
          类型: None
          描述: 构造方法无返回值
        [错误码]
          错误码1: E_FM_001 ffmpeg 不可执行时登记
        [前置条件] ffmpeg ≥ 6.0 已安装（preflight 校验）
        [后置条件] 实例可调用 invoke/probe_duration
        [并发安全] 否
        [幂等性] 是
        [性能约束] 构造 < 10ms
        [来源标注] [DD-001:MD-009]
        """
        # 业务代码（DD-S 负责）
        ...

    def invoke(self, args: list[str]) -> int:
        """执行 ffmpeg 子进程调用。

        [函数名] invoke
        [职责] 构造 ffmpeg 命令并同步执行
        [关联接口契约] IC-025（间接通过 capture_screenshots）
        [参数说明]
          参数1: args (list[str]) 必填 无默认 ffmpeg 命令参数列表（不含 ffmpeg 自身）
                 校验规则: 必须非空；元素必须为字符串
        [返回值]
          类型: int
          描述: ffmpeg 进程 exit code（0 = 成功）
          特殊值: 非零值表示失败，需登记 E_FM_001
        [错误码]
          错误码1: E_FM_001 ffmpeg 失败（子进程 exit != 0）
          错误码2: E_FM_001 超时（subprocess.TimeoutExpired → kill）
        [前置条件] args 非空；ffmpeg_path 可执行
        [后置条件] 子进程已 wait() 并释放；stderr 已记录
        [并发安全] 否（每次调用独立）
        [幂等性] 否（不同视频/参数产生不同输出）
        [性能约束] 单次调用 < 30s（受 timeout_sec 约束）
        [示例]
          ```
          invoker = FFmpegInvoker()
          rc = invoker.invoke(["-i", "input.mp4", "-vf", "select=eq(pict_type,I)", "out_%03d.jpg"])
          ```
        [来源标注] [DD-001:MD-009/MD-002 subprocess 模式]
        """
        # 业务代码（DD-S 负责）
        ...

    def parse_progress(self, stderr_line: str) -> Optional[float]:
        """解析 ffmpeg -progress 输出行。

        [函数名] parse_progress
        [职责] 从 ffmpeg stderr 提取已处理时长（秒）
        [参数说明]
          参数1: stderr_line (str) 必填 无默认 ffmpeg -progress 单行输出
                 校验规则: 格式为 "key=value" 或 "out_time_ms=..."
        [返回值]
          类型: Optional[float]
          描述: 已处理时长（秒），None 表示该行不含进度信息
          特殊值: None 表示忽略行
        [错误码] -（纯解析函数，不抛错）
        [前置条件] stderr_line 非空
        [后置条件] 返回值 ≥ 0 或 None
        [并发安全] 是（无副作用）
        [幂等性] 是（相同输入始终返回相同输出）
        [性能约束] < 1ms
        [来源标注] [DD-001:MD-009] [DD-M推断:基于 ffmpeg -progress 协议]
        """
        # 业务代码（DD-S 负责）
        ...

    def probe_duration(self, video_path: str) -> int:
        """探测视频时长（秒）。

        [函数名] probe_duration
        [职责] 通过 ffprobe 探测视频时长
        [参数说明]
          参数1: video_path (str) 必填 无默认 视频文件绝对路径
                 校验规则: 路径存在；ffprobe 可读
        [返回值]
          类型: int
          描述: 视频时长（秒，四舍五入）
          特殊值: 0 表示探测失败
        [错误码]
          错误码1: E_FM_001 ffprobe 失败时返回 0
        [前置条件] video_path 存在且可读
        [后置条件] 返回非负整数
        [并发安全] 否
        [幂等性] 是（相同视频返回相同时长）
        [性能约束] < 1s
        [来源标注] [DD-001:MD-009]
        """
        # 业务代码（DD-S 负责）
        ...


# ===== 子模块2: I 帧选择器（策略模式：I 帧选择策略） =====

class IFrameSelector:
    """I 帧选择策略实现（策略模式）。

    [类名] IFrameSelector
    [职责] 通过 ffmpeg `-vf select=eq(pict_type,I)` 选择关键帧
    [关联设计规范] MD-009 子模块2 i_frame_selector（来自 DD-001）
    [属性]
      属性1: filter_args (list[str]) ffmpeg -vf 过滤器参数
      属性2: count (int) 期望抽取帧数
    [方法列表]
      方法1: select(video_path: str, count: int) -> list[str] - 返回抽取的帧文件路径列表
      方法2: count_frames(video_path: str) -> int - 探测视频总 I 帧数
      方法3: build_select_filter(count: int) -> str - 构造 select 表达式
    [状态机] N/A（无状态策略）
    [异常处理]
      异常1: ffmpeg 失败 → 静默返回空列表（E_FM_001 登记）
    [并发安全] 否
    [来源标注] [DD-001:MD-009] [调研:S-201] [DD-M推断:策略模式实现可替换 I 帧/均匀采样/场景检测]
    """

    filter_args: list[str]
    count: int

    def __init__(self, filter_args: Optional[list[str]] = None, count: int = DEFAULT_SCREENSHOT_COUNT) -> None:
        """初始化 I 帧选择器。

        [函数名] __init__
        [职责] 构造默认 I 帧选择策略
        [参数说明]
          参数1: filter_args (Optional[list[str]]) 可选 None 自定义 ffmpeg -vf 参数
          参数2: count (int) 可选 DEFAULT_SCREENSHOT_COUNT 期望帧数
        [返回值]
          类型: None
          描述: 构造方法无返回值
        [错误码] -
        [前置条件] ffmpeg ≥ 6.0 已安装
        [后置条件] 实例可调用 select/count_frames
        [并发安全] 否
        [幂等性] 是
        [性能约束] 构造 < 1ms
        [来源标注] [DD-001:MD-009]
        """
        # 业务代码（DD-S 负责）
        ...

    def select(self, video_path: str, count: int) -> list[str]:
        """选择 I 帧并写入临时文件。

        [函数名] select
        [职责] 调用 ffmpeg 抽取 count 个 I 帧，返回文件路径列表
        [关联接口契约] IC-025
        [参数说明]
          参数1: video_path (str) 必填 无默认 视频文件绝对路径
          参数2: count (int) 必填 无默认 期望帧数（1-10）
                 校验规则: 1 ≤ count ≤ 10
        [返回值]
          类型: list[str]
          描述: 抽取的 I 帧文件绝对路径列表
          特殊值: 空列表表示 ffmpeg 失败（静默）
        [错误码]
          错误码1: E_FM_001 ffmpeg 失败 → 静默返回 []
        [前置条件] video_path 存在；ffmpeg ≥ 6.0
        [后置条件] 临时帧文件已写入；返回路径列表（失败时为空）
        [并发安全] 否（外部 Semaphore(3) 保护）
        [幂等性] 是（相同 video + count 产生相同结果）
        [性能约束] 1-3s（30s 视频）
        [示例]
          ```
          selector = IFrameSelector()
          paths = selector.select("/path/to/video.mp4", 5)
          # paths == ["/tmp/frame_001.jpg", ..., "/tmp/frame_005.jpg"]
          ```
        [来源标注] [DD-001:MD-009/IC-025] [DD-M推断:基于 ffmpeg -vf select=eq(pict_type,I) 官方文档]
        """
        # 业务代码（DD-S 负责）
        ...

    def count_frames(self, video_path: str) -> int:
        """探测视频总 I 帧数。

        [函数名] count_frames
        [职责] 通过 ffprobe 统计视频中 I 帧总数
        [参数说明]
          参数1: video_path (str) 必填 无默认 视频文件绝对路径
        [返回值]
          类型: int
          描述: I 帧总数
          特殊值: 0 表示探测失败
        [错误码]
          错误码1: E_FM_001 ffprobe 失败 → 返回 0
        [前置条件] video_path 存在
        [后置条件] 返回非负整数
        [并发安全] 否
        [幂等性] 是
        [性能约束] < 2s
        [来源标注] [DD-001:MD-009]
        """
        # 业务代码（DD-S 负责）
        ...

    def build_select_filter(self, count: int) -> str:
        """构造 ffmpeg select 过滤器表达式。

        [函数名] build_select_filter
        [职责] 生成 `-vf select='eq(pict_type,I),...'` 字符串
        [参数说明]
          参数1: count (int) 必填 无默认 期望帧数
                 校验规则: ≥ 1
        [返回值]
          类型: str
          描述: ffmpeg -vf 过滤器参数（不含 -vf 前缀）
        [错误码] -
        [前置条件] count ≥ 1
        [后置条件] 返回的表达式可被 ffmpeg 解析
        [并发安全] 是
        [幂等性] 是
        [性能约束] < 1ms
        [来源标注] [DD-001:MD-009] [DD-M推断:均匀采样公式 ceil(N/count)]
        """
        # 业务代码（DD-S 负责）
        ...


# ===== 子模块3: 图像压缩器（策略模式：压缩策略） =====

class ImageCompressor:
    """图像压缩策略实现（策略模式）。

    [类名] ImageCompressor
    [职责] 将抽取的 I 帧压缩到 ≤200KB
    [关联设计规范] MD-009 子模块3 image_compressor（来自 DD-001）
    [属性]
      属性1: max_size_kb (int) 单张目标最大字节数（默认 200KB）
      属性2: default_qscale (int) ffmpeg mjpeg q 默认值
    [方法列表]
      方法1: compress(input_path: str, output_path: str) -> int - 压缩并返回字节数
      方法2: adjust_qscale(current_size: int, current_q: int) -> int - 自适应 qscale
      方法3: needs_recompress(size_bytes: int) -> bool - 判断是否需要重新压缩
    [状态机]
      初始 q=2 → [compress] → 检查大小
      大小 ≤ 200KB → DONE
      大小 > 200KB → 提升 qscale（adjust_qscale）→ RECOMPRESS → DONE
    [异常处理]
      异常1: ffmpeg 失败 → 静默跳过（不阻塞主链）
    [并发安全] 否
    [来源标注] [DD-001:MD-009] [DD-M推断:策略模式可替换为 PIL/Pillow/cv2 实现]
    """

    max_size_kb: int
    default_qscale: int

    def __init__(self, max_size_kb: int = MAX_SCREENSHOT_SIZE_KB, default_qscale: int = DEFAULT_QSCALE) -> None:
        """初始化图像压缩器。

        [函数名] __init__
        [职责] 配置压缩阈值与初始 qscale
        [参数说明]
          参数1: max_size_kb (int) 可选 MAX_SCREENSHOT_SIZE_KB 单张目标最大字节数
          参数2: default_qscale (int) 可选 DEFAULT_QSCALE 初始 qscale 值
        [返回值]
          类型: None
          描述: 构造方法无返回值
        [错误码] -
        [前置条件] max_size_kb ≥ 1；1 ≤ default_qscale ≤ MAX_QSCALE
        [后置条件] 实例可调用 compress
        [并发安全] 否
        [幂等性] 是
        [性能约束] 构造 < 1ms
        [来源标注] [DD-001:MD-009]
        """
        # 业务代码（DD-S 负责）
        ...

    def compress(self, input_path: str, output_path: str) -> int:
        """压缩单张图像到 ≤max_size_kb。

        [函数名] compress
        [职责] 调用 ffmpeg mjpeg 编码并自适应调整 qscale
        [关联接口契约] IC-025
        [参数说明]
          参数1: input_path (str) 必填 无默认 输入 I 帧路径
          参数2: output_path (str) 必填 无默认 输出压缩图像路径
        [返回值]
          类型: int
          描述: 输出文件字节数
          特殊值: 0 表示 ffmpeg 失败
        [错误码]
          错误码1: E_FM_001 ffmpeg 失败 → 返回 0
        [前置条件] input_path 存在；输出目录可写
        [后置条件] output_path 文件存在且 ≤ max_size_kb × 1024
        [并发安全] 否（外部 Semaphore 保护）
        [幂等性] 是（相同输入 + qscale 产生相同输出）
        [性能约束] < 500ms/帧
        [示例]
          ```
          compressor = ImageCompressor()
          size = compressor.compress("/tmp/frame_001.jpg", "screenshots/abc123_t1.jpg")
          assert size <= 200 * 1024
          ```
        [来源标注] [DD-001:MD-009/IC-025]
        """
        # 业务代码（DD-S 负责）
        ...

    def adjust_qscale(self, current_size: int, current_q: int) -> int:
        """自适应调整 qscale。

        [函数名] adjust_qscale
        [职责] 当前文件超限时，增大 qscale 降低画质以缩小体积
        [参数说明]
          参数1: current_size (int) 必填 无默认 当前文件字节数
          参数2: current_q (int) 必填 无默认 当前 qscale 值
        [返回值]
          类型: int
          描述: 调整后的 qscale 值
          特殊值: MAX_QSCALE 表示已达上限（继续增大画质不可接受）
        [错误码] -
        [前置条件] current_size > 0；MIN_QSCALE ≤ current_q ≤ MAX_QSCALE
        [后置条件] 返回值 ≥ current_q（qscale 单调递增）
        [并发安全] 是
        [幂等性] 是
        [性能约束] < 1ms
        [来源标注] [DD-001:MD-009] [DD-M推断:策略为 q += 1（线性）]
        """
        # 业务代码（DD-S 负责）
        ...

    def needs_recompress(self, size_bytes: int) -> bool:
        """判断是否需要重新压缩。

        [函数名] needs_recompress
        [职责] 比较当前大小与阈值
        [参数说明]
          参数1: size_bytes (int) 必填 无默认 当前文件字节数
        [返回值]
          类型: bool
          描述: True = 需要重新压缩（size_bytes > max_size_kb × 1024）
        [错误码] -
        [前置条件] size_bytes ≥ 0
        [后置条件] 返回值与 size_bytes 同步
        [并发安全] 是
        [幂等性] 是
        [性能约束] < 1ms
        [来源标注] [DD-001:MD-009]
        """
        # 业务代码（DD-S 负责）
        ...


# ===== 子模块4: 输出文件命名器 =====

class OutputNamer:
    """截图输出文件命名器。

    [类名] OutputNamer
    [职责] 生成视频截图文件名（video_id + timestamp + 唯一性保证）
    [关联设计规范] MD-009 子模块4 output_namer（来自 DD-001）
    [属性]
      属性1: pattern (str) 命名模板，默认 "{video_id}_{timestamp}_{idx:03d}.jpg"
      属性2: base_dir (Path) 截图输出根目录
    [方法列表]
      方法1: generate_name(video_id: str, timestamp_sec: int, idx: int) -> str - 生成单个文件名
      方法2: ensure_unique(name: str) -> str - 并发场景下保证文件名唯一
    [状态机] N/A
    [异常处理]
      异常1: 文件名冲突 → ensure_unique 自增后缀
    [并发安全] 是（ensure_unique 基于 os.stat 检查）
    [来源标注] [DD-001:MD-009] [DD-M推断:基于时间戳 + 索引的命名约定]
    """

    pattern: str
    base_dir: Path

    def __init__(self, base_dir: str = SCREENSHOT_DIR, pattern: str = "{video_id}_{timestamp}_{idx:03d}.jpg") -> None:
        """初始化文件命名器。

        [函数名] __init__
        [职责] 配置命名模板与输出目录
        [参数说明]
          参数1: base_dir (str) 可选 SCREENSHOT_DIR 输出根目录名
          参数2: pattern (str) 可选默认模板 命名模板（含 {video_id}/{timestamp}/{idx} 占位符）
        [返回值]
          类型: None
          描述: 构造方法无返回值
        [错误码] -
        [前置条件] base_dir 字符串合法；pattern 包含必要占位符
        [后置条件] 实例可调用 generate_name
        [并发安全] 否
        [幂等性] 是
        [性能约束] 构造 < 1ms
        [来源标注] [DD-001:MD-009]
        """
        # 业务代码（DD-S 负责）
        ...

    def generate_name(self, video_id: str, timestamp_sec: int, idx: int) -> str:
        """生成单个截图文件名。

        [函数名] generate_name
        [职责] 按 pattern 模板生成文件名
        [参数说明]
          参数1: video_id (str) 必填 无默认 视频 ID
                 校验规则: 非空；不含路径分隔符
          参数2: timestamp_sec (int) 必填 无默认 帧时间戳（秒）
          参数3: idx (int) 必填 无默认 帧索引（1-based）
        [返回值]
          类型: str
          描述: 截图文件名（不含路径）
        [错误码] -
        [前置条件] video_id 合法；timestamp_sec ≥ 0；idx ≥ 1
        [后置条件] 返回文件名不含路径分隔符
        [并发安全] 是（纯函数）
        [幂等性] 是
        [性能约束] < 1ms
        [示例]
          ```
          namer = OutputNamer()
          name = namer.generate_name("abc123", 10, 1)
          # name == "abc123_10_001.jpg"
          ```
        [来源标注] [DD-001:MD-009]
        """
        # 业务代码（DD-S 负责）
        ...

    def ensure_unique(self, name: str) -> str:
        """保证文件名唯一（并发安全）。

        [函数名] ensure_unique
        [职责] 检查文件是否存在，存在则追加 _N 后缀
        [参数说明]
          参数1: name (str) 必填 无默认 候选文件名
        [返回值]
          类型: str
          描述: 不冲突的文件名
        [错误码] -
        [前置条件] base_dir 已创建
        [后置条件] 返回的路径 os.path.exists() == False
        [并发安全] 是（基于 os.path.exists TOCTOU 风险由 Semaphore 上层保证）[DD-M推断:接受轻量 TOCTOU]
        [幂等性] 否（多次调用可能产生不同后缀）
        [性能约束] < 5ms
        [来源标注] [DD-001:MD-009]
        """
        # 业务代码（DD-S 负责）
        ...


# ===== 模块顶层入口函数（IC-025 对外 API）=====

def capture_screenshots(video_path: str, video_id: str, count: int = DEFAULT_SCREENSHOT_COUNT) -> list[ScreenshotFrame]:
    """M-009 对外主入口：截取视频的 I 帧并压缩。

    [函数名] capture_screenshots
    [职责] 编排 I 帧选择 + 压缩 + 命名产出截图列表
    [关联接口契约] IC-025（API-025）
    [参数说明]
      参数1: video_path (str) 必填 无默认 视频文件绝对路径
             校验规则: 路径存在；格式为 mp4/webm/mkv
      参数2: video_id (str) 必填 无默认 视频唯一 ID
             校验规则: 非空；不含路径分隔符
      参数3: count (int) 可选 DEFAULT_SCREENSHOT_COUNT 期望截图数量（1-10）
    [返回值]
      类型: list[ScreenshotFrame]
      描述: 截图帧对象列表
      特殊值: 空列表表示 ffmpeg 失败（静默跳过，不抛错）
    [错误码]
      错误码1: E_FM_001 ffmpeg 失败 → 返回空列表（静默）
    [前置条件] ffmpeg ≥ 6.0 已安装；video_path 存在
    [后置条件] frames 长度 ≤ count；每个 frame.path 存在
    [并发安全] 是（外部 Semaphore(3) 保护，详见 M-012）
    [幂等性]
      是否幂等: 是
      幂等键来源: video_id
      幂等有效期: 永久
      重复请求处理: 覆盖写入同名截图
    [性能约束] 1-3s（30s 视频，count=5）
    [示例]
      ```
      frames = capture_screenshots("/path/to/abc123.mp4", "abc123", count=5)
      # frames == [ScreenshotFrame(path=..., timestamp_sec=2, size_kb=180, idx=1), ...]
      ```
    [来源标注] [DD-001:MD-009/IC-025]
    """
    # 业务代码（DD-S 负责）
    ...


def invoke_ffmpeg(args: list[str]) -> int:
    """模块级 ffmpeg 调用快捷函数。

    [函数名] invoke_ffmpeg
    [职责] 包装 FFmpegInvoker.invoke 供模块外调用
    [参数说明]
      参数1: args (list[str]) 必填 无默认 ffmpeg 参数列表
    [返回值]
      类型: int
      描述: ffmpeg exit code（0 = 成功）
    [错误码]
      错误码1: E_FM_001 ffmpeg 失败
    [前置条件] args 非空
    [后置条件] ffmpeg 进程已 wait
    [并发安全] 否
    [幂等性] 否
    [性能约束] < 30s
    [来源标注] [DD-001:MD-009]
    """
    # 业务代码（DD-S 负责）
    ...


def select_i_frames(video_path: str, count: int) -> list[str]:
    """模块级 I 帧选择快捷函数。

    [函数名] select_i_frames
    [职责] 包装 IFrameSelector.select 供模块外调用
    [参数说明]
      参数1: video_path (str) 必填 无默认 视频路径
      参数2: count (int) 必填 无默认 帧数
    [返回值]
      类型: list[str]
      描述: I 帧文件路径列表
    [错误码]
      错误码1: E_FM_001 ffmpeg 失败 → 返回 []
    [前置条件] video_path 存在
    [后置条件] 返回临时文件路径
    [并发安全] 否
    [幂等性] 是
    [性能约束] 1-3s
    [来源标注] [DD-001:MD-009]
    """
    # 业务代码（DD-S 负责）
    ...


def compress_image(input_path: str, output_path: str) -> int:
    """模块级图像压缩快捷函数。

    [函数名] compress_image
    [职责] 包装 ImageCompressor.compress 供模块外调用
    [参数说明]
      参数1: input_path (str) 必填 无默认 输入图像路径
      参数2: output_path (str) 必填 无默认 输出图像路径
    [返回值]
      类型: int
      描述: 输出文件字节数
    [错误码]
      错误码1: E_FM_001 失败 → 返回 0
    [前置条件] input_path 存在
    [后置条件] output_path 存在且 ≤ 200KB
    [并发安全] 否
    [幂等性] 是
    [性能约束] < 500ms
    [来源标注] [DD-001:MD-009]
    """
    # 业务代码（DD-S 负责）
    ...


# ===== 模块公开接口声明（__all__）=====
__all__: list[str] = [
    "FFmpegInvoker",
    "IFrameSelector",
    "ImageCompressor",
    "OutputNamer",
    "capture_screenshots",
    "invoke_ffmpeg",
    "select_i_frames",
    "compress_image",
    "DEFAULT_SCREENSHOT_COUNT",
    "MAX_SCREENSHOT_SIZE_KB",
    "E_FM_001",
    "SCREENSHOT_DIR",
]
"""模块公开 API 清单——供外部 from research_tool.ffmpeg_wrapper import ... 使用。
[DD-M推断:基于"模块边界清晰 + 显式公开"原则，避免 from xxx import *]"""
