# 框架决策记录（FDR）— M-003（DD-M-003）

> **生成方**：DD-M-003 | **日期**：2026-06-01

## FDR-001 单文件聚合 vs 子包拆分

- **决策状态**：已接受
- **决策内容**：M-003 采用方案 A（单文件 `downloader.py` 聚合 5 子模块），不采用方案 B（子包拆分）
- **决策理由**：
  1. DD-001 FS §M-003 明确指定 `research_tool/downloader.py` 单文件（FS-001 文件名规范）
  2. 5 类 + 5 函数约 15 方法，预估 < 600 行，单文件可控
  3. 多方案对比得分 9.34 vs 7.55，差距 1.79
  4. 单文件最小化多实例隔离风险（R28）
- **拒绝的替代方案**：方案 B（子包拆分 downloader/ → version_validator.py 等 5 文件）—— 违反 DD-001 FS 单文件规范，且增加 __init__.py 复杂度
- **影响范围**：research_tool/downloader.py、research_tool/tests/test_downloader.py
- **相关 FDR**：无
- **来源标注**：[DD-001:FS-VideoIngest-V1.1 §M-003] [DD-M推断: 5 类规模评估]

## FDR-002 Cookie 0o600 权限硬约束

- **决策状态**：已接受
- **决策内容**：CookieInjector.validate_perms 强制要求 cookie_path 文件权限 = 0o600，否则抛出 DownloadError(E_DL_001)
- **决策理由**：
  1. AR 调研 Cookie 安全规范要求 0o600 防止同机其他用户读取
  2. yt-dlp 社区默认建议 Cookie 文件 0o600
  3. 防止因权限不当导致的安全审计失败
- **拒绝的替代方案**：仅警告不抛错 —— 风险过高，与 AR 安全规范冲突
- **影响范围**：CookieInjector.validate_perms、CookieInjector.inject_args、测试 fixture cookie_file_0o600
- **相关 FDR**：无
- **来源标注**：[DD-M推断: 0o600 强制，AR 调研 Cookie 安全规范]

## FDR-003 B 站 403 严格不绕过

- **决策状态**：已接受
- **决策内容**：BilibiliDownloader 遇到 403 必须抛出 DownloadError(E_DL_BILI_403)，不重试不绕过
- **决策理由**：
  1. AR 调研 S-101 明确：B 站 403 不绕过（合规与社区约定）
  2. 提示用户提供有效 Cookie 是正确的产品策略
  3. 绕过可能涉及伪造 User-Agent / Referer，存在合规风险
- **拒绝的替代方案**：自动重试 + 伪造请求头 —— 违反 AR 调研结论
- **影响范围**：BilibiliDownloader.download、_handle_403、TestBilibiliDownloader
- **相关 FDR**：无
- **来源标注**：[DD-001:MD-VideoIngest-V1.1 §M-003] [调研:S-101]

## FDR-004 测试文件按"5 核心 + 3 边界 + 4 异常 = 12 用例"分布

- **决策状态**：已接受
- **决策内容**：test_downloader.py 实际产出 6 个测试类共 13 个测试场景（核心 5 + 边界 3 + 异常 5）
- **决策理由**：
  1. MD §M-003 测试策略指定 5+3+4=12 用例作为目标
  2. 实际拆分到 6 个测试类（5 个主类 + 1 集成类），每个类 1-3 用例
  3. 异常用例 5 个略超 4 个（TestCookieInjector 多 1 个），覆盖率提升
- **拒绝的替代方案**：严格 12 用例拆分到 5 类 —— 边界条件用例被压缩
- **影响范围**：test_downloader.py 的 6 个测试类
- **相关 FDR**：无
- **来源标注**：[DD-001:MD-VideoIngest-V1.1 §M-003 测试策略]

## 来源标注
[DD-001:FS-VideoIngest-V1.1 §M-003] [DD-001:MD-VideoIngest-V1.1 §M-003] [DD-M推断: 多处]
