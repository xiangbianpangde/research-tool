# API 设计规范

> **本规范是 [ai-workflow 第二步·迭代开发 §2.1 实现](ai-workflow_AI协作开发流程/04-第二步_迭代开发.md) 的细化**，约束「提供 HTTP API 的功能点」的接口设计；[§2.2 可观测验证](ai-workflow_AI协作开发流程/04-第二步_迭代开发.md) 中「API 接口」一类的证据（Swagger / curl 输出）即对照本规范。
>
> **档位裁剪**：内部小工具可裁剪版本管理与限流；对外 / 多端服务守全部。

---

## 一、红线（必守）

| # | 红线 | 怎么自动抓 |
|---|------|-----------|
| 1 | 资源用名词复数 + kebab-case，URL 内无动词 | `spectral` OpenAPI lint |
| 2 | 所有响应统一外层结构 `code` + `message` + `data` | 响应拦截器（§二）+ 审查 |
| 3 | HTTP 状态码与 body `code` 一致（不返回 200 + `code:50000`） | 审查 |
| 4 | 错误码唯一、废弃不复用 | 错误码表评审 |
| 5 | 所有接口默认需认证（公开接口显式标注例外） | 中间件默认拦截 |
| 6 | 输入严格校验（类型/长度/格式/范围，白名单优先） | pydantic / zod schema |
| 7 | 向后兼容：不删 / 不重命名已有字段 | `spectral` diff + 审查 |

---

## 二、落地：统一响应 + 契约校验（复制即用）

**统一响应拦截器**（FastAPI 示例，前端只需写一个解析器对接全部接口）：

```python
# 成功统一包成 {code:0, message:"ok", data:...}
@app.middleware("http")
async def wrap_response(request, call_next): ...

# 业务异常 → {code, message, data:null}，HTTP 状态码与 code 对齐
@app.exception_handler(AppError)
async def handle(req, exc):
    return JSONResponse(status_code=exc.http_status,
                        content={"code": exc.code, "message": exc.message, "data": None})
```

**`.spectral.yaml`（CI 中 lint OpenAPI）：**

```yaml
extends: ["spectral:oas"]
rules:
  path-kebab-case: { given: "$.paths[*]~", then: { function: pattern, functionOptions: { match: "^[a-z0-9-/{}]+$" } } }
  no-verb-in-path: { given: "$.paths[*]~", then: { function: pattern, functionOptions: { notMatch: "(get|create|update|delete)" } } }
```

---

## 三、决策表 / 速查

### URL 与方法

```
GET /api/v1/users          列表      POST   /api/v1/users        创建
GET /api/v1/users/:id      详情      PUT/PATCH /api/v1/users/:id 更新
DELETE /api/v1/users/:id   删除      GET /api/v1/users/:id/orders 子资源
```
层级 ≤ 3 层；特殊操作用 `POST /orders/:id/cancel`。

### 响应结构

```json
{ "code": 0, "message": "ok", "data": { "items": [], "total": 100, "page": 1, "page_size": 20 } }
```

### 状态码 ↔ 错误码（格式：HTTP码 + 2位模块 + 2位错误）

| HTTP | 场景 | 错误码示例 |
|------|------|-----------|
| 400 | 参数不合法 | 40001 缺参 / 40002 格式错 |
| 401 | 未认证/过期 | 40101 过期 / 40102 无效 |
| 403 | 权限不足 | 40301 |
| 404 | 不存在 | 40401 用户 / 40402 订单 |
| 409 | 冲突 | 40901 重复 |
| 422 | 语义错误 | 42201 库存不足 |
| 429 | 限流 | 42901 |
| 500 | 服务端 | 50001 DB / 50002 三方超时 |

### 分页 / 版本 / 安全

| 项 | 约定 |
|----|------|
| 分页 | `page`（从 1 起）+ `page_size`（默认 20，上限 100）；列表返回 `total` |
| 排序 / 过滤 | `sort_by` + `sort_order(asc/desc)`；`?status=active` |
| 版本 | URL 路径版本 `/api/v1/`；主版本号变 = 不兼容；同时维护 ≥ 2 个大版本 |
| 认证 | `Authorization: Bearer <token>` |
| 防护 | CORS 白名单；限流 60 req/min + `X-RateLimit-*` |

---

## 四、反模式（保留 API 独有）

### ❌ 动词 URL
`GET /api/getUserList`、`POST /api/createOrder` → 一个 CRUD 资源裂成 4 个无关 URL。
✅ `/api/users` 一个资源 + HTTP 方法表达操作。

### ❌ 响应格式不一致
接口 A `{success:true,result:..}`、接口 B `{code:200,data:..}` → 前端每个接口写一套解析。
✅ 全部 `{code,message,data}`，前端一个拦截器通吃。

### ❌ 不兼容升级
v2 "顺手"把 `name` 改成 `fullName` → 未迁移的 v1 调用方生产事故。
✅ 新增字段、保留旧字段；监控旧字段调用量归零后再下线。

---

## 五、检查清单

- [ ] URL 名词复数 + kebab-case，无动词（spectral 通过）
- [ ] HTTP 方法语义正确（GET 读 / POST 建 / PUT 全量 / PATCH 部分 / DELETE 删）
- [ ] 响应统一 `code` + `message` + `data`；列表带 `total`
- [ ] 错误码唯一、不复用
- [ ] 分页参数统一（`page` + `page_size`）
- [ ] 所有接口默认需认证
- [ ] 输入做了校验（类型/长度/范围）
- [ ] 未删除 / 重命名已有字段（向后兼容）
- [ ] CORS 白名单 + 限流已配

---

## 六、关联

| 方向 | 链接 |
|------|------|
| 细化自 | [ai-workflow 第二步 §2.1 实现](ai-workflow_AI协作开发流程/04-第二步_迭代开发.md) |
| 验收标准 | [docs/specs/04-api.md](../docs/specs/04-api.md) |
| 错误处理 / 异常类型 | [02-代码编写规范 §四](02-coding_代码编写规范.md) |
| API 路由全景图 | [08-代码理解与图谱规范](08-code-understanding_代码理解与图谱规范.md) |

---

## 更新记录

| 日期 | 版本 | 变更说明 |
|------|------|---------|
| 2026-05-27 | v2.0 | 重构为 §2.1 的细化；红线配 spectral；新增统一响应拦截器骨架与 OpenAPI lint 规则；表格精简 |
| 2026-05-26 | v1.0 | 按统一模板改造 |
