<template>
  <div class="page-view">
    <div class="page-stack">
      <PageHeader
        title="页面列表"
        description="查看已同步页面、控制向量解析开关，并跟踪后台解析进度。"
      >
        <template #actions>
          <a-input-search
            v-model="keyword"
            class="page-search"
            placeholder="搜索页面标题"
            allow-clear
            @search="applyKeyword"
            @clear="keyword = ''"
          />
          <a-button :loading="loading" @click="loadPages">
            <template #icon><IconRefresh /></template>
            刷新
          </a-button>
        </template>
      </PageHeader>

      <div class="stats-grid">
        <div class="stat-card">
          <span class="stat-label">总页面</span>
          <strong class="stat-value">{{ pages.length }}</strong>
          <span class="stat-foot">已同步内容</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">已解析</span>
          <strong class="stat-value success">{{ counts.indexed }}</strong>
          <span class="stat-foot">向量已写入</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">解析中</span>
          <strong class="stat-value">{{ counts.indexing }}</strong>
          <span class="stat-foot">后台 Worker</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">待处理</span>
          <strong class="stat-value warning">{{ counts.pending }}</strong>
          <span class="stat-foot">等待解析</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">解析失败</span>
          <strong class="stat-value" :class="{ danger: counts.failed > 0 }">
            {{ counts.failed }}
          </strong>
          <span class="stat-foot">需要重试</span>
        </div>
        <div class="stat-card">
          <span class="stat-label">零向量块</span>
          <strong class="stat-value" :class="{ warning: counts.zeroVectors > 0 }">
            {{ counts.zeroVectors }}
          </strong>
          <span class="stat-foot">可能影响召回</span>
        </div>
      </div>

      <div class="surface-panel">
        <div class="table-toolbar">
          <div class="table-toolbar-left">
            <a-select
              v-model="selectedRoot"
              class="root-select"
              placeholder="全部页面"
              allow-clear
              @change="selectedRoot = $event || null"
            >
              <a-option :value="null">全部页面</a-option>
              <a-option
                v-for="root in roots"
                :key="root.root_id"
                :value="root.root_id"
              >
                {{ root.page_title }}
              </a-option>
            </a-select>
            <span class="table-count">
              显示 {{ filteredPages.length }} / {{ pages.length }} 个页面
            </span>
          </div>
          <div class="worker-summary">
            <span
              class="worker-pill"
              :class="{ running: state.workerStatus.running }"
            >
              {{ state.workerStatus.running ? 'Worker 在线' : 'Worker 离线' }}
            </span>
          </div>
        </div>

        <a-alert v-if="loadError" type="error" closable>
          {{ loadError }}
        </a-alert>

        <a-table
          :data="filteredPages"
          :loading="loading"
          :pagination="false"
          :bordered="{ cell: true }"
          :scroll="{ x: 1180 }"
          row-key="page_id"
          class="pages-table"
        >
          <template #columns>
            <a-table-column
              title="页面"
              data-index="page_title"
              :width="280"
              fixed="left"
            >
              <template #cell="{ record }">
                <button
                  type="button"
                  class="page-title-link"
                  :title="record.page_title"
                  @click="openInspector(record.page_id)"
                >
                  {{ record.page_title }}
                </button>
              </template>
            </a-table-column>
            <a-table-column title="大小" :width="86">
              <template #cell="{ record }">
                {{ sizeLabel(record) }}
              </template>
            </a-table-column>
            <a-table-column title="图片" data-index="image_count" :width="76" />
            <a-table-column title="嵌入 / 分块" :width="132">
              <template #cell="{ record }">
                <span>
                  {{ embeddingCount(record) }}/{{ totalChunks(record) }}
                </span>
                <span
                  v-if="record.zero_vector_chunks > 0"
                  class="zero-count"
                  :title="`${record.zero_vector_chunks} 个零向量块`"
                >
                  {{ record.zero_vector_chunks }} zero
                </span>
              </template>
            </a-table-column>
            <a-table-column title="向量解析" :width="104">
              <template #cell="{ record }">
                <a-switch
                  :model-value="record.vector_enabled"
                  :loading="toggling[record.page_id]"
                  :disabled="record.status === 'skipped'"
                  @change="toggleVector(record, $event)"
                />
              </template>
            </a-table-column>
            <a-table-column title="解析状态" :width="220">
              <template #cell="{ record }">
                <div class="vector-cell">
                  <StatusTag
                    :status="record.vector_status"
                    :progress="record.vector_progress"
                    :stage="record.vector_stage"
                  />
                  <a-progress
                    v-if="record.vector_status === 'indexing'"
                    :percent="progress(record)"
                    size="small"
                    :show-text="false"
                  />
                </div>
              </template>
            </a-table-column>
            <a-table-column title="最近同步" :width="170">
              <template #cell="{ record }">
                <span class="time-cell">{{ fmtTime(record.last_synced_time) }}</span>
              </template>
            </a-table-column>
            <a-table-column title="最近解析" :width="170">
              <template #cell="{ record }">
                <span class="time-cell">{{ fmtTime(record.last_vectorized_time) }}</span>
              </template>
            </a-table-column>
            <a-table-column title="操作" :width="80" fixed="right">
              <template #cell="{ record }">
                <a-tooltip
                  :content="
                    record.vector_status === 'indexing'
                      ? '解析进行中'
                      : '重新解析'
                  "
                >
                  <a-button
                    type="text"
                    shape="circle"
                    size="small"
                    :loading="vectorizing[record.page_id]"
                    :disabled="!record.vector_enabled || record.vector_status === 'indexing'"
                    @click="triggerVectorize(record)"
                  >
                    <template #icon><IconSync /></template>
                  </a-button>
                </a-tooltip>
              </template>
            </a-table-column>
          </template>
          <template #empty>
            <EmptyState
              title="没有匹配的页面"
              :description="emptyDescription"
            />
          </template>
        </a-table>
      </div>
    </div>
  </div>
</template>

<script>
const { computed, inject, onBeforeUnmount, onMounted, reactive, ref } = Vue;

export default {
  name: 'PageList',
  setup() {
    const api = window.RagApi;
    const utils = window.UIUtils;
    const toast = window.RagApp.toast;
    const state = inject('globalState');

    const pages = ref([]);
    const roots = ref([]);
    const loading = ref(true);
    const loadError = ref('');
    const selectedRoot = ref(null);
    const keyword = ref('');
    const appliedKeyword = ref('');
    const toggling = reactive({});
    const vectorizing = reactive({});
    let pollTimer = null;

    const filteredPages = computed(() => {
      const rootId = selectedRoot.value;
      const query = appliedKeyword.value.trim().toLowerCase();
      return pages.value.filter((page) => {
        const rootMatches = !rootId || page.root_id === rootId;
        const titleMatches =
          !query ||
          String(page.page_title || '')
            .toLowerCase()
            .includes(query);
        return rootMatches && titleMatches;
      });
    });

    const counts = computed(() => {
      const result = {
        indexed: 0,
        indexing: 0,
        pending: 0,
        failed: 0,
        zeroVectors: 0,
      };
      pages.value.forEach((page) => {
        if (page.vector_status === 'indexed') result.indexed += 1;
        if (page.vector_status === 'indexing') result.indexing += 1;
        if (page.vector_status === 'pending') result.pending += 1;
        if (page.vector_status === 'failed') result.failed += 1;
        result.zeroVectors += Number(page.zero_vector_chunks) || 0;
      });
      return result;
    });

    const emptyDescription = computed(() => {
      if (pages.value.length === 0) {
        return '运行 rag-kb sync 同步 Notion 页面后，这里会展示页面列表。';
      }
      return '调整 Root 筛选或标题搜索条件。';
    });

    function sizeLabel(page) {
      return `${Math.round(Number(page.doc_size_kb) || 0)} KB`;
    }

    function totalChunks(page) {
      return Number(page.chunk_count) + Number(page.image_count);
    }

    function embeddingCount(page) {
      return Math.max(0, totalChunks(page) - Number(page.zero_vector_chunks));
    }

    function progress(page) {
      return utils.clampNumber(page.vector_progress, 0, 100, 0);
    }

    function fmtTime(value) {
      return utils.fmtTime(value);
    }

    function openInspector(pageId) {
      window.RagApp.openPage(pageId);
    }

    function applyKeyword(value) {
      appliedKeyword.value = value || '';
    }

    async function loadPages() {
      loading.value = true;
      loadError.value = '';
      try {
        const [nextPages, nextRoots] = await Promise.all([
          api.listPages(),
          api.listRoots(),
        ]);
        pages.value = nextPages || [];
        roots.value = nextRoots || [];
      } catch (error) {
        loadError.value = utils.errorMessage(error, '加载页面列表失败');
      } finally {
        loading.value = false;
      }
    }

    async function refreshPoll() {
      if (document.hidden) return;
      try {
        pages.value = await api.listPages();
      } catch {
        // Keep the current list and retry on the next interval.
      }
    }

    async function toggleVector(page, enabled) {
      toggling[page.page_id] = true;
      const previous = page.vector_enabled;
      try {
        const result = await api.toggleVector(page.page_id);
        const record = pages.value.find((item) => item.page_id === page.page_id);
        if (record) {
          record.vector_enabled = result.vector_enabled;
          if (result.vector_enabled) {
            record.vector_status = 'pending';
            record.vector_progress = 0;
            record.vector_stage = '';
            record.last_vectorized_time = null;
          }
        }
        toast(
          result.vector_enabled ? '已开启向量解析' : '已关闭向量解析',
          'success'
        );
      } catch (error) {
        page.vector_enabled = previous;
        toast(utils.errorMessage(error, '切换向量解析失败'), 'error');
      } finally {
        delete toggling[page.page_id];
      }
    }

    async function triggerVectorize(page) {
      vectorizing[page.page_id] = true;
      try {
        await api.triggerVectorize(page.page_id);
        const record = pages.value.find((item) => item.page_id === page.page_id);
        if (record) {
          record.vector_status = 'indexing';
          record.vector_progress = 0;
          record.vector_stage = 'preparing';
        }
        toast('已加入向量解析队列', 'success');
      } catch (error) {
        toast(utils.errorMessage(error, '触发解析失败'), 'error');
      } finally {
        delete vectorizing[page.page_id];
      }
    }

    onMounted(() => {
      loadPages();
      pollTimer = setInterval(refreshPoll, 3000);
    });

    onBeforeUnmount(() => {
      if (pollTimer) clearInterval(pollTimer);
    });

    return {
      pages,
      roots,
      loading,
      loadError,
      selectedRoot,
      keyword,
      filteredPages,
      counts,
      emptyDescription,
      toggling,
      vectorizing,
      state,
      sizeLabel,
      embeddingCount,
      totalChunks,
      progress,
      fmtTime,
      openInspector,
      applyKeyword,
      loadPages,
      toggleVector,
      triggerVectorize,
    };
  },
};
</script>

<style scoped>
.page-search {
  width: 230px;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(130px, 1fr));
  gap: 12px;
}

.stat-card {
  display: flex;
  min-height: 104px;
  flex-direction: column;
  padding: 15px 16px;
  background: #fff;
  border: 1px solid var(--border-light);
  border-radius: 8px;
  box-shadow: var(--shadow-card);
}

.stat-label {
  color: var(--text-secondary);
  font-size: 12px;
}

.stat-value {
  margin: 5px 0 2px;
  color: var(--text-primary);
  font-family: "SF Mono", "Menlo", monospace;
  font-size: 25px;
  line-height: 1.2;
}

.stat-foot {
  color: var(--text-tertiary);
  font-size: 11px;
}

.table-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.table-toolbar-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.root-select {
  width: 220px;
}

.table-count,
.worker-summary {
  color: var(--text-secondary);
  font-size: 12px;
}

.worker-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 8px;
  border-radius: 10px;
  background: var(--bg-hover);
}

.worker-pill::before {
  width: 6px;
  height: 6px;
  background: #c9cdd4;
  border-radius: 50%;
  content: "";
}

.worker-pill.running::before {
  background: #00b42a;
}

.page-title-link {
  display: block;
  max-width: 100%;
  padding: 0;
  overflow: hidden;
  color: var(--primary-color);
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  background: none;
  border: 0;
  cursor: pointer;
  font-size: 13px;
}

.page-title-link:hover {
  text-decoration: underline;
}

.zero-count {
  display: block;
  margin-top: 3px;
  color: #f53f3f;
  font-size: 11px;
}

.vector-cell {
  display: flex;
  min-width: 145px;
  flex-direction: column;
  gap: 5px;
}

.time-cell {
  color: var(--text-secondary);
  font-size: 12px;
  white-space: nowrap;
}

:deep(.pages-table .arco-table-td) {
  vertical-align: middle;
}

@media (max-width: 1200px) {
  .stats-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}

@media (max-width: 720px) {
  .stats-grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .page-search {
    width: 100%;
  }

  .table-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .root-select {
    width: 100%;
  }
}
</style>
