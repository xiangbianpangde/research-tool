# research-tool · 未来 10 个 Star 增长方向

> 数据基础：`reports/github_growth_analysis.md`
> 排序原则：低成本高回报优先；高风险高回报靠后

## 增长瓶颈摘要

| # | 瓶颈 | 权重 |
|---|---|---|
| B1 | README 零视觉证据 + 全中文 + 无 badge + 无 Quick Start | 35% |
| B2 | 部署门槛过高 | 30% |
| B3 | 社区资产全空 | 25% |
| 备 | 反向循环 / 视频摄取 / 国内网络适配差异化未传达 | 10% |

## Top 10 Star 增长方向（按潜在收益降序）

| # | 方向 | 用户价值 | 实现方案 | 推广 | 验证 |
|---|---|---|---|---|---|
| **S1** | 首页重做：5 秒价值主张 + GIF demo + Quick Start | 5 秒内明白"这是为我准备的吗？" | 重排 README：第 1 段 = "From topic to cited report in 3 commands" + V1.1 截图/GIF + `pip install research-tool && research ui` | 同步发 1 篇 dev.to + 1 篇知乎 | 5 个外部开发者盲测首屏 30 秒；30 天 Star +30% |
| **S2** | PyPI 发布 + `[project.urls]` | `pip install research-tool` 一行可用 | (1) `[project.urls]`；(2) `release.yml` trusted publisher；(3) README PyPI badge | PyPI 自动 release | 下载量 ≥ 100/周 |
| **S3** | 英文 README + Topics 补齐 | 打开英文 GitHub 用户市场 | 新建 `README.en.md` 翻译前 80 行 + `gh repo edit --add-topic` | HN "Show HN" | 英文 README 跳转率；HN 排名 ≥ 30 |
| **S4** | CI 全套 + badges 满屏 | 贡献者看到 CI 全绿敢提 PR | (1) `test.yml` matrix；(2) `codecov.yml`；(3) README 加 CI/Coverage/License/PyPI badge | Badges 视觉吸引 | CI 全绿 + coverage 报告；PR 数/月 |
| **S5** | 抽出 `docs/showcase/` + 反向循环头牌 | 让人看到真实产物 | (a) `docs/showcase/` 放 3-5 个真实 `report.md` + 知识树渲染图；(b) `docs/why-research-tool.md` | 链接到 README 首屏 | showcase 浏览量；star 转化率 |
| **S6** | CONTRIBUTING + Issue/PR 模板 + Discussions | 降低贡献门槛 | (1) `CONTRIBUTING.md`；(2) 3 个 ISSUE_TEMPLATE；(3) PULL_REQUEST_TEMPLATE；(4) 开启 Discussions | README 链接 | PR 数/月；Issues 分类 |
| **S7** | 反向循环原理博客 | 提升 HN/dev.to/知乎曝光 | 2000-3000 字博客含架构图 + 伪代码 | dev.to/知乎/Medium/HN | 阅读量 ≥ 5k；衍生 star ≥ 50 |
| **S8** | CHANGELOG.md + release tag + GitHub Releases | 增加"成熟感" | (1) `CHANGELOG.md`；(2) git tag 历史 commit；(3) GitHub Releases | README 链接 | Releases 列表完整 |
| **S9** | 3 分钟产品 demo 视频 | 让人 3 分钟看完产品 | Gradio UI 录 3 分钟：主题输入 → collect → 知识树 → 报告 | 嵌 README + 投 B 站/YouTube/dev.to | 视频观看量 |
| **S10** | Showcase 用户故事 | "别人也在用" | 联系 3-5 个真实用户写 200 字 + 截图 | `docs/users/` + README 链接 | 用户案例 ≥ 3 |

## 季度路线图

| 季度 | 必做 | 加分 |
|---|---|---|
| Q3 2026 | S1 + S2 + S4 | S8 |
| Q4 2026 | S3 + S5 + S6 | S9 |
| Q1 2027 | S7 | S10 |

## 与 future_roadmap.md 边界

- S1-S10 聚焦 Star 增长（README/CI/社区/对外传播）
- `future_roadmap.md` 聚焦产品技术演进（P0 债务 / 架构 / CVPR / 专家库 / 测试）
- 两表可并行推进；S2 + future_roadmap D2 = 同一动作（PyPI 发布）

---

**报告完成时间**：2026-07-21
**关联文件**：`reports/github_growth_analysis.md`（诊断）/ `reports/future_roadmap.md`（技术演进）