# RAG Notion 知识库系统 — 前端实现说明

> **版本**：v2.0.0  
> **日期**：2026-08-14  
> **原则**：前端已经由单文件 HTML 重构为 Vue 3 SPA；本文件描述当前实现。

## 1. 现状

前端位于 `src/rag_notion_kb/web/static/`，由 FastAPI 直接托管，无 Node.js 构建步骤。页面框架是 Vue 3 Composition API + Vue Router hash 模式，组件库使用 Arco Design Vue。

## 2. 运行方式

```bash
rag-kb web --host 127.0.0.1 --port 58000
```

访问 `http://127.0.0.1:58000`。Web Server 挂载 `/static` 和 `/images`，并把未知的非 API 路径回退到 SPA 入口。

## 3. 技术栈

| 层 | 实现 |
|----|------|
| 框架 | Vue 3.5.17 via CDN |
| 路由 | Vue Router 4.5.1 via CDN |
| 组件库 | Arco Design Vue 2.58.0 via CDN |
| SFC 加载 | vue3-sfc-loader 0.9.5 via CDN |
| Markdown | 本地 `marked.min.js` |
| 代码高亮 | 本地 `highlight.min.js` |
| 样式 | Arco CSS + 本地 `styles.css` |

`index.html` 加载 CDN 资源与本地脚本，并在 Vue app 挂载前显示启动占位。组件文件由 `vue3-sfc-loader` 动态获取，编译为 Vue 组件。

## 4. 文件组织

```text
static/
├── index.html
├── api.js
├── app.js
├── utils.js
├── marked.min.js
├── highlight.min.js
├── github.min.css
├── styles.css
├── components/
│   ├── AppLayout.vue
│   ├── EmptyState.vue
│   ├── PageHeader.vue
│   ├── PageTree.vue
│   ├── ScoreBar.vue
│   ├── StatusTag.vue
│   └── SyncProgress.vue
└── views/
    ├── RootManager.vue
    ├── PageList.vue
    ├── ChunkInspector.vue
    ├── SearchDebug.vue
    └── SearchHistory.vue
```

其中 `api.js`、`app.js`、`utils.js`、`index.html` 是自定义入口和基础设施；`marked.min.js`、`highlight.min.js`、`github.min.css` 是 vendored 库。所有 `.vue` 文件是 SPA 组件。

## 5. 路由

`app.js` 中的路由使用 hash history：

| 路由 | 名称 | 组件 | 页面标题 |
|------|------|------|----------|
| `/` | 重定向 | - | Root 管理 |
| `/roots` | `roots` | `RootManager.vue` | Root 管理 |
| `/pages` | `pages` | `PageList.vue` | 页面列表 |
| `/page/:page_id` | `page` | `ChunkInspector.vue` | Chunk 详情 |
| `/inspect/:page_id` | 重定向到 `page` | - | Chunk 详情 |
| `/search-debug` | `search-debug` | `SearchDebug.vue` | 检索调试 |
| `/search-history` | `search-history` | `SearchHistory.vue` | 检索历史 |
| `/history` | 重定向到 `/search-history` | - | 检索历史 |
| 其他 | 重定向到 `/roots` | - | Root 管理 |

服务端收到非 `/api/`、`/static/`、`/images/` 的未知路径时返回 `index.html`；前端会把 pathname 同步到 hash 路由。

## 6. 全局状态与组件

`app.js` 使用 Vue `reactive` 保存轻量全局状态：

- `collapsed`：侧栏折叠状态。
- `routeTitle`：当前页面标题。
- `workerStatus`：VectorizeWorker 状态。
- `workerPolling`：Worker 状态轮询标记。

每 15 秒在文档可见时刷新一次 Worker 状态。`bootstrap()` 预加载共享组件并注册为全局组件。

通用组件职责：

| 文件 | 职责 |
|------|------|
| `AppLayout.vue` | 侧边导航、顶部栏、内容区布局 |
| `PageHeader.vue` | 页面标题、说明与操作区 |
| `EmptyState.vue` | 空数据状态 |
| `PageTree.vue` | 递归页面树、选择与展开状态 |
| `ScoreBar.vue` | 检索分数可视化 |
| `StatusTag.vue` | pending/indexing/indexed/failed 状态标签 |
| `SyncProgress.vue` | 同步任务进度模态框 |

## 7. 页面视图

### `RootManager.vue`

展示已配置 Root，支持添加、删除、树缓存元数据、刷新树、展开树、多选页面并启动批量同步。批量同步使用 `api.syncPages()`，随后由 `SyncProgress.vue` 轮询 `api.getSyncProgress()`。

### `PageList.vue`

展示 `GET /api/pages` 返回的页面摘要。可筛选 Root 和向量状态，支持向量开关 `api.toggleVector()`、手动向量化 `api.triggerVectorize()`，并轮询刷新 indexing 状态。

### `ChunkInspector.vue`

打开单页详情。使用 Markdown 渲染原始内容，展示 chunk 类型、header path、向量非零状态和 embedding stats。

### `SearchDebug.vue`

调试检索 Pipeline。可设置 dense/sparse 权重、TopK、最小相似度、ReRank 模型、过滤条件、上下文模式和最大 token。`api.debugSearch()` 返回 `DebugSearchResponse`，页面用 `ScoreBar.vue` 展示每阶段分数。支持通过 `history_id` 查询参数回放历史。

### `SearchHistory.vue`

展示 `api.listHistory()` 与 `api.getHistoryStats()` 的检索历史和健康统计，支持过滤、删除单条、清空全部，以及跳转 `SearchDebug.vue` 回放。

## 8. API 封装

`api.js` 统一执行 `fetch`、JSON 序列化、错误解析与 `ApiError` 抛出。暴露方法：

```text
request
ApiError
listRoots
addRoot
deleteRoot
getRootTree
refreshRootTree
listPages
getPageDetail
syncPage
toggleVector
triggerVectorize
syncPages
getSyncProgress
debugSearch
listHistory
getHistoryStats
getHistory
replayHistory
deleteHistory
clearHistory
getWorkerStatus
```

## 9. 与后端 API 的对应关系

前端 API 封装与 `Backend.md` 第 9 节一致，没有私有或后端未知的路由。`/api/roots/{root_id}/tree` 用于页面树；`/api/sync` 只执行 `sync_fetch`，不自动向量化。

## 10. 测试与风险

- 后端集成测试覆盖 Web API；前端为 CDN SPA，当前没有浏览器自动化测试。
- 外部 CDN 不可用时 SPA 无法启动；Markdown 与代码高亮库已本地化，Vue/Arco/vue3-sfc-loader 仍依赖 CDN。
- 组件动态加载失败会在启动占位处显示错误信息。

## 11. 修订历史

| 版本 | 日期 | 变更内容 |
|------|------|----------|
| v2.0.0 | 2026-08-14 | 更新为当前 Vue 3 + Arco SPA，补充 Root 管理、检索调试、检索历史与 API 清单 |
