# research-tool · 风险登记表（Risk Register）

> **创建日期**：2026-07-21
> **关联**：`planning/audit_scope.md` / `problems/technical_debt.md`

---

## 风险评估矩阵

概率：L(低) / M(中) / H(高)
影响：L(低) / M(中) / H(高) / C(关键)

---

## 审核过程风险

| ID | 风险 | 概率 | 影响 | 缓解措施 | 状态 |
|----|------|------|------|---------|------|
| R-01 | Agent 输出超 context 限制 | M | M | 每 Agent ≤ 8k token；超则拆多轮 | 已缓解 |
| R-02 | 实验无算力/数据无法验证 | H | M | UNVERIFIED 标记 + 诚实性声明 | 已接受 |
| R-03 | 工作区被未授权修改 | L | H | 启动期 git status 留痕；退出期对比 | 已缓解 |
| R-04 | 5 个 Agent 输出格式不一 | M | L | 严格结构化模板（背景/方法/发现/建议） | 已缓解 |
| R-05 | 文档数据与代码实际不一致 | H | M | 所有数据用命令实证，不依赖文档声明 | 已缓解 |
| R-06 | 审核范围蔓延（scope creep） | M | M | `audit_scope.md` 明确 Out of Scope | 已缓解 |

---

## 项目技术风险（审核发现）

| ID | 风险 | 概率 | 影响 | 来源 | 缓解措施 |
|----|------|------|------|------|---------|
| R-07 | API 密钥泄露（.env 含真实 key） | M | C | TD-02 | rotate 4 个 key + 迁移至 ~/.research/.env |
| R-08 | 非 macOS 用户无法运行（硬编码路径） | H | H | TD-03 | cli.py:1073 改为相对路径 |
| R-09 | 静默失败导致调试困难 | H | M | TD-04 | 17 处 except:pass 加日志 |
| R-10 | CI 缺失导致回归 | H | H | TD-01 | 新增 pytest workflow |
| R-11 | 架构扩展受阻（_exec 大 if-elif） | M | M | TD-05 | StageRegistry 化 |
| R-12 | 循环依赖隐患（infra→app） | M | H | TD-06 | 拆 application/services.py |
| R-13 | 重构无安全网（34 模块无测试） | H | H | TD-20 | 补核心 stage 测试 |
| R-14 | 60 处宽异常吞噬掩盖真实错误 | H | M | TD-17 | 灰度分类治理 |

---

## 项目运营风险

| ID | 风险 | 概率 | 影响 | 来源 | 缓解措施 |
|----|------|------|------|------|---------|
| R-15 | Star 数持续为 0 | H | M | Star 分析 | README 重做 + PyPI 发布 + 英文版本 |
| R-16 | 单作者 bus factor = 1 | H | H | git log | CONTRIBUTING + 社区建设 |
| R-17 | 文档漂移（STATUS/CLAUDE 数据过期） | H | L | 矛盾点 | 审核轮同步 + CI 校验 |
| R-18 | 未跟踪代码丢失（6 个生产文件） | M | H | file_audit | 及时 commit |
| R-19 | 重依赖劝退用户（MinerU 7GB） | H | M | Star 分析 | 分档安装 + 云端替代 |
| R-20 | 竞品碾压（LangChain/OpenAI DeepResearch） | M | H | 竞争分析 | 差异化：反向循环 + 视频摄取 + 国内网络 |

---

## 风险热力图

```
影响
  C |       R-07
  H | R-13  R-08,R-10,R-12,R-18  R-16,R-20
  M | R-06  R-01,R-04,R-05,R-11  R-09,R-14,R-15,R-19
  L |       R-03                  R-17
    +----------------------------------------
      L         M                  H      概率
```

---

## Top 5 需立即处理

| 优先级 | ID | 风险 | 行动 |
|--------|-----|------|------|
| 1 | R-07 | API 密钥泄露 | 立即 rotate + 迁移 |
| 2 | R-10 | CI 缺失 | 新增 pytest workflow |
| 3 | R-08 | 硬编码路径 | 1 行修复 |
| 4 | R-13 | 无测试安全网 | 补核心 stage 测试 |
| 5 | R-18 | 未跟踪代码 | commit 6 个文件 |

---

## 监控计划

| 频率 | 检查项 |
|------|--------|
| 每次 commit | gitleaks pre-commit hook |
| 每次 PR | pytest CI（待建） |
| 每月 | STATUS.md vs 代码一致性 |
| 每季度 | 技术债务评级更新 |

---

**更新时间**：2026-07-21
