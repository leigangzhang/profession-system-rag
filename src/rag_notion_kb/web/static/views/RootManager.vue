<template>
  <div class="page-view">
    <div class="page-stack">
      <PageHeader
        title="Notion Root 管理"
        description="管理同步根页面，展开页面树后可选择页面进行增量同步。"
      >
        <template #actions>
          <a-button type="primary" @click="openAddModal">
            <template #icon><IconPlus /></template>
            添加 Root
          </a-button>
        </template>
      </PageHeader>

      <div v-if="loading" class="state-panel">
        <a-spin :size="24" />
        <span>正在加载 Root 页面…</span>
      </div>

      <div v-else-if="loadError" class="state-panel error-panel">
        <IconExclamationCircle />
        <span>{{ loadError }}</span>
        <a-button size="small" @click="loadRoots">重试</a-button>
      </div>

      <EmptyState
        v-else-if="roots.length === 0"
        title="尚未配置 Root 页面"
        description="添加一个 Notion 页面作为同步入口，系统会在后台建立页面树缓存。"
      >
        <a-button type="primary" @click="openAddModal">
          <template #icon><IconPlus /></template>
          添加 Root
        </a-button>
      </EmptyState>

      <div v-else class="roots-list">
        <section
          v-for="root in roots"
          :key="root.root_id"
          class="root-card"
        >
          <header class="root-card-header">
            <button
              type="button"
              class="root-summary"
              @click="toggleTree(root.root_id)"
            >
              <span class="root-summary-icon">
                <IconFolder />
              </span>
              <span class="root-summary-copy">
                <strong class="truncate" :title="root.page_title">
                  {{ root.page_title }}
                </strong>
                <span class="root-id mono">{{ root.root_id }}</span>
              </span>
            </button>

            <div class="root-actions">
              <span class="root-cache-time">
                {{ cacheLabel(root.root_id) }}
              </span>
              <a-button
                size="small"
                :loading="treeLoading[root.root_id]"
                @click.stop="toggleTree(root.root_id)"
              >
                {{ expandedRoots[root.root_id] ? '收起树' : '展开树' }}
              </a-button>
              <a-button
                size="small"
                :loading="refreshing[root.root_id]"
                @click="refreshTree(root.root_id)"
              >
                <template #icon><IconRefresh /></template>
                刷新
              </a-button>
              <a-button
                size="small"
                status="danger"
                @click="openDeleteModal(root)"
              >
                <template #icon><IconDelete /></template>
                删除
              </a-button>
            </div>
          </header>

          <a-alert v-if="treeError[root.root_id]" type="error" closable>
            {{ treeError[root.root_id] }}
          </a-alert>

          <div v-show="expandedRoots[root.root_id]" class="tree-panel">
            <div v-if="treeLoading[root.root_id]" class="tree-state">
              <a-spin size="20" />
              <span>正在加载页面树…</span>
            </div>
            <div
              v-else-if="treeData[root.root_id] && treeData[root.root_id].length"
              class="tree-scroll"
            >
              <PageTree
                :nodes="treeData[root.root_id]"
                :selected="selectedPages"
                :sync-info="syncInfo"
                :syncing="syncingRootId === root.root_id"
                @toggle="handleTreeToggle"
              />
            </div>
            <EmptyState
              v-else-if="treeLoaded[root.root_id]"
              title="没有发现页面"
              description="可以点击刷新重新拉取该 Root 下的页面树。"
            />
            <div v-else class="tree-state">
              <span class="muted">展开后加载页面树</span>
            </div>
          </div>
        </section>
      </div>
    </div>

    <transition name="batch-bar-fade">
      <div v-if="hasExpandedTree" class="batch-bar">
        <div class="batch-bar-info">
          <IconCheckCircle />
          <span>已选择 {{ selectedCount }} 个页面</span>
        </div>
        <div class="batch-bar-actions">
          <a-button
            size="small"
            :disabled="selectedCount === 0"
            @click="clearSelection"
          >
            取消全选
          </a-button>
          <a-button
            type="primary"
            size="small"
            :loading="Boolean(syncingRootId)"
            :disabled="selectedCount === 0 || Boolean(syncingRootId)"
            @click="startBatchSync"
          >
            同步选中页面
          </a-button>
        </div>
      </div>
    </transition>

    <a-modal
      v-model:visible="addVisible"
      title="添加 Notion Root 页面"
      :mask-closable="false"
      :esc-to-close="!adding"
      @before-ok="submitAddRoot"
      @cancel="resetAddModal"
    >
      <div class="modal-form">
        <label for="root-page-id">Notion Page ID</label>
        <a-input
          id="root-page-id"
          v-model="addPageId"
          placeholder="a1b2c3d4-e5f6-7890-abcd-ef1234567890"
          allow-clear
          :disabled="adding"
          @press-enter="submitEnterAdd"
        />
        <a-alert v-if="addError" type="error" class="modal-error">
          {{ addError }}
        </a-alert>
        <p class="modal-help">
          系统会先向 Notion 验证该页面，成功后自动在后台缓存页面树。
        </p>
      </div>
    </a-modal>

    <a-modal
      v-model:visible="deleteVisible"
      title="删除 Root 页面"
      :mask-closable="false"
      @before-ok="confirmDeleteRoot"
      @cancel="closeDeleteModal"
    >
      <p v-if="deleteTarget" class="delete-confirm">
        确定删除 <strong>{{ deleteTarget.page_title }}</strong> 吗？该 Root
        下的同步数据、向量和页面树缓存也会一并删除。
      </p>
    </a-modal>

    <SyncProgress
      v-model:visible="syncVisible"
      :sync-id="syncId"
      @finish="handleSyncFinish"
    />
  </div>
</template>

<script>
const { computed, onMounted, reactive, ref } = Vue;

export default {
  name: 'RootManager',
  setup() {
    const api = window.RagApi;
    const utils = window.UIUtils;
    const toast = window.RagApp.toast;
    const router = window.RagApp.router;

    const roots = ref([]);
    const loading = ref(true);
    const loadError = ref('');

    const expandedRoots = reactive({});
    const treeData = reactive({});
    const treeLoading = reactive({});
    const treeLoaded = reactive({});
    const treeError = reactive({});
    const cacheTimes = reactive({});
    const refreshing = reactive({});
    const syncInfo = reactive({});
    const treeMap = reactive({});
    const selectedPages = reactive({});
    const syncingRootId = ref(null);

    const addVisible = ref(false);
    const addPageId = ref('');
    const adding = ref(false);
    const addError = ref('');

    const deleteVisible = ref(false);
    const deleteTarget = ref(null);

    const syncVisible = ref(false);
    const syncId = ref('');

    const hasExpandedTree = computed(() =>
      Object.values(expandedRoots).some(Boolean)
    );
    const selectedCount = computed(() => Object.keys(selectedPages).length);
    const currentExpandedRoot = computed(() =>
      Object.keys(expandedRoots).find((key) => expandedRoots[key])
    );

    function cacheLabel(rootId) {
      if (!cacheTimes[rootId]) return '未缓存';
      return `缓存于 ${utils.fmtTimeShort(cacheTimes[rootId])}`;
    }

    function flattenSyncInfo(nodes) {
      if (!nodes) return;
      nodes.forEach((node) => {
        if (node.sync_status || node.synced_at) {
          syncInfo[node.page_id] = {
            status: node.sync_status,
            syncedAt: node.synced_at || null,
          };
        } else {
          delete syncInfo[node.page_id];
        }
        if (node.children && node.children.length) {
          flattenSyncInfo(node.children);
        }
      });
    }

    function buildTreeMap(nodes, rootId) {
      if (!nodes) return;
      nodes.forEach((node) => {
        treeMap[node.page_id] = {
          rootId,
          children: (node.children || []).map((child) => child.page_id),
        };
        if (node.children && node.children.length) {
          buildTreeMap(node.children, rootId);
        }
      });
    }

    async function loadCacheMeta(rootId) {
      try {
        const data = await api.getRootTree(rootId);
        if (data.cached_at) cacheTimes[rootId] = data.cached_at;
      } catch {
        // The tree is loaded lazily when expanded.
      }
    }

    async function loadRoots() {
      loading.value = true;
      loadError.value = '';
      try {
        roots.value = await api.listRoots();
        await Promise.allSettled(roots.value.map((root) => loadCacheMeta(root.root_id)));
      } catch (error) {
        loadError.value = utils.errorMessage(error, '加载 Root 页面失败');
      } finally {
        loading.value = false;
      }
    }

    async function loadTree(rootId) {
      treeLoading[rootId] = true;
      treeError[rootId] = '';
      try {
        const data = await api.getRootTree(rootId);
        treeData[rootId] = data.tree || [];
        treeLoaded[rootId] = true;
        if (data.cached_at) cacheTimes[rootId] = data.cached_at;
        flattenSyncInfo(data.tree || []);
        buildTreeMap(data.tree || [], rootId);
      } catch (error) {
        treeError[rootId] = utils.errorMessage(error, '加载页面树失败');
      } finally {
        treeLoading[rootId] = false;
      }
    }

    async function toggleTree(rootId) {
      if (expandedRoots[rootId]) {
        expandedRoots[rootId] = false;
        return;
      }
      expandedRoots[rootId] = true;
      if (!treeData[rootId]) await loadTree(rootId);
    }

    async function refreshTree(rootId) {
      refreshing[rootId] = true;
      try {
        const data = await api.refreshRootTree(rootId);
        if (data.cached_at) cacheTimes[rootId] = data.cached_at;
        if (expandedRoots[rootId]) {
          await loadTree(rootId);
        } else {
          treeData[rootId] = [];
          treeLoaded[rootId] = false;
        }
        toast('页面树已刷新', 'success');
      } catch (error) {
        toast(utils.errorMessage(error, '刷新页面树失败'), 'error');
      } finally {
        refreshing[rootId] = false;
      }
    }

    function handleTreeToggle({ pageId, checked }) {
      if (checked) selectedPages[pageId] = true;
      else delete selectedPages[pageId];
      cascadeSelection(pageId, checked);
    }

    function cascadeSelection(pageId, checked) {
      const node = treeMap[pageId];
      if (!node) return;
      node.children.forEach((childId) => {
        if (checked) selectedPages[childId] = true;
        else delete selectedPages[childId];
        cascadeSelection(childId, checked);
      });
    }

    function clearSelection() {
      Object.keys(selectedPages).forEach((pageId) => {
        delete selectedPages[pageId];
      });
    }

    function openAddModal() {
      addVisible.value = true;
      addPageId.value = '';
      addError.value = '';
    }

    function resetAddModal() {
      addPageId.value = '';
      addError.value = '';
    }

    async function submitAddRoot() {
      const pageId = addPageId.value.trim();
      if (!pageId) {
        addError.value = '请输入 Notion Page ID';
        return false;
      }
      adding.value = true;
      addError.value = '';
      try {
        await api.addRoot(pageId);
        toast('Root 页面已添加', 'success');
        addVisible.value = false;
        await loadRoots();
        return true;
      } catch (error) {
        addError.value = utils.errorMessage(error, '添加 Root 失败');
        return false;
      } finally {
        adding.value = false;
      }
    }

    function submitEnterAdd() {
      if (addVisible.value && !adding.value) submitAddRoot();
    }

    function openDeleteModal(root) {
      deleteTarget.value = root;
      deleteVisible.value = true;
    }

    function closeDeleteModal() {
      deleteTarget.value = null;
    }

    async function confirmDeleteRoot() {
      if (!deleteTarget.value) return false;
      try {
        await api.deleteRoot(deleteTarget.value.root_id);
        const rootId = deleteTarget.value.root_id;
        delete expandedRoots[rootId];
        delete treeData[rootId];
        delete cacheTimes[rootId];
        Object.keys(treeMap).forEach((pageId) => {
          if (treeMap[pageId].rootId === rootId) delete treeMap[pageId];
        });
        roots.value = roots.value.filter((root) => root.root_id !== rootId);
        Object.keys(syncInfo).forEach((pageId) => {
          delete syncInfo[pageId];
        });
        deleteVisible.value = false;
        deleteTarget.value = null;
        toast('Root 页面已删除', 'success');
        return true;
      } catch (error) {
        toast(utils.errorMessage(error, '删除 Root 失败'), 'error');
        return false;
      }
    }

    async function startBatchSync() {
      const pageIds = Object.keys(selectedPages);
      if (!pageIds.length || syncingRootId.value) return;
      syncingRootId.value = currentExpandedRoot.value || null;
      try {
        const result = await api.syncPages(pageIds);
        syncId.value = result.sync_id;
        syncVisible.value = true;
      } catch (error) {
        syncingRootId.value = null;
        toast(utils.errorMessage(error, '启动同步失败'), 'error');
      }
    }

    async function refreshExpandedTrees() {
      const expanded = Object.keys(expandedRoots).filter(
        (rootId) => expandedRoots[rootId]
      );
      await Promise.all(expanded.map((rootId) => loadTree(rootId)));
    }

    async function handleSyncFinish(task) {
      syncingRootId.value = null;
      if (task && task.status === 'completed') {
        toast('批量同步完成', 'success');
        await refreshExpandedTrees();
      } else if (task && task.status === 'failed') {
        toast(task.error_message || '批量同步失败', 'error');
      }
    }

    onMounted(loadRoots);

    return {
      roots,
      loading,
      loadError,
      loadRoots,
      expandedRoots,
      treeData,
      treeLoading,
      treeLoaded,
      treeError,
      cacheTimes,
      refreshing,
      syncInfo,
      selectedPages,
      syncingRootId,
      addVisible,
      addPageId,
      adding,
      addError,
      deleteVisible,
      deleteTarget,
      syncVisible,
      syncId,
      hasExpandedTree,
      selectedCount,
      cacheLabel,
      toggleTree,
      refreshTree,
      handleTreeToggle,
      clearSelection,
      openAddModal,
      resetAddModal,
      submitAddRoot,
      submitEnterAdd,
      openDeleteModal,
      closeDeleteModal,
      confirmDeleteRoot,
      startBatchSync,
      handleSyncFinish,
    };
  },
};
</script>

<style scoped>
.state-panel {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  min-height: 160px;
  color: var(--text-secondary);
  background: #fff;
  border: 1px solid var(--border-light);
  border-radius: 8px;
}

.error-panel {
  color: #f53f3f;
}

.roots-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.root-card {
  overflow: hidden;
  background: #fff;
  border: 1px solid var(--border-light);
  border-radius: 8px;
  box-shadow: var(--shadow-card);
}

.root-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 16px 18px;
}

.root-summary {
  display: flex;
  align-items: center;
  min-width: 0;
  padding: 0;
  color: inherit;
  text-align: left;
  background: none;
  border: 0;
  cursor: pointer;
}

.root-summary-icon {
  display: grid;
  flex: 0 0 36px;
  width: 36px;
  height: 36px;
  margin-right: 11px;
  place-items: center;
  color: var(--primary-color);
  background: var(--primary-light);
  border-radius: 8px;
  font-size: 18px;
}

.root-summary-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}

.root-summary-copy strong {
  max-width: min(42vw, 520px);
  color: var(--text-primary);
  font-size: 15px;
  font-weight: 600;
}

.root-id {
  color: var(--text-tertiary);
  font-size: 11px;
}

.root-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 7px;
}

.root-cache-time {
  margin-right: 6px;
  color: var(--text-tertiary);
  font-size: 11px;
}

.tree-panel {
  padding: 0 10px 10px;
  border-top: 1px solid var(--border-light);
}

.tree-scroll {
  max-height: 560px;
  padding: 8px 0;
  overflow: auto;
}

.tree-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  min-height: 110px;
  color: var(--text-secondary);
}

.batch-bar {
  position: fixed;
  right: 24px;
  bottom: 24px;
  z-index: 20;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 10px 12px 10px 16px;
  background: #1f2329;
  border-radius: 9px;
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.18);
}

.batch-bar-info {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #fff;
  font-size: 13px;
}

.batch-bar-actions {
  display: flex;
  gap: 7px;
}

.batch-bar-fade-enter-active,
.batch-bar-fade-leave-active {
  transition: opacity 160ms ease, transform 160ms ease;
}

.batch-bar-fade-enter-from,
.batch-bar-fade-leave-to {
  opacity: 0;
  transform: translateY(8px);
}

.modal-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.modal-form label {
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
}

.modal-help {
  margin: 0;
  color: var(--text-tertiary);
  font-size: 12px;
  line-height: 1.6;
}

.modal-error {
  margin-top: 2px;
}

.delete-confirm {
  margin: 4px 0 10px;
  color: var(--text-secondary);
  line-height: 1.7;
}

.delete-confirm strong {
  color: var(--text-primary);
}

@media (max-width: 860px) {
  .root-card-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .root-actions {
    width: 100%;
    flex-wrap: wrap;
  }

  .root-cache-time {
    width: 100%;
    margin: 0 0 4px;
  }

  .batch-bar {
    right: 12px;
    bottom: 12px;
    left: 12px;
    flex-direction: column;
  }
}
</style>
