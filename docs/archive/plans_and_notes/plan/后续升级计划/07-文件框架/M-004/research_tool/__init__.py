"""
M-004 缓存管理器 — 模块初始化文件（无业务代码）

[文件路径] src/research_tool/__init__.py（M-004 模块导出）
[文件职责] 导出 M-004 模块的公共接口（类 + 函数）
[所属模块] M-004
[功能描述]
  功能1: 导出 5 个核心类（DBConnection / CacheRepository / HashCalculator / TTLCleaner / LockManager）
  功能2: 导出 5 个模块级函数（query_cache / write_cache / cleanup_expired / compute_url_sha256 / acquire_write_lock）
  功能3: 导出 M-004 相关的常量（DEFAULT_TTL_DAYS / YOUTUBE_TTL_HOURS / DB_FILE_PERMS）
[依赖关系]
  依赖文件: research_tool/cache_manager.py
  被依赖文件: research_tool/downloader.py (M-003)
              research_tool/cli.py (M-001)
[注意事项]
  注意1: 遵循 CS-001 命名规范（snake_case）
  注意2: 公共接口稳定，私有成员以下划线开头不导出
[代码风格] CS-001
[创建日期] 2026-06-01
[修改历史]
  2026-06-01: DD-M-004-20260601 - 初始 __init__.py 框架
[作者] DD-M-004-20260601
[来源标注] [DD-001:FS-VideoIngest-V1.1] [DD-001:MD-004]
"""

# === M-004 公共接口导出（仅注释，不写业务代码） ===
# [DD-M推断:依据 CS-001 __init__.py 导出规范]

# from research_tool.cache_manager import (
#     DBConnection,
#     CacheRepository,
#     HashCalculator,
#     TTLCleaner,
#     LockManager,
#     query_cache,
#     write_cache,
#     cleanup_expired,
#     compute_url_sha256,
#     acquire_write_lock,
#     DEFAULT_TTL_DAYS,
#     YOUTUBE_TTL_HOURS,
#     DB_FILE_PERMS,
#     CACHE_TABLE_NAME,
# )

# [模块元数据]
__version__: str = "1.1.0"
"""M-004 模块版本，与 VideoIngest V1.1 对齐。"""

__author__: str = "DD-M-004-20260601"
"""M-004 模块文件框架作者。"""

__module_id__: str = "M-004"
"""M-004 模块编号（多实例隔离标识）。"""
