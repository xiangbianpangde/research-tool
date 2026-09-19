# 异常分支演练 — VideoIngest V1.1（第⑧棒 系统模拟运行）

> **项目**：VideoIngest V1.1
> **模拟日期**：2026-06-01
> **覆盖异常**：3 个最有代表性的异常场景（资源类 1 + 通信类 1 + 模型类 1）
> **挑选依据**：EX-001~EX-045 中按"代表性强、覆盖 BP-002/003/007 三个核心流程、覆盖 NFR3（错误处理）三条硬约束"选取
> **来源标注**：[PRD-V1.1:F-NNN] = 01-需求澄清 PRD V1.1 功能编号；[EX-NNN] = 03-逻辑梳理 异常场景；[调研-V1.0:S-NNN] = 02-调研验证 调研建议

---

## 0. 选取标准

| 异常编号 | 类别 | 流程 | 选取理由 |
|----------|------|------|----------|
| **EX-004** | 资源类 | BP-002 preflight | V1.1 新增（S-002），YouTube 唯一硬阻塞入口；NFR3"明确报错"最典型 |
| **EX-008** | 通信类 | BP-003 downloader | 真实生产环境最高频异常（4K/付费/限地区）；NFR3"现象+原因+下一步"三段式最完整 |
| **EX-025** | 模型类 | BP-007 notes_schema | V1.1 新增（S-202），降级策略 + 错误码 + warning 级别不阻塞 = NFR3 + 鲁棒性双满足 |

---

## 1. 异常场景 EX-004：Deno 缺失（资源类）

### 1.1 触发条件

- **流程**：BP-002 preflight check
- **触发时机**：CLI 启动后 t0 + 0.05s（preflight 子拍 0.1）
- **触发命令**：`research run "技术大会 talk 调研" --video-url "https://www.youtube.com/watch?v=xxxxxxx"`
- **触发检查**：`subprocess.run(['deno', '--version'], timeout=2, capture_output=True).returncode != 0`（Deno 未安装）或 `Deno.version_info().deno < '2.0.0'`（版本过低）
- **真实场景**：用户机器首次安装 research-tool，未预先安装 Deno

### 1.2 捕获模块

- **捕获模块**：M-002 preflight + M-010 error_handler
- **捕获位置**：`research_tool/cli/preflight.py::check_deno()` 函数返回 `PreflightItem(status='FAIL', missing=True)`
- **调用栈**：
  ```
  M-001.cli.main() 
    → M-002.PreflightRunner.run() 
      → check_deno()  # 返回 FAIL
        → M-010.register_error(code='E_DL_001_DENO_MISSING', phenomenon=..., cause=..., suggestion=...)
          → M-001.handle_preflight_failure()  # YouTube 任务直接退出
  ```

### 1.3 报错信息（NF-003 三段式 [PRD-V1.1:NF-003]）

```
[ERROR] E_DL_001_DENO_MISSING (退出码 2)

【现象】检测到 Deno 未安装或版本低于 2.0（要求 ≥ 2.0）
【原因】YouTube PO Token 机制要求 yt-dlp 配合 Deno ≥ 2.0 作为 JavaScript 运行时
       （yt-dlp 2025-09-23 公告 https://github.com/yt-dlp/yt-dlp/issues/12482）
【下一步】请按本机操作系统安装 Deno ≥ 2.0：
          Windows : irm https://deno.land/install.ps1 | iex
          macOS   : curl -fsSL https://deno.land/install.sh | sh
          Linux   : curl -fsSL https://deno.land/install.sh | sh
       安装后请重新运行命令。详见 https://deno.land/

【受影响的 URL】https://www.youtube.com/watch?v=xxxxxxx
【平台】youtube（Deno 是 YouTube 必需；B 站任务不受影响）
```

### 1.4 用户可执行的下一步

| 选项 | 命令 | 预期结果 |
|------|------|----------|
| 安装 Deno 后重试 | 安装 → `research run "..." --video-url "https://..."` | 进入主链 |
| 切到 B 站任务 | `research run "..." --video-url "https://www.bilibili.com/video/BV..."` | 绕过 Deno 检查 |
| 切到本地文件 | `research run "..." --video-file ./local.mp4` | 绕过 Deno 检查 |

### 1.5 流程是否优雅退出

- **是否退出**：是（YouTube 任务立即退出，不进入主链）
- **退出码**：2（与 E_DL_001_DENO_MISSING 错误码字典对应 [DE-010 ErrorRecord]）
- **耗时**：≤ 1s（满足 AC-E2E-04 类似响应时间要求）
- **中间产物**：无（preflight 阶段不产生任何文件）
- **可重入性**：是（用户安装 Deno 后重新执行同一命令可正常进入主链）
- **结论**：✅ 优雅退出，NFR3"明确报错不静默"完全满足
- **来源**：[EX-004] [PRD-V1.1:F-003.AC-2] [调研-V1.0:S-002] [DE-010/DE-012]

---

## 2. 异常场景 EX-008：B 站 403（通信类）

### 2.1 触发条件

- **流程**：BP-003 downloader
- **触发时机**：CLI 进入主链后 t0 + 0.3s ~ t0 + 5s（下载阶段）
- **触发命令**：`research run "技术大会 talk 调研" --video-url "https://www.bilibili.com/video/BVxxxxxxxxx"`
- **触发检查**：
  - `yt-dlp` 调用返回 HTTP 403
  - 视频属性为 4K / 付费 / 限地区（任一即可）
  - 真实场景：B 站大会官方录像为「大会员限定」且仅限大会员 4K 画质
- **首次失败后行为**：
  - 第 1 次重试：等待 2^1 = 2s 后重试
  - 第 2 次重试：等待 2^2 = 4s 后重试
  - 第 3 次失败：抛出 `DownloadError`，M-010 登记 E_DL_BILI_403

### 2.2 捕获模块

- **捕获模块**：M-003 downloader.bilibili + M-010 error_handler
- **捕获位置**：`research_tool/downloader/bilibili.py::BilibiliDownloader.download()` 捕获 `yt_dlp.utils.DownloadError`
- **调用栈**：
  ```
  M-001.cli.dispatch_task()
    → M-003.BilibiliDownloader.download(url)
      → yt_dlp.YoutubeDL(...).download([url])  # 抛 DownloadError(403)
        → except DownloadError as e:
            → M-010.register_error(code='E_DL_BILI_403', phenomenon=..., cause=..., suggestion=..., intermediate_files=[part_file])
              → M-001.aggregate_errors()
  ```

### 2.3 报错信息（NF-003 三段式）

```
[ERROR] E_DL_BILI_403 (退出码 3)

【现象】视频 BVxxxxxxxxx 下载返回 HTTP 403 Forbidden
【原因】该视频为以下类型之一：
       1) 4K 高清（仅大会员可下载）
       2) 付费视频（需购买后观看）
       3) 限地区视频（您所在地区不可观看）
       公开下载接口已重试 2 次（间隔 2s/4s）仍失败
【下一步】请准备 cookies.txt 文件：
          1) 安装浏览器扩展「Get cookies.txt LOCALLY」或「Cookie Editor」
          2) 登录 www.bilibili.com 后导出 Netscape 格式 cookie
          3) 保存为 cookies.txt（位于用户目录下，权限 600）
          4) 重新运行命令并附加：--cookie-file ~/cookies.txt
          
          模板文件：research_tool/templates/cookies.txt.bili（含空占位）

【受影响的 URL】https://www.bilibili.com/video/BVxxxxxxxxx
【平台】bilibili
【已重试】2 次（指数退避 2s/4s）
【保留的中间产物】raw/技术大会 talk 调研/assets/BVxxxxxxxxx.mp4.part（约 0 KB）
```

### 2.4 用户可执行的下一步

| 选项 | 命令 | 预期结果 |
|------|------|----------|
| 提供 cookie 重试 | `--cookie-file ~/cookies.txt` | 突破登录视频限制 |
| 切到 1080p 公开视频 | 换 URL | 主链继续 |
| 使用本地下载好的视频 | `--video-file ./BVxxx.mp4` | 跳过下载直接转写 |
| 接受失败 | Ctrl+C | 中断，缓存不写入 |

### 2.5 流程是否优雅退出

- **是否退出**：是（视频下载失败后主链中断，缓存不写入）
- **退出码**：3（与 E_DL_BILI_403 错误码字典对应）
- **耗时**：≤ 8s（重试 2s + 4s + 错误处理 1s + 退出 1s）
- **中间产物保留**：`.mp4.part` 文件保留（如有），便于排查（不自动清理 [DE-010.intermediate_files]）
- **可重入性**：是（用户提供 cookie 后可重新执行）
- **并发场景**：3 并发下，1 个任务失败不影响其他 2 个；M-001 退出码取最严重错误码 [EX-039] [PRD-V1.1:CE-010]
- **结论**：✅ 优雅退出，NFR3"明确报错不静默"+ F-011 错误码 100% 可解析完全满足
- **来源**：[EX-008] [PRD-V1.1:F-003.AC-3/F-011/F-012] [调研-V1.0:S-001] [DE-010]

---

## 3. 异常场景 EX-025：LLM 章节数 < 3 触发降级（模型类）

### 3.1 触发条件

- **流程**：BP-007 notes_schema（章节解析）
- **触发时机**：t0 + 295s（LLM 总结完成后）
- **触发检查**：
  - `len(parse_chapters(DE-006.front_matter.video_chapters)) < 3`
  - 常见原因：纯音乐/口播密度低/画面单一/视频 < 15 分钟
  - 真实场景：技术大会茶歇访谈（约 12 分钟，嘉宾 2 人自由对话，LLM 仅切出"开场+结束"2 章节）
- **影响范围**：仅 chapter 字段降级，不影响 LLM 总结正文质量

### 3.2 捕获模块

- **捕获模块**：M-007 notes_schema + M-010 error_handler
- **捕获位置**：`research_tool/notes/schema.py::validate_chapters()` 检测 `len(chapters) < 3`
- **调用栈**：
  ```
  M-006.LLMClient.summarize()  # 输出 front_matter.video_chapters = [{...}, {...}] 仅 2 章节
    → M-007.SchemaParser.parse(front_matter)
      → validate_chapters()  # 检测 < 3
        → M-007.equidistant_slice(total_duration, interval=300s)  # 等距 5min 切片
          → DE-007.chapters 替换为 6 个等距章节
          → M-007.set_fallback_flag(is_fallback=True, source='equidistant')
          → M-010.register_warning(code='E_LLM_002_CHAPTERS_FALLBACK', ...)
            → M-008.compose_markdown()  # 继续主链
  ```

### 3.3 报错信息（NF-003 三段式，warning 级别不阻塞）

```
[WARN] E_LLM_002_CHAPTERS_FALLBACK (warning，不阻塞)

【现象】LLM 总结输出仅 2 个章节，少于结构稳定阈值 3
【原因】可能为以下情况之一：
       1) 视频内容为纯音乐/画面单一/口播密度低，LLM 难以自动切分
       2) 视频时长 < 15 分钟，章节天然较少
       3) LLM 幻觉：未识别语义切换点
【下一步】已自动应用降级策略：
          - 等距切片：每 5 分钟 1 章节（30 分钟视频得 6 章节）
          - 标记 is_fallback=True，source='equidistant'（可追溯）
          - 完整正文（LLM 总结）保留，仅章节结构降级
          
          如需更高质量：
          1) 重新运行：--style academic  # 学术风格更倾向切分
          2) 指定不同 LLM：--llm-model deepseek-v4-pro
          3) 使用本地 LLM：--llm-model qwen2.5-72b-local

【受影响的视频】BVxxxxxxxxx
【降级策略】等距切片（5min 间隔）
【原章节数】2
【降级后章节数】6
【是否阻塞】否（warning 级别）
```

### 3.4 用户可执行的下一步

| 选项 | 命令 | 预期结果 |
|------|------|----------|
| 接受降级 | （无操作） | 笔记正常生成，章节为等距切片 |
| 切更高质量风格 | `--style academic` | LLM 更倾向切分 |
| 切更强 LLM | `--llm-model deepseek-v4-pro` | 总结质量更高 |
| 强制 LLM 重试 | `--no-cache` | 走完整重转写 + 总结路径 |
| 接受低章节数（不降级） | （V1.1 无此选项，V1.2 候选）| — |

### 3.5 流程是否优雅退出

- **是否退出**：否（warning 级别，主链继续）
- **退出码**：0（成功）
- **耗时**：< 1s（章节降级计算）
- **中间产物**：DE-007.is_fallback=True，DE-007.source='equidistant' 写入 front matter
- **可追溯性**：✅ 完整保留（is_fallback + source 字段可审计）
- **降级效果**：
  - 6 个等距章节（30 分钟视频，5min 间隔）
  - 正文 LLM 总结保留不变
  - 截图引用（DE-008）保留不变
  - 知识树 tags 合并不变
- **结论**：✅ 优雅降级不阻塞，NFR3"明确报错不静默"+ 降级策略 + 可追溯三重要求完全满足
- **来源**：[EX-025] [PRD-V1.1:F-010.AC-2/AC-3] [调研-V1.0:S-202] [DE-007] [DE-010]

---

## 4. 三个异常场景的 NFR3 满足度对比

| 维度 | EX-004 Deno 缺失 | EX-008 B 站 403 | EX-025 章节 < 3 |
|------|------------------|------------------|-----------------|
| 错误码 100% 可解析 | ✅ E_DL_001_DENO_MISSING | ✅ E_DL_BILI_403 | ✅ E_LLM_002_CHAPTERS_FALLBACK |
| 现象+原因+下一步 3 段 | ✅ | ✅ | ✅ |
| 退出码 | 2（阻塞退出） | 3（阻塞退出） | 0（warning 不阻塞） |
| 中间产物保留 | N/A（preflight） | ✅ .mp4.part 保留 | ✅ is_fallback 标记 |
| 用户可执行下一步 | ✅ 4 种选项 | ✅ 4 种选项 | ✅ 5 种选项 |
| 可重入性 | ✅ | ✅ | ✅ |
| 优雅退出 | ✅ | ✅ | ✅（降级继续） |
| 阻塞性 | YouTube 阻塞；B 站不阻塞 | 单任务失败 | warning 级别不阻塞 |

**NFR3 满足度**：3/3 = 100%（"明确报错不静默"全部满足）

---

## 5. 异常覆盖完整性声明

- 本文档覆盖 3 个最有代表性的异常场景（资源/通信/模型三类各 1）
- 完整异常场景清单见 [EX-NNN 异常场景清单.md]（45 条），覆盖核心 4/5、标准 3/5、辅助 2/5 全部合规
- 异常登记到 [DE-010 ErrorRecord] 错误码字典：12 个错误码（E_DL_001 / E_DL_001_DENO_MISSING / E_DL_BILI_403 / E_DL_002_VERSION_TOO_OLD / E_TR_001 / E_LLM_001 / E_LLM_002_CHAPTERS_FALLBACK / E_CK_001 / E_PIPE_001 / E_DL_META_001 / E_FM_001 / E_SYS_001）
- 全部错误码含 `phenomenon / cause / suggestion` 三段字段 [DE-010] [PRD-V1.1:NF-003]

---

> **本文档结束**。3 个异常场景全部满足 NFR3"明确报错不静默"+ NF-009 鲁棒性重试 + F-011 错误码 100% 可解析。系统在零修改下可优雅处理 3 类典型异常。
