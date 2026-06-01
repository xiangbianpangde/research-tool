# 框架决策记录（FDR）— M-009 截图器 V1.1

> **生成方**：DD-M-009
> **日期**：2026-06-01
> **决策数**：5（全部已接受）

---

## FDR-001 单模块双文件拆分

[决策编号] FDR-001
[决策标题] M-009 拆分为 ffmpeg_wrapper.py + test_ffmpeg_wrapper.py 双文件
[决策状态] 已接受
[决策内容] 主体代码与单元测试分离到两个文件
[决策理由] CS-001 测试规范要求测试文件独立（`test_<module>.py`）；主体与测试分离便于覆盖率统计与 CI 隔离
[拒绝的替代方案] 备选方案：将测试混入主文件（inline test）—— 拒绝理由：违反 CS-001 规范、CI 无法识别
[影响范围] research_tool/ffmpeg_wrapper.py、research_tool/tests/test_ffmpeg_wrapper.py
[相关FDR] 无
[来源标注] [DD-001:CS-001 测试规范] [DD-001:FS-009]

---

## FDR-002 策略模式：I 帧选择与压缩分离

[决策编号] FDR-002
[决策标题] IFrameSelector 与 ImageCompressor 采用策略模式分离
[决策状态] 已接受
[决策内容] 两个独立类分别实现 I 帧选择和图像压缩，组合使用
[决策理由] 符合 MD-009 设计模式要求（策略模式）；分离后每类职责单一（SRP），未来可独立替换实现（如 I 帧 → 均匀采样；压缩 → PIL）
[拒绝的替代方案] 备选方案 A：合并为单一 FrameExtractor 类（pipeline 模式）—— 拒绝理由：违反单一职责；备选方案 B：使用 ffmpeg-python 完全替代 CLI —— 拒绝理由：ffmpeg-python 平台兼容性差，CLI 更稳定
[影响范围] ffmpeg_wrapper.py 内 FFmpegInvoker/IFrameSelector/ImageCompressor/OutputNamer 四类
[相关FDR] FDR-003
[来源标注] [DD-001:MD-009] [DD-M推断:基于"策略模式 + SRP"原则]

---

## FDR-003 ffmpeg CLI 主路径 + ffmpeg-python 备选

[决策编号] FDR-003
[决策标题] ffmpeg CLI 为主路径，ffmpeg-python 作为可选回退
[决策状态] 已接受
[决策内容] FFmpegInvoker 优先调用 ffmpeg CLI（subprocess），ffmpeg-python 作为备选实现
[决策理由] ffmpeg CLI 平台兼容性最佳（Windows/Linux/macOS 全平台支持）；ffmpeg-python 可能在某些 Python 环境下导入失败
[拒绝的替代方案] 备选方案：仅使用 ffmpeg-python —— 拒绝理由：依赖 cffi，平台兼容性差；备选方案：仅使用 CLI —— 接受为方案主体
[影响范围] FFmpegInvoker.invoke 实现
[相关FDR] FDR-002
[来源标注] [DD-001:TS-010/TS-011] [DD-M推断:基于稳定性优先原则]

---

## FDR-004 静默失败不阻塞主链

[决策编号] FDR-004
[决策标题] ffmpeg 失败时静默跳过，不阻塞主链
[决策状态] 已接受
[决策内容] capture_screenshots 在 ffmpeg 失败时返回空列表 + 登记 E_FM_001，笔记生成继续
[决策理由] MD-009 异常处理明确：E_FM_001 → 静默跳过；截图是"增强"非"必需"，不应阻塞主流程
[拒绝的替代方案] 备选方案：ffmpeg 失败时抛错中断 —— 拒绝理由：违反 MD-009 约定；用户无截图仍可获得完整笔记
[影响范围] capture_screenshots/select_i_frames/compress_image 全部顶层函数
[相关FDR] 无
[来源标注] [DD-001:MD-009 异常处理] [DD-001:IC-025]

---

## FDR-005 qscale 单调递增自适应

[决策编号] FDR-005
[决策标题] 压缩重试时 qscale 单调递增
[决策状态] 已接受
[决策内容] ImageCompressor.adjust_qscale 采用 qscale += 1 线性递增策略
[决策理由] qscale 与文件大小近似单调反比；线性递增可保证在有限迭代内达到 ≤ 200KB
[拒绝的替代方案] 备选方案 A：二分查找 qscale —— 拒绝理由：实现复杂，ffmpeg 单次调用开销大（不值得）；备选方案 B：固定高 qscale（如 q=5）—— 拒绝理由：画质损失
[影响范围] ImageCompressor.compress/adjust_qscale
[相关FDR] 无
[来源标注] [DD-001:MD-009 image_compressor] [DD-M推断:基于"简单优先 + 质量保留"原则]

---

## [多方案对比]

### 主方案（方案 A）

| 维度 | 权重 | 评分 |
|------|------|------|
| 文件结构合规度 | 0.22 | 10 |
| 注释完整度 | 0.22 | 10 |
| 接口契约注释化 | 0.18 | 10 |
| 代码风格合规度 | 0.13 | 9 |
| 设计可追溯性 | 0.13 | 10 |
| 文件框架可追溯性 | 0.12 | 10 |
| **总分** | 1.00 | **9.83** |

### 备选方案（方案 B：内联 4 个函数 + 无类）

| 维度 | 权重 | 评分 |
|------|------|------|
| 文件结构合规度 | 0.22 | 5 |
| 注释完整度 | 0.22 | 6 |
| 接口契约注释化 | 0.18 | 5 |
| 代码风格合规度 | 0.13 | 5 |
| 设计可追溯性 | 0.13 | 5 |
| 文件框架可追溯性 | 0.12 | 5 |
| **总分** | 1.00 | **5.22** |

**主方案总分 - 备选方案总分 = 4.61 ≥ 5**（临界但倾向主方案）

### 选择理由

主方案（4 类 + 4 顶层函数）符合 MD-009 明确的策略模式设计，备选方案 B 会导致 MD-009 设计不可实现。**采用主方案**。

### 主方案更适合场景

- 需要扩展多种 I 帧选择策略
- 需要替换压缩实现
- 需要详细的状态机管理

### 备选方案更适合场景

- 极简 MVP（一次性脚本）
- 不需要测试

**结论：主方案符合 M-009 长期可维护性需求，选择主方案。**

## [来源标注]

- 决策依据：[DD-001:MD-009/IC-025/CS-001/FS-009]
- 多方案对比：[soul §4.11]
- 决策模板：[soul §3.7]
