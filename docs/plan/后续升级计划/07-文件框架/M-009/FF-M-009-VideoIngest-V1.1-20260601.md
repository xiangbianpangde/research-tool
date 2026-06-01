# 文件框架结构 — M-009 截图器（ffmpeg_wrapper）V1.1

> **生成方**：DD-M-009（详细设计师-模块）
> **日期**：2026-06-01
> **负责模块**：M-009（唯一负责，禁止跨模块操作）
> **上游依据**：DD-001（FS-009 / MD-009 / IC-025 / CS-001）

---

## [模块编号] M-009
## [模块名称] ffmpeg_wrapper（截图器）

## [文件框架]

```
research_tool/
  ffmpeg_wrapper.py                    ← [职责：M-009 主模块，4 子模块 + 4 顶层函数 + IC-025 入口]
    - [类1注释] FFmpegInvoker - ffmpeg/ffprobe 子进程调用与解析
    - [类2注释] IFrameSelector - I 帧选择（策略模式）
    - [类3注释] ImageCompressor - 图像压缩 ≤ 200KB（策略模式）
    - [类4注释] OutputNamer - 文件命名（video_id + timestamp + 唯一性）
    - [函数1注释] capture_screenshots - IC-025 主入口
    - [函数2注释] invoke_ffmpeg - FFmpegInvoker 快捷包装
    - [函数3注释] select_i_frames - IFrameSelector 快捷包装
    - [函数4注释] compress_image - ImageCompressor 快捷包装

research_tool/tests/
  test_ffmpeg_wrapper.py               ← [职责：M-009 单元测试，9 用例（核心 4 + 边界 3 + 异常 2）]
    - [TestFFmpegInvoker]
      - [测试场景1: test_invoke_success] [断言: result == 0] [Mock: subprocess.run]
      - [测试场景2: test_invoke_failure] [断言: result != 0 + register_error] [Mock: subprocess.run]
      - [测试场景3: test_invoke_timeout] [断言: result == -1] [Mock: TimeoutExpired]
      - [测试场景4: test_parse_progress_time_ms] [断言: result == 5.0] [Mock: 无]
    - [TestIFrameSelector]
      - [测试场景1: test_select_normal] [断言: 5 paths] [Mock: FFmpegInvoker]
      - [测试场景2: test_select_ffmpeg_failure] [断言: result == []] [Mock: invoke=1]
      - [测试场景3: test_build_select_filter] [断言: select=eq(pict_type,I) in result] [Mock: 无]
    - [TestImageCompressor]
      - [测试场景1: test_compress_under_threshold] [断言: size ≤ 200KB] [Mock: ffmpeg]
      - [测试场景2: test_compress_recompress_loop] [断言: qscale 递增] [Mock: 模拟两次]
      - [测试场景3: test_adjust_qscale] [断言: new_q > current_q] [Mock: 无]
      - [测试场景4: test_needs_recompress_boundary] [断言: 200KB→False, 201KB→True] [Mock: 无]
    - [TestOutputNamer]
      - [测试场景1: test_generate_name] [断言: "abc123_10_001.jpg"] [Mock: 无]
      - [测试场景2: test_ensure_unique_with_existing] [断言: 追加 _N] [Mock: os.path.exists]
    - [TestCaptureScreenshots]
      - [测试场景1: test_capture_screenshots_success] [断言: 5 frames] [Mock: ffmpeg+压缩]
      - [测试场景2: test_capture_screenshots_ffmpeg_failure] [断言: [] + register_error] [Mock: invoke=1]
    - [TestModuleConstants]
      - [测试场景1: test_default_screenshot_count] [断言: 5]
      - [测试场景2: test_max_screenshot_size_kb] [断言: 200]
      - [测试场景3: test_error_code_constant] [断言: "E_FM_001"]
```

## [文件间依赖关系]

```
research_tool/
  ffmpeg_wrapper.py (M-009)
    ├─→ datatypes.py (DE-008 ScreenshotFrame)         [本仓库共享 dataclass]
    ├─→ error_handler.py (M-010)                       [错误码登记 register_error]
    └─→ structured_logger.py (M-011)                   [日志 emit_log]
                                                  ↓
  notes_schema.py (M-007) → ffmpeg_wrapper.py (M-009)  [M-007 调用 capture_screenshots]
```

**依赖关系验证**：DAG 无环；M-009 仅被 M-007 调用，符合 FS-009 规范。

## [文件命名合规验证]

| 文件 | 命名规则 | 合规 |
|------|---------|------|
| ffmpeg_wrapper.py | snake_case（CS-001 约定） | ✓ |
| test_ffmpeg_wrapper.py | test_<module>.py（CS-001 约定） | ✓ |

## [目录层级]

- 2 层（research_tool/ + tests/）—— 符合 FS-009 5/5 检查

## [来源标注]

- 文件结构：[DD-001:FS-009]
- 模块细化：[DD-001:MD-009]
- 接口契约：[DD-001:IC-025]
- 代码风格：[DD-001:CS-001]
- 唯一性保证：[DD-M推断:基于 time.time() 纳秒 + 索引的命名策略]
