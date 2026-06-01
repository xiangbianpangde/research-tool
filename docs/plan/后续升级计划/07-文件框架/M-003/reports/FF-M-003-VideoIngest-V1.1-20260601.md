# 文件框架结构 — M-003（DD-M-003）

> **生成方**：DD-M-003
> **日期**：2026-06-01
> **负责模块**：M-003 下载器
> **依据**：[DD-001:FS-VideoIngest-V1.1 §M-003] [DD-001:MD-VideoIngest-V1.1 §M-003]

---

## 模块编号
M-003

## 模块名称
downloader（下载器）

## 文件框架

```
产出物/07-文件框架/M-003/
├── research_tool/
│   ├── downloader.py                       ← [职责：yt-dlp 包装 + 5 子模块（version_validator/youtube/bilibili/local/cookie）]
│   │   ├── [文件头注释: 完整]
│   │   ├── [模块常量: YTDLP_MIN_VERSION / PLATFORM_* / E_DL_* 等 11 项]
│   │   ├── [异常类: DownloadError]
│   │   ├── [类1: YtDlpVersionValidator + 3 方法注释]
│   │   ├── [类2: YouTubeDownloader + 3 方法注释]
│   │   ├── [类3: BilibiliDownloader + 3 方法注释]
│   │   ├── [类4: LocalFileResolver + 3 方法注释]
│   │   ├── [类5: CookieInjector + 2 方法注释]
│   │   ├── [模块函数: validate_yt_dlp_version / download_youtube / download_bilibili / resolve_local / inject_cookie 共 5 个]
│   │   └── [main: CLI 调试入口]
│   └── tests/
│       └── test_downloader.py              ← [职责：M-003 单元测试 + 集成测试]
│           ├── [文件头注释: 完整]
│           ├── [4 个 pytest fixtures: sample_ytdlp_version / sample_video_url / cookie_file_0o600 / local_mp4_file]
│           ├── [测试类1: TestYtDlpVersionValidator - 3 测试场景]
│           ├── [测试类2: TestCookieInjector - 2 测试场景]
│           ├── [测试类3: TestLocalFileResolver - 3 测试场景]
│           ├── [测试类4: TestYouTubeDownloader - 2 测试场景]
│           ├── [测试类5: TestBilibiliDownloader - 2 测试场景]
│           └── [测试类6: TestDownloaderIntegration - 1 集成测试场景 + @pytest.mark.integration + @pytest.mark.slow]
└── reports/
    ├── FF-M-003-VideoIngest-V1.1-20260601.md  ← 本文件
    ├── API-M-003-VideoIngest-V1.1-20260601.md
    ├── FC-M-003-VideoIngest-V1.1-20260601.md
    ├── FDR-M-003-VideoIngest-V1.1-20260601.md
    └── FH-M-003-VideoIngest-V1.1-20260601.md
```

## 文件间依赖关系

```
downloader.py
  ├─→ datatypes.py (DE-001 VideoURL, DE-005 DownloadTask)  [类型]
  ├─→ cache_manager.py (M-004 缓存查询 IC-009)              [可选查询]
  ├─→ error_handler.py (M-010 错误码登记 IC-026)            [错误上报]
  └─→ structured_logger.py (M-011 JSON 日志 IC-028)        [日志]

test_downloader.py
  └─→ downloader.py                                          [被测]
```

依赖关系 DAG，无环检测通过 [soul §4.15]。

## 多方案对比（soul §4.11）

### 方案 A（主方案 · 单文件聚合）
- **结构**：`downloader.py` 单文件 + `test_downloader.py` 单文件
- **优点**：
  - 严格符合 DD-001 FS §M-003 的"2 层目录 + 单模块文件"规范
  - 5 子模块聚合在一个文件，类之间共享私有工具函数方便
  - 文件命名 100% 合规（snake_case / test_<module>.py）
  - DD-S 搭骨架时只需创建 2 个文件，最少开销
- **缺点**：
  - 单文件代码量较大（5 类 + 5 函数 + 测试）
  - 模块边界由类内 `_` 私有成员隔离

### 方案 B（备选方案 · 子包拆分）
- **结构**：
  ```
  downloader/
    __init__.py
    version_validator.py
    youtube.py
    bilibili.py
    local.py
    cookie.py
  tests/test_downloader.py
  ```
- **优点**：
  - 文件职责更细（每文件 1 个类）
  - 单独测试某个适配器更清晰
- **缺点**：
  - **违反 DD-001 FS §M-003 单文件规范**
  - 增加 `__init__.py` 复杂度和跨文件依赖
  - 5 个文件的导入路径管理复杂（多实例隔离风险高）
  - DD-001 已明确 `research_tool/downloader.py`，变更需 DD-001 协调

### 对比评估

| 维度 | 权重 | 方案 A | 方案 B |
|------|------|--------|--------|
| 文件结构合规度（对齐 DD-001 FS） | 0.22 | 9.5 | 5.0 |
| 注释完整度 | 0.22 | 9.0 | 8.5 |
| 接口契约注释化完整度 | 0.18 | 9.5 | 9.0 |
| 代码风格合规度 | 0.13 | 9.5 | 9.0 |
| 设计可追溯性 | 0.13 | 9.0 | 7.0 |
| 文件框架可追溯性 | 0.12 | 9.5 | 8.0 |
| **总分** | 1.00 | **9.34** | **7.55** |

### 选择结果
**主方案总分（9.34） - 备选方案总分（7.55） = 1.79 < 5**

按 soul §4.11 选择规则：差距 < 5 → 需额外标注"方案 A 更适合 X 场景，方案 B 更适合 Y 场景"。

**额外标注**：
- 方案 A 更适合：5 子模块 + 1 模块文件的紧凑型模块（当前 M-003 场景）
- 方案 B 更适合：M-005 转写器（6 子模块、whisper/bcut/groq 三个独立引擎，文件可能超过 800 行需拆分）

**最终选择**：方案 A（主方案）。理由：
1. **严格对齐 DD-001 FS §M-003 单文件规范**（R1/R5 禁止自行发明）
2. 5 个类共约 15 方法，代码量可控（预估 < 600 行）
3. 单文件 + 单测试文件，最小化多实例隔离风险（R28）

## 阶梯退出检查（soul §4.3 L0）
- ① 分配模块 M-003 已分类到框架主题：✓（业务逻辑型）
- ② 该模块的 FS 已识别：✓（FS §M-003）
- ③ D1 ≥ 70：✓（D1 = 100%）

## DD-M 洞察

**洞察 1** [类型: 跨模块依赖未标注]
M-003 的 downloader.py 调用 M-004 cache_manager 进行缓存查询（命中时短路下载），文件头注释的"依赖关系"已标注 M-004，但未明确"调用时机"（仅在 youtube/bilibili download 时调用，resolve_local 不调用）。建议 DD-S 在实现时增加一行调用时序注释 [DD-M推断: 性能优化角度]。

**洞察 2** [类型: 异常处理未标注]
模块定义了 DownloadError 领域异常 + 6 个 E_DL_* 错误码常量，但 M-003 与 M-010 的对接约定（哪些错误码由 M-010 统一登记）尚未在文件头注释体现。建议在 downloader.py 顶部 docstring 明确"所有 DownloadError → M-010 register_error"。

**洞察 3** [类型: 类型注解缺失风险]
5 个模块函数的类型注解完整（`url: VideoURL` / `args: list[str]` / `path: str`），但 `_build_args` / `_handle_403` 等私有方法的返回类型 `list[str]` 长度不可预测（依赖 url）。[DD-M推断: 可考虑用 `tuple[str, ...]` 替代，但保留 list 便于 mock 测试时的可变行为]。

## 来源标注
[DD-001:FS-VideoIngest-V1.1 §M-003] [DD-001:MD-VideoIngest-V1.1 §M-003]
