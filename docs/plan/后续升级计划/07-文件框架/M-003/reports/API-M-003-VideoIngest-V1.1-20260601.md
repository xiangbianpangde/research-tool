# 接口注释清单 — M-003（DD-M-003）

> **生成方**：DD-M-003 | **日期**：2026-06-01 | **模块**：M-003 下载器
> **依据**：[DD-001:IC-VideoIngest-V1.1 §IC-005/007/008]

| 接口编号 | 关联契约 | 实现位置 | 函数签名 | 参数说明 | 返回值 | 错误码 | 状态 |
|---------|---------|---------|---------|---------|--------|--------|------|
| API-005 | IC-005 | downloader.py::resolve_local | `resolve_local(path: str) -> DownloadTask` | path: 文件绝对路径 | DownloadTask | E_DL_LOCAL_001/002 | 完整 |
| API-007 | IC-007 | downloader.py::validate_yt_dlp_version | `validate_yt_dlp_version() -> bool` | 无 | bool | E_DL_002_VERSION_TOO_OLD / E_DL_003_NETWORK | 完整 |
| API-007 | IC-007 | downloader.py::download_youtube | `download_youtube(url: VideoURL) -> DownloadTask` | url: VideoURL(YOUTUBE) | DownloadTask | E_DL_001 / E_DL_001_DENO_MISSING / E_DL_003_NETWORK | 完整 |
| API-007 | IC-007 | downloader.py::download_bilibili | `download_bilibili(url: VideoURL) -> DownloadTask` | url: VideoURL(BILIBILI) | DownloadTask | E_DL_001 / E_DL_BILI_403 / E_DL_003_NETWORK | 完整 |
| API-007 | IC-007 | downloader.py::inject_cookie | `inject_cookie(args: list[str], cookie_path: str) -> list[str]` | args, cookie_path | list[str] | E_DL_001 | 完整 |
| API-008 | IC-008 | downloader.py::resolve_local | `resolve_local(path: str) -> DownloadTask` | path | DownloadTask | E_DL_LOCAL_001/002 | 完整 |

## 函数签名注释示例

```python
def download_youtube(url: VideoURL) -> DownloadTask:
    """
    [函数名] download_youtube
    [职责]  同步入口：YouTube 视频下载
    [关联接口契约] IC-007
    [参数说明] url: VideoURL 必填（platform=YOUTUBE）
    [返回值] 类型: DownloadTask；描述: 下载结果
    [错误码] E_DL_001 / E_DL_001_DENO_MISSING / E_DL_003_NETWORK
    [前置条件] url 合法 + yt-dlp 版本已校验
    [后置条件] file_path 存在
    [并发安全] 否（由 M-012 Semaphore 保护）
    [幂等性] 否（除非 M-004 缓存命中）
    [性能约束] 视频大小相关（10-300s）
    [来源标注] [DD-001:IC-VideoIngest-V1.1 §IC-007]
    """
```

## 覆盖率统计
- 接口契约总数（M-003 涉及）：3 个 IC（IC-005/007/008）
- 已注释化接口：3/3 = 100%
- 函数签名注释完整：6/6 = 100%
- 参数/返回值/错误码齐全：6/6 = 100%

## 来源标注
[DD-001:IC-VideoIngest-V1.1 §IC-005/007/008] [DD-001:MD-VideoIngest-V1.1 §M-003]
