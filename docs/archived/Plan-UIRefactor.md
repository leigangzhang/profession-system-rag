# RAG Notion 知识库系统 - 前端全面重构（飞书风格 + Vue 3）实施计划

## Context

### 当前状态
系统后端已实现：
1. **向量开关 + 后台异步向量化**：`sync_fetch` / `sync_vectorize` / `VectorizeWorker`
2. **Notion Root 页面管理 + 选择性同步**：`NotionRootStore` + 页面树 + 批量同步
3. **混合检索效果评估 + 检索历史**：`debug_search()` + `SearchHistoryStore`

当前前端为**单文件 HTML**（`web/static/index.html`），所有页面写在一个文件中，CSS 堆叠，维护困难。

### 本次变更
用户要求：**参考飞书（Lark）页面设计风格，对整个 Web 前端进行全面重构**：
1. 使用 **Vue 3（Composition API）** 组件化开发
2. 使用 **Arco Design Vue**（字节跳动组件库，风格最接近飞书）
3. 避免 HTML/CSS 代码堆叠，页面拆分为独立 Vue 组件
4. **后端 API 完全保持兼容**，不做任何变更

---

## 核心设计决策

| # | 决策 | 说明 |
|---|---|---|
| 1 | Vue 3 via CDN，无构建步骤 | 用户无需安装 Node.js，直接通过 CDN 引入 Vue 3 + Arco Design，保持项目纯 Python |
| 2 | Arco Design Vue via CDN | 字节跳动出品，风格现代清爽，最接近飞书设计语言 |
| 3 | 后端 API 零变更 | 所有 `/api/*` 路由保持不变，`web_server.py` 只调整 static 文件挂载 |
| 4 | 单页应用（SPA） | Vue Router hash 模式，前端路由切换，无页面刷新 |
| 5 | API 封装层 | 统一 `api.js` 封装所有后端调用，组件只调用封装函数 |
| 6 | 组件按视图拆分 | 每个功能页面对应一个 `views/*.vue` 文件，通用组件放 `components/` |

---

## 技术栈

| 层 | 技术 | 引入方式 |
|----|------|----------|
| 框架 | Vue 3.4 + Vue Router 4 | CDN (`unpkg.com`) |
| 组件库 | Arco Design Vue 2.x | CDN (`unpkg.com`) |
| 图标 | Arco Vue Icon | CDN (`unpkg.com`) |
| Markdown | marked.js | CDN (`unpkg.com`) |
| 代码高亮 | highlight.js | CDN (`unpkg.com`) |
| 样式 | Arco CSS + 自定义变量覆盖 | CDN + `<style>` |

### CDN 引用（`index.html`）

```html
<!-- Vue 3 -->
<script src="https://unpkg.com/vue@3/dist/vue.global.js"></script>
<!-- Vue Router -->
<script src="https://unpkg.com/vue-router@4/dist/vue-router.global.js"></script>
<!-- Arco Design Vue -->
<link rel="stylesheet" href="https://unpkg.com/@arco-design/web-vue/dist/arco.css" />
<script src="https://unpkg.com/@arco-design/web-vue/dist/arco-vue.min.js"></script>
<script src="https://unpkg.com/@arco-design/web-vue/dist/arco-vue-icon.min.js"></script>
<!-- marked + highlight -->
<script src="https://unpkg.com/marked/marked.min.js"></script>
<link rel="stylesheet" href="https://unpkg.com/@highlightjs/cdn-assets/styles/github.min.css" />
<script src="https://unpkg.com/@highlightjs/cdn-assets/highlight.min.js"></script>
```

---

## 飞书风格设计规范

### 色彩系统

```css
:root {
  /* 主色 */
  --primary-color: #3370FF;
  --primary-light: #E8F1FF;
  --primary-hover: #2860E0;
  
  /* 背景 */
  --bg-body: #F5F6F7;
  --bg-card: #FFFFFF;
  --bg-sidebar: #FFFFFF;
  --bg-hover: #F2F3F5;
  --bg-selected: #E8F1FF;
  
  /* 文字 */
  --text-primary: #1F2329;
  --text-secondary: #646A73;
  --text-tertiary: #8F959E;
  --text-link: #3370FF;
  
  /* 边框 */
  --border-color: #DEE0E3;
  --border-light: #EBECF0;
  
  /* 状态色 */
  --success: #34D399;
  --warning: #FBBF24;
  --error: #F87171;
  --info: #3370FF;
  
  /* 阴影 */
  --shadow-card: 0 1px 2px rgba(0,0,0,0.06), 0 2px 8px rgba(0,0,0,0.04);
  --shadow-dropdown: 0 4px 12px rgba(0,0,0,0.08);
}
```

### 布局系统

| 元素 | 规格 |
|------|------|
| 侧边栏宽度 | 220px |
| 侧边栏背景 | `#FFFFFF`，右边框 `1px solid #EBECF0` |
| 内容区背景 | `#F5F6F7` |
| 卡片圆角 | `8px` |
| 卡片阴影 | `0 1px 2px rgba(0,0,0,0.06)` |
| 按钮圆角 | `4px` |
| 表格行高 | `48px` |
| 间距单位 | `8px` 倍数（8, 16, 24, 32, 40） |
| 字体 | `-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif` |

### 组件风格映射

| 飞书元素 | Arco 组件 | 自定义覆盖 |
|----------|-----------|------------|
| 侧边导航 | `a-menu` | 宽度 220px，选中项 `#E8F1FF` 背景 |
| 卡片容器 | `a-card` | 圆角 8px，阴影微调 |
| 表格 | `a-table` | 表头 `#F2F3F5`，行 hover `#F2F3F5` |
| 按钮（主） | `a-button type='primary'` | 背景 `#3370FF` |
| 按钮（次） | `a-button` | 边框 `#DEE0E3`，文字 `#646A73` |
| 标签 | `a-tag` | 圆角 4px（小号） |
| 滑动条 | `a-slider` | 轨道色 `#3370FF` |
| 开关 | `a-switch` | 开启色 `#3370FF` |
| 输入框 | `a-input` | 聚焦边框 `#3370FF` |
| 下拉选择 | `a-select` | 圆角 4px |
| 单选 | `a-radio-group` | 选中色 `#3370FF` |
| 多选 | `a-checkbox-group` | 选中色 `#3370FF` |
| 进度条 | `a-progress` | 色 `#3370FF` |
| 分页 | `a-pagination` | 简洁样式 |
| 模态框 | `a-modal` | 圆角 8px |
| 消息提示 | `a-message` | 右上角 |

---

## 前端架构

### 文件结构

```
src/rag_notion_kb/web/
├── web_server.py              # 后端 FastAPI（不变，只调整 static 挂载）
├── static/
│   ├── index.html             # SPA 入口：加载 CDN + 挂载 Vue App
│   ├── app.js                 # Vue App 实例、路由配置、全局状态
│   ├── api.js                 # 后端 API 封装层（所有 axios/fetch 调用）
│   ├── components/            # 通用组件
│   │   ├── AppLayout.vue      # 主布局：侧边栏 + 顶部栏 + 内容区
│   │   ├── PageHeader.vue     # 页面顶部标题 + 面包屑
│   │   ├── ScoreBar.vue       # 检索分数可视化条
│   │   ├── StatusTag.vue      # 状态标签（pending/indexing/indexed/failed）
│   │   ├── PageTree.vue       # Notion 页面树（递归组件）
│   │   ├── SyncProgress.vue   # 同步进度模态框
│   │   └── EmptyState.vue     # 空状态插画
│   └── views/                 # 页面级组件
│       ├── RootManager.vue    # Root 管理 + 页面树 + 批量同步
│       ├── PageList.vue       # 已同步页面列表 + 向量开关
│       ├── ChunkInspector.vue # Chunk 详情（Markdown + Chunk 卡片）
│       ├── SearchDebug.vue    # 检索调试/评估
│       └── SearchHistory.vue  # 检索历史列表 + 回放
```

### 路由配置

```javascript
const routes = [
  { path: '/', redirect: '/roots' },
  { path: '/roots', component: RootManager, meta: { title: 'Root 管理', icon: 'icon-folder' } },
  { path: '/pages', component: PageList, meta: { title: '页面列表', icon: 'icon-file' } },
  { path: '/inspect/:page_id', component: ChunkInspector, meta: { title: 'Chunk 详情' } },
  { path: '/search-debug', component: SearchDebug, meta: { title: '检索调试', icon: 'icon-search' } },
  { path: '/search-history', component: SearchHistory, meta: { title: '检索历史', icon: 'icon-history' } },
];
```

### 全局状态（Pinia 替代：简单 reactive）

由于使用 CDN 无构建，`Pinia` 不方便引入。使用 Vue 3 内置 `reactive` 实现轻量全局状态：

```javascript
// app.js
const globalState = Vue.reactive({
  activeMenu: '/roots',
  syncProgress: null,      // 当前同步进度
  vectorizeWorkerStatus: {}, // Worker 状态
  toasts: [],              // 全局消息队列
});
app.provide('globalState', globalState);
```

### API 封装层（`api.js`）

```javascript
const API_BASE = '';

const api = {
  // Root 管理
  async listRoots() { return fetch(`${API_BASE}/api/roots`).then(r => r.json()); },
  async addRoot(page_id) { return fetch(`${API_BASE}/api/roots`, {method:'POST', body:JSON.stringify({page_id})}); },
  async deleteRoot(root_id) { return fetch(`${API_BASE}/api/roots/${root_id}`, {method:'DELETE'}); },
  async getRootTree(root_id) { return fetch(`${API_BASE}/api/roots/${root_id}/tree`).then(r => r.json()); },
  
  // 页面列表
  async listPages() { return fetch(`${API_BASE}/api/pages`).then(r => r.json()); },
  async toggleVector(page_id) { return fetch(`${API_BASE}/api/pages/${page_id}/vector-toggle`, {method:'POST'}); },
  async triggerVectorize(page_id) { return fetch(`${API_BASE}/api/pages/${page_id}/vectorize`, {method:'POST'}); },
  
  // 同步
  async syncPages(page_ids) { return fetch(`${API_BASE}/api/sync`, {method:'POST', body:JSON.stringify({page_ids})}); },
  async getSyncProgress(sync_id) { return fetch(`${API_BASE}/api/sync/${sync_id}/progress`).then(r => r.json()); },
  
  // 检索调试
  async debugSearch(params) { return fetch(`${API_BASE}/api/search/debug`, {method:'POST', body:JSON.stringify(params)}).then(r => r.json()); },
  
  // 检索历史
  async listHistory(limit=50, source=null) { 
    const q = new URLSearchParams({limit});
    if (source) q.append('source', source);
    return fetch(`${API_BASE}/api/search/history?${q}`).then(r => r.json());
  },
  async getHistory(history_id) { return fetch(`${API_BASE}/api/search/history/${history_id}`).then(r => r.json()); },
  async replayHistory(history_id) { return fetch(`${API_BASE}/api/search/history/${history_id}/replay`, {method:'POST'}).then(r => r.json()); },
  async deleteHistory(history_id) { return fetch(`${API_BASE}/api/search/history/${history_id}`, {method:'DELETE'}); },
  async clearHistory() { return fetch(`${API_BASE}/api/search/history?clear=all`, {method:'DELETE'}); },
  
  // Chunk 详情
  async getPageDetail(page_id) { return fetch(`${API_BASE}/api/pages/${page_id}`).then(r => r.json()); },
  
  // Worker 状态
  async getWorkerStatus() { return fetch(`${API_BASE}/api/worker/status`).then(r => r.json()); },
};
```

---

## 各页面设计

### 1. AppLayout.vue（主布局）

```
┌─────────────────────────────────────────────────────────────┐
│ ┌────────┐  ┌────────────────────────────────────────────┐ │
│ │        │  │ 标题栏                                      │ │
│ │  Logo  │  │ Breadcrumb: Root 管理                       │ │
│ │ RAG KB │  ├────────────────────────────────────────────┤ │
│ │        │  │                                             │ │
│ ├────────┤  │  内容区（router-view）                       │ │
│ │ 🗂 Root │  │                                             │ │
│ │ 📄 页面│  │                                             │ │
│ │ 🔍 调试│  │                                             │ │
│ │ 📜 历史│  │                                             │ │
│ │        │  │                                             │ │
│ └────────┘  └────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

**Arco 组件**：`a-layout`, `a-layout-sider`, `a-menu`, `a-layout-header`, `a-layout-content`

### 2. RootManager.vue（Root 管理）

**布局**：
- 顶部：标题"Notion Root 管理" + 描述文字 + [+ 添加 Root] 按钮
- 中间：Root 卡片网格（`a-grid`），每个卡片显示标题、ID、添加时间、操作按钮
- 点击卡片展开：页面树（`PageTree.vue` 递归组件）+ 批量操作栏

**Arco 组件**：`a-card`, `a-grid`, `a-tree`, `a-button`, `a-modal`（添加 Root 弹窗）, `a-input`

### 3. PageList.vue（页面列表）

**布局**：
- 顶部：统计卡片行（总页面数、已索引、待处理、失败）
- 中间：表格（`a-table`），列：页面标题、chunk数、image数、向量开关（`a-switch`）、向量状态（`StatusTag.vue`）、进度条（`a-progress`）、操作

**Arco 组件**：`a-table`, `a-switch`, `a-progress`, `a-tag`, `a-button`

### 4. ChunkInspector.vue（Chunk 详情）

**布局**：
- 左右分栏（`a-row :gutter=24`）：
  - 左：Markdown 渲染（`marked.js`）
  - 右：Chunk 卡片列表（`a-card` 小卡片，含类型标签、header_path、向量状态圆点）

**Arco 组件**：`a-row`, `a-col`, `a-card`, `a-tag`

### 5. SearchDebug.vue（检索调试）

**布局**：
- 左侧：参数面板（`a-card`，固定宽度 320px）
  - Query 输入（`a-textarea`）
  - Dense/Sparse 权重（两个 `a-slider`，联动）
  - TopK（`a-input-number`）
  - Min Similarity（`a-slider`）
  - Rerank 模型（`a-select`）
  - 元数据过滤（动态 `a-select` + `a-checkbox-group`）
  - 上下文扩展（`a-radio-group`，三个选项）
  - [开始检索] 按钮（`a-button type='primary'`）
- 右侧：结果面板
  - 统计栏（`a-descriptions`）：Dense数、Sparse数、过滤后、融合后、ReRank后、耗时
  - 结果列表：结果卡片（`a-card`，含 Rank、分数条、元数据、内容）

**Arco 组件**：`a-slider`, `a-input-number`, `a-select`, `a-radio-group`, `a-checkbox-group`, `a-descriptions`, `a-card`, `a-button`, `a-textarea`

### 6. SearchHistory.vue（检索历史）

**布局**：
- 顶部：标题 + 来源筛选（`a-select`）+ 查询搜索（`a-input-search`）+ [清空全部] 按钮
- 中间：表格（`a-table`），列：时间、来源标签（`a-tag`，颜色区分）、查询（截断）、结果数、Top分、操作列（[预览] + [删除]）

**Arco 组件**：`a-table`, `a-tag`, `a-select`, `a-input-search`, `a-button`, `a-popconfirm`

---

## 后端调整（最小化）

`web/web_server.py` 只需确保：
1. `index.html` 能被正确返回（已经是 `/` 路由）
2. `static/` 目录正确挂载（已经是 `app.mount("/static", ...)`）
3. **新增**：确保 Vue 的 history 模式能回退到 `index.html`

```python
# 在 create_app 中，所有 API 路由之后添加
@app.get("/{full_path:path}")
async def spa_catch_all(full_path: str) -> FileResponse:
    """SPA fallback: serve index.html for all non-API routes."""
    return FileResponse(str(static_dir / "index.html"))
```

> **后端 API 完全不变**，所有已有 `/api/*` 路由保持原样。

---

## 实现步骤

### Phase 1：前端基础设施搭建（1.5h）

1. **创建目录结构**：
   ```
   web/static/components/
   web/static/views/
   ```

2. **重写 `index.html`**：
   - 引入 Vue 3 + Vue Router + Arco Design + marked + highlight CDN
   - 挂载点 `<div id="app"></div>`
   - 引入 `app.js`

3. **编写 `app.js`**：
   - Vue App 创建
   - Vue Router 配置（5 个路由）
   - 全局状态 `globalState`（reactive）
   - Arco Design 注册（`ArcoVue`）
   - 自定义主题色覆盖（CSS 变量）

4. **编写 `api.js`**：
   - 所有后端 API 封装函数

5. **编写 `components/AppLayout.vue`**：
   - 侧边栏 + 内容区框架
   - 导航菜单与路由联动
   - 飞书风格样式

### Phase 2：Root 管理页（1.5h）

1. **编写 `views/RootManager.vue`**：
   - Root 卡片网格
   - 添加 Root 弹窗（输入 page_id，验证）
   - 页面树展开（递归 `PageTree.vue`）
   - 多选 + 批量同步
   - 同步进度展示

2. **编写 `components/PageTree.vue`**：
   - 递归渲染 Notion 页面树
   - Checkbox 多选
   - 展开/折叠

### Phase 3：页面列表页（1h）

1. **编写 `views/PageList.vue`**：
   - 统计卡片（`a-statistic`）
   - 表格（`a-table`）
   - 向量开关（`a-switch`）
   - 状态标签（`StatusTag.vue`）
   - 进度条（`a-progress`）
   - 2 秒轮询刷新

2. **编写 `components/StatusTag.vue`**：
   - 四种状态的颜色和图标映射

### Phase 4：Chunk 详情页（0.5h）

1. **编写 `views/ChunkInspector.vue`**：
   - 左右分栏布局
   - Markdown 渲染
   - Chunk 卡片列表

### Phase 5：检索调试页（2h）

1. **编写 `views/SearchDebug.vue`**：
   - 左侧参数面板（全部表单组件）
   - 权重滑动杆联动逻辑
   - 动态元数据过滤添加/删除
   - 右侧结果面板
   - 统计栏
   - 结果卡片（含 `ScoreBar.vue`）

2. **编写 `components/ScoreBar.vue`**：
   - 横向 5 段分数可视化条
   - Tooltip 显示精确数值

3. **URL 参数解析**：`history_id` 自动回放

### Phase 6：检索历史页（1h）

1. **编写 `views/SearchHistory.vue`**：
   - 筛选栏（来源 + 查询搜索）
   - 表格（时间、来源标签、查询、结果数、Top分、操作）
   - 预览跳转（携带 history_id）
   - 清空全部（二次确认）

### Phase 7：后端微调 + 集成验收（1.5h）

1. **修改 `web/web_server.py`**：
   - 添加 SPA catch-all 路由

2. **手动测试**：
   - 各页面路由切换
   - Root 管理全流程
   - 页面列表轮询
   - 检索调试调参
   - 检索历史回放
   - 飞书风格视觉检查

3. **边界测试**：
   - 直接刷新 `/search-debug` → 验证 SPA fallback 生效
   - 无数据状态 → 验证 EmptyState 组件显示
   - 移动端适配 → 侧边栏折叠

---

## 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `web/static/index.html` | **重写** | SPA 入口，引入 CDN |
| `web/static/app.js` | **新增** | Vue App + Router + 全局状态 |
| `web/static/api.js` | **新增** | 后端 API 封装层 |
| `web/static/components/AppLayout.vue` | **新增** | 主布局框架 |
| `web/static/components/PageHeader.vue` | **新增** | 页面顶部标题栏 |
| `web/static/components/ScoreBar.vue` | **新增** | 检索分数可视化条 |
| `web/static/components/StatusTag.vue` | **新增** | 向量状态标签 |
| `web/static/components/PageTree.vue` | **新增** | Notion 页面树（递归） |
| `web/static/components/SyncProgress.vue` | **新增** | 同步进度模态框 |
| `web/static/components/EmptyState.vue` | **新增** | 空状态 |
| `web/static/views/RootManager.vue` | **新增** | Root 管理页 |
| `web/static/views/PageList.vue` | **新增** | 页面列表页 |
| `web/static/views/ChunkInspector.vue` | **新增** | Chunk 详情页 |
| `web/static/views/SearchDebug.vue` | **新增** | 检索调试页 |
| `web/static/views/SearchHistory.vue` | **新增** | 检索历史页 |
| `web/web_server.py` | 修改 | 添加 SPA catch-all 路由 |

> **删除**：原有的单文件 `index.html` 中的内联 CSS/JS 逻辑（保留文件作为入口）

---

## 验收标准

| # | 验收项 | 通过标准 |
|---|--------|----------|
| 1 | Vue SPA 正常运行 | 访问 `/` 加载 Vue App，无 console 报错 |
| 2 | 路由切换 | 点击侧边栏菜单，内容区切换，URL 变化，无页面刷新 |
| 3 | 直接刷新 | 访问 `/search-debug` 直接刷新，页面正常加载 |
| 4 | 飞书风格 | 主色 `#3370FF`，卡片圆角 8px，表格行高 48px，整体视觉清爽 |
| 5 | Root 管理 | 添加/删除 Root、展开树、多选同步功能完整 |
| 6 | 页面列表 | 向量开关、状态标签、进度条、轮询刷新正常 |
| 7 | 检索调试 | 权重联动、调参检索、分数可视化、结果展示正常 |
| 8 | 检索历史 | 列表展示、来源筛选、预览跳转、清空功能正常 |
| 9 | 后端兼容 | 所有原有 `/api/*` 接口无需修改即可正常工作 |
| 10 | 移动端 | 侧边栏可折叠，内容区自适应（至少平板尺寸可用） |

---

## 风险与应对

| 风险 | 影响 | 可能性 | 应对策略 |
|------|------|--------|----------|
| CDN 加载失败 | 页面白屏 | 低 | 提供本地 fallback（下载 CDN 文件到 `static/lib/`） |
| Arco Design CDN 不支持某些组件 | 组件无法使用 | 低 | 备选 Element Plus CDN；或手写组件 |
| Vue 3 CDN 版本兼容性 | 某些语法不支持 | 低 | 使用 Vue 3.3+ 稳定版本，避免实验性语法 |
| 老浏览器不支持 | 页面无法打开 | 低 | 项目目标用户为开发者，要求现代浏览器 |
| SPA fallback 与 API 路由冲突 | API 被错误拦截 | 低 | catch-all 路由放在最后，且排除 `/api` 前缀 |

---

## 修订历史

| 版本 | 日期 | 作者 | 变更内容 |
|------|------|------|----------|
| v1.0 | 2026-08-12 | Claude | 前端全面重构计划：Vue 3 CDN + Arco Design Vue CDN，飞书风格设计规范，SPA 架构，5 个功能页面组件化拆分，后端 API 零变更 |

---

*本文档为前端全面重构的权威实施计划。后端 API 完全保持兼容，所有变更仅限前端 `web/static/` 目录。*
