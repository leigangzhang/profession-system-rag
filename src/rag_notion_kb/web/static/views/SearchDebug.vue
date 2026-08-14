<template>
  <div class="page-view">
    <div class="page-stack debug-stack">
      <PageHeader
        title="检索调试"
        description="观察 Dense、Sparse、融合与 ReRank 各阶段的召回和打分变化。"
      />

      <div class="debug-layout">
        <aside class="debug-params">
          <div class="debug-params-scroll">
            <section class="form-section">
              <div class="form-section-title">查询问题</div>
              <a-textarea
                v-model="form.query"
                :auto-size="{ minRows: 4, maxRows: 8 }"
                placeholder="输入检索问题"
                @keydown="handleQueryKeydown"
              />
            </section>

            <section class="form-section">
              <div class="form-section-title">融合权重</div>
              <div class="weight-head">
                <span>Dense</span>
                <span class="mono">{{ denseWeight.toFixed(2) }}</span>
                <span>Sparse</span>
                <span class="mono">{{ sparseWeight.toFixed(2) }}</span>
              </div>
              <a-slider
                v-model="denseWeight"
                :min="0"
                :max="1"
                :step="0.01"
                :show-tooltip="true"
                :format-tooltip="(value) => Number(value).toFixed(2)"
              />
              <div class="weight-labels">
                <span>0</span>
                <span>0.5</span>
                <span>1</span>
              </div>
            </section>

            <section class="form-section">
              <div class="form-section-title">检索参数</div>
              <div class="field-grid">
                <label class="field">
                  <span>TopK</span>
                  <a-input-number
                    v-model="form.topK"
                    :min="1"
                    :max="100"
                    :step="1"
                  />
                </label>
                <label class="field">
                  <span>最大 Token</span>
                  <a-input-number
                    v-model="form.maxTokens"
                    :min="100"
                    :max="8000"
                    :step="100"
                  />
                </label>
              </div>

              <label class="field">
                <span class="field-label-row">
                  <span>最小相似度</span>
                  <span class="mono">{{ form.minSimilarity.toFixed(2) }}</span>
                </span>
                <a-slider
                  v-model="form.minSimilarity"
                  :min="0"
                  :max="1"
                  :step="0.01"
                  :show-tooltip="true"
                  :format-tooltip="(value) => Number(value).toFixed(2)"
                />
              </label>

              <label class="field">
                <span>ReRank 模型</span>
                <a-select
                  v-model="form.rerankModel"
                  allow-clear
                  placeholder="不使用 ReRank"
                  @change="rerankModelChanged"
                >
                  <a-option value="">不使用 ReRank</a-option>
                  <a-option value="qwen3-vl-rerank">qwen3-vl-rerank</a-option>
                  <a-option value="qwen3-vl-reranker">qwen3-vl-reranker</a-option>
                  <a-option
                    v-for="model in customRerankModels"
                    :key="model"
                    :value="model"
                  >
                    {{ model }}
                  </a-option>
                </a-select>
              </label>

              <a-checkbox v-model="form.skipRerank" @change="skipRerankChanged">
                跳过 ReRank
              </a-checkbox>
            </section>

            <section class="form-section">
              <div class="form-section-head">
                <div class="form-section-title">元数据过滤</div>
                <a-button type="text" size="small" @click="addFilter">
                  <template #icon><IconPlus /></template>
                  添加过滤
                </a-button>
              </div>

              <div v-if="filters.length === 0" class="filter-empty">
                当前没有过滤条件，将检索全部页面。
              </div>

              <div v-for="(filter, index) in filters" :key="index" class="filter-row">
                <div class="filter-row-head">
                  <a-select
                    v-model="filter.field"
                    size="small"
                    @change="filterFieldChanged(filter)"
                  >
                    <a-option
                      v-for="definition in filterDefinitions"
                      :key="definition.key"
                      :value="definition.key"
                      :disabled="isFieldUsed(definition.key, filter)"
                    >
                      {{ definition.label }}
                    </a-option>
                  </a-select>
                  <a-button
                    type="text"
                    size="mini"
                    status="danger"
                    @click="removeFilter(index)"
                  >
                    <template #icon><IconClose /></template>
                  </a-button>
                </div>
                <a-checkbox-group
                  v-model="filter.selected"
                  :options="optionsForFilter(filter.field)"
                />
              </div>
            </section>

            <section class="form-section">
              <div class="form-section-title">上下文扩展</div>
              <a-radio-group v-model="form.contextMode" direction="vertical">
                <a-radio value="none">不扩展</a-radio>
                <a-radio value="parent">扩展到上一级标题</a-radio>
                <a-radio value="h2">扩展到二级标题</a-radio>
              </a-radio-group>
            </section>
          </div>

          <div class="debug-params-footer">
            <a-button type="primary" long :loading="searching" @click="executeSearch">
              <template #icon><IconSearch /></template>
              {{ searching ? '检索中…' : '开始检索' }}
            </a-button>
          </div>
        </aside>

        <section class="debug-results">
          <div v-if="response" class="result-stats">
            <div class="result-stat">
              <span>Dense</span>
              <strong>{{ response.total_dense }}</strong>
            </div>
            <div class="result-stat">
              <span>Sparse</span>
              <strong>{{ response.total_sparse }}</strong>
            </div>
            <div class="result-stat">
              <span>过滤后</span>
              <strong>{{ response.total_after_filter }}</strong>
            </div>
            <div class="result-stat">
              <span>融合后</span>
              <strong>{{ response.total_after_fusion }}</strong>
            </div>
            <div class="result-stat">
              <span>ReRank 后</span>
              <strong>{{ response.total_after_rerank }}</strong>
            </div>
            <div class="result-stat">
              <span>耗时</span>
              <strong>{{ latencyLabel }}</strong>
            </div>
          </div>

          <div v-if="resultMessage" class="result-message" :class="{ error: resultError }">
            <IconExclamationCircle v-if="resultError" />
            <IconSearch v-else />
            <span>{{ resultMessage }}</span>
          </div>

          <div v-if="response && response.results.length" class="results-list">
            <article
              v-for="hit in response.results"
              :key="`${hit.page_id}-${hit.chunk_index}-${hit.rank}`"
              class="result-card"
            >
              <header class="result-card-head">
                <div class="result-title-block">
                  <span class="result-rank mono">#{{ hit.rank }}</span>
                  <h3>
                    <a
                      href="#"
                      @click.prevent="openChunk(hit)"
                    >
                      {{ hit.page_title }}
                    </a>
                  </h3>
                  <div class="result-meta truncate" :title="hit.header_path">
                    {{ hit.header_path || '未分类' }} · {{ hit.chunk_type }} ·
                    L{{ hit.header_level }}
                  </div>
                </div>
                <div class="result-final">
                  <strong>{{ formatScore(hit.scores.final_score) }}</strong>
                  <span>Final</span>
                </div>
              </header>

              <div class="score-bars">
                <ScoreBar
                  label="Dense"
                  :value="hit.scores.dense_score"
                  color="#3370ff"
                />
                <ScoreBar
                  label="Sparse"
                  :value="hit.scores.sparse_score"
                  color="#00b42a"
                />
                <ScoreBar
                  label="RRF"
                  :value="hit.scores.rrf_score"
                  color="#722ed1"
                />
                <ScoreBar
                  label="ReRank"
                  :value="hit.scores.rerank_score"
                  color="#ff7d00"
                />
                <ScoreBar
                  label="Final"
                  :value="hit.scores.final_score"
                  color="#f53f3f"
                />
              </div>

              <div
                class="result-content markdown-content"
                :class="{ expanded: expandedResults[hit.rank] }"
                v-html="renderResultContent(hit)"
              ></div>
              <button
                type="button"
                class="result-toggle"
                @click="toggleResult(hit.rank)"
              >
                {{ expandedResults[hit.rank] ? '收起' : '展开' }}
              </button>

              <div v-if="hit.matched_snippet" class="matched-snippet">
                匹配: {{ hit.matched_snippet }}
              </div>
            </article>
          </div>

          <EmptyState
            v-if="!searching && !response && !resultMessage"
            title="输入查询后开始检索"
            description="调整权重、TopK、相似度阈值和元数据过滤条件，查看各阶段结果。"
            icon="IconSearch"
          />
        </section>
      </div>
    </div>
  </div>
</template>

<script>
const {
  computed,
  nextTick,
  onMounted,
  reactive,
  ref,
  watch,
} = Vue;

export default {
  name: 'SearchDebug',
  setup() {
    const api = window.RagApi;
    const utils = window.UIUtils;
    const toast = window.RagApp.toast;
    const router = window.RagApp.router;
    const route = router.currentRoute.value;

    const form = reactive({
      query: '',
      topK: 5,
      maxTokens: 4000,
      minSimilarity: 0.4,
      rerankModel: '',
      skipRerank: false,
      contextMode: 'h2',
    });
    const denseWeight = ref(0.5);
    const filters = ref([]);
    const pageTitles = ref([]);
    const pageIds = ref([]);
    const customRerankModels = ref([]);
    const response = ref(null);
    const searching = ref(false);
    const resultMessage = ref('');
    const resultError = ref(false);
    const expandedResults = reactive({});

    const sparseWeight = computed(() =>
      Math.max(0, Math.min(1, 1 - Number(denseWeight.value)))
    );

    const filterDefinitions = computed(() => [
      { key: 'chunk_type', label: 'Chunk 类型' },
      { key: 'header_level', label: '标题层级' },
      { key: 'page_title', label: '页面标题' },
      { key: 'page_ids', label: 'Page ID' },
    ]);

    const latencyLabel = computed(() => {
      if (!response.value) return '';
      return utils.fmtLatency(response.value.latency_ms);
    });

    function optionsForFilter(field) {
      const values = {
        chunk_type: ['text', 'table', 'code', 'image'],
        header_level: ['1', '2', '3', '4', '5', '6'],
        page_title: pageTitles.value,
        page_ids: pageIds.value,
      };
      return (values[field] || []).map((value) => ({ label: value, value }));
    }

    function isFieldUsed(field, current) {
      return filters.value.some(
        (filter) => filter !== current && filter.field === field
      );
    }

    function addFilter() {
      const used = new Set(filters.value.map((filter) => filter.field));
      const available = filterDefinitions.value.find(
        (definition) => !used.has(definition.key)
      );
      const field = available ? available.key : 'chunk_type';
      filters.value.push({ field, selected: [] });
    }

    function removeFilter(index) {
      filters.value.splice(index, 1);
    }

    function filterFieldChanged(filter) {
      filter.selected = [];
    }

    function collectFilters() {
      return filters.value.reduce((result, filter) => {
        if (filter.field && filter.selected.length) {
          result[filter.field] = [...filter.selected];
        }
        return result;
      }, {});
    }

    function collectParams() {
      return {
        query: form.query.trim(),
        dense_weight: utils.clampNumber(denseWeight.value, 0, 1, 0.5),
        sparse_weight: sparseWeight.value,
        top_k: utils.clampNumber(form.topK, 1, 100, 5),
        min_similarity: utils.clampNumber(form.minSimilarity, 0, 1, 0.4),
        rerank_model: form.skipRerank ? '' : form.rerankModel,
        filters: collectFilters(),
        context_mode: form.contextMode,
        max_tokens: utils.clampNumber(form.maxTokens, 100, 8000, 4000),
      };
    }

    function rerankModelChanged() {
      if (form.rerankModel) form.skipRerank = false;
    }

    function skipRerankChanged(value) {
      if (value) form.rerankModel = '';
    }

    function handleQueryKeydown(event) {
      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        executeSearch();
      }
    }

    function formatScore(value) {
      return utils.formatScore(value);
    }

    function renderResultContent(hit) {
      return utils.renderChunkMarkdown(
        hit.expanded_text || hit.chunk_text || ''
      );
    }

    function bindResultContent() {
      const container = document.querySelector('.debug-results .results-list');
      if (!container) return;
      container.querySelectorAll('.result-content').forEach((content) => {
        content.querySelectorAll('img').forEach((image) => {
          image.addEventListener('error', utils.handleImageError);
          if (image.complete && image.naturalWidth === 0) {
            image.dispatchEvent(new Event('error'));
          }
        });
        utils.highlightCodeBlocks(content);
      });
    }

    async function executeSearch() {
      const params = collectParams();
      if (!params.query) {
        resultMessage.value = '请输入查询问题';
        resultError.value = true;
        toast('请输入查询问题', 'warning');
        return;
      }

      searching.value = true;
      response.value = null;
      resultMessage.value = '检索中…';
      resultError.value = false;
      Object.keys(expandedResults).forEach((key) => {
        delete expandedResults[key];
      });

      try {
        const nextResponse = await api.debugSearch(params);
        response.value = nextResponse;
        resultMessage.value = nextResponse.error || '';
        resultError.value = Boolean(nextResponse.error);
        if (nextResponse.error) {
          toast(nextResponse.error, 'error');
        } else if (!nextResponse.results.length) {
          resultMessage.value = '没有检索结果';
          resultError.value = false;
        }
        await nextTick();
        bindResultContent();
      } catch (error) {
        resultMessage.value = utils.errorMessage(error, '检索失败');
        resultError.value = true;
        toast(resultMessage.value, 'error');
      } finally {
        searching.value = false;
      }
    }

    function toggleResult(rank) {
      expandedResults[rank] = !expandedResults[rank];
    }

    function openChunk(hit) {
      window.RagApp.openPage(hit.page_id, hit.chunk_index);
    }

    async function loadFilterOptions() {
      try {
        const pages = await api.listPages();
        pageTitles.value = [...new Set(
          (pages || []).map((page) => page.page_title).filter(Boolean)
        )];
        pageIds.value = (pages || [])
          .map((page) => page.page_id)
          .filter(Boolean);
      } catch {
        // Metadata filters remain usable with static field values.
      }
    }

    function fillParams(params, source) {
      if (!params) return;
      form.query = params.query || '';
      const dense = Number(params.dense_weight);
      denseWeight.value = Number.isFinite(dense) ? dense : 0.5;
      form.topK = utils.clampNumber(params.top_k, 1, 100, 5);
      form.maxTokens = utils.clampNumber(
        params.max_tokens,
        100,
        8000,
        4000
      );
      let minimum = params.min_similarity;
      if (minimum === undefined) minimum = source === 'debug' ? 0.4 : 0;
      form.minSimilarity = utils.clampNumber(minimum, 0, 1, 0.4);

      const rerankModel = params.rerank_model || '';
      form.rerankModel = rerankModel;
      form.skipRerank = !rerankModel;
      if (
        rerankModel &&
        !['qwen3-vl-rerank', 'qwen3-vl-reranker'].includes(rerankModel) &&
        !customRerankModels.value.includes(rerankModel)
      ) {
        customRerankModels.value.push(rerankModel);
      }

      let mode = params.context_mode;
      if (!mode && params.expand_to_level !== undefined) {
        const level = Number(params.expand_to_level);
        if (level >= 100) mode = 'none';
        else if (level <= 1) mode = 'parent';
        else mode = 'h2';
      }
      form.contextMode = mode || 'h2';

      filters.value = [];
      Object.entries(params.filters || {}).forEach(([field, rawValues]) => {
        const values = Array.isArray(rawValues) ? rawValues : [rawValues];
        filters.value.push({ field, selected: values.map(String) });
      });
    }

    async function loadHistoryReplay(historyId) {
      if (!historyId) return;
      resultMessage.value = '加载历史参数…';
      resultError.value = false;
      response.value = null;
      try {
        const history = await api.getHistory(historyId);
        fillParams(history.params || { query: history.query }, history.source);
        if (history.snapshot) {
          response.value = history.snapshot;
          resultMessage.value = '';
          await nextTick();
          bindResultContent();
        } else {
          resultMessage.value = '该历史记录没有保存结果快照';
          resultError.value = true;
        }
      } catch (error) {
        resultMessage.value = utils.errorMessage(error, '无法加载历史参数');
        resultError.value = true;
        toast(resultMessage.value, 'error');
      }
    }

    watch(
      () => route.query.history_id,
      (historyId) => loadHistoryReplay(historyId)
    );

    onMounted(() => {
      loadFilterOptions();
      if (route.query.history_id) loadHistoryReplay(route.query.history_id);
    });

    return {
      form,
      denseWeight,
      sparseWeight,
      filters,
      customRerankModels,
      filterDefinitions,
      response,
      searching,
      resultMessage,
      resultError,
      expandedResults,
      latencyLabel,
      optionsForFilter,
      isFieldUsed,
      addFilter,
      removeFilter,
      filterFieldChanged,
      rerankModelChanged,
      skipRerankChanged,
      handleQueryKeydown,
      formatScore,
      renderResultContent,
      executeSearch,
      toggleResult,
      openChunk,
    };
  },
};
</script>

<style scoped>
.debug-stack {
  max-width: none;
}

.debug-layout {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  min-height: 640px;
}

.debug-params {
  position: sticky;
  top: 0;
  display: flex;
  flex: 0 0 330px;
  width: 330px;
  height: calc(100vh - 140px);
  min-height: 560px;
  flex-direction: column;
  overflow: hidden;
  background: #fff;
  border: 1px solid var(--border-light);
  border-radius: 8px;
  box-shadow: var(--shadow-card);
}

.debug-params-scroll {
  min-height: 0;
  flex: 1;
  padding: 16px;
  overflow: auto;
}

.form-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 0;
  border-bottom: 1px solid var(--border-light);
}

.form-section:first-child {
  padding-top: 0;
}

.form-section:last-child {
  border-bottom: 0;
}

.form-section-title {
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.4px;
  text-transform: uppercase;
}

.form-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.weight-head {
  display: grid;
  grid-template-columns: 45px 48px 1fr 48px;
  align-items: center;
  gap: 4px;
  color: var(--text-secondary);
  font-size: 12px;
}

.weight-head .mono {
  text-align: right;
}

.weight-labels {
  display: flex;
  justify-content: space-between;
  color: var(--text-tertiary);
  font-size: 10px;
}

.field-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.field {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 7px;
  color: var(--text-secondary);
  font-size: 12px;
}

.field-label-row {
  display: flex;
  justify-content: space-between;
}

.filter-empty {
  padding: 9px;
  color: var(--text-tertiary);
  background: var(--bg-hover);
  border-radius: 4px;
  font-size: 12px;
}

.filter-row {
  padding: 9px;
  background: #fafbfc;
  border: 1px solid var(--border-light);
  border-radius: 5px;
}

.filter-row-head {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 8px;
}

.debug-params-footer {
  flex: 0 0 auto;
  padding: 12px 16px;
  border-top: 1px solid var(--border-light);
}

.debug-results {
  min-width: 0;
  flex: 1;
}

.result-stats {
  display: grid;
  grid-template-columns: repeat(6, minmax(90px, 1fr));
  gap: 8px;
  margin-bottom: 14px;
}

.result-stat {
  display: flex;
  min-height: 64px;
  flex-direction: column;
  justify-content: center;
  padding: 11px 12px;
  background: #fff;
  border: 1px solid var(--border-light);
  border-radius: 7px;
  box-shadow: var(--shadow-card);
}

.result-stat span {
  color: var(--text-secondary);
  font-size: 10px;
  letter-spacing: 0.4px;
  text-transform: uppercase;
}

.result-stat strong {
  margin-top: 4px;
  color: var(--text-primary);
  font-family: "SF Mono", "Menlo", monospace;
  font-size: 17px;
}

.result-message {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 110px;
  color: var(--text-secondary);
  background: #fff;
  border: 1px dashed var(--border-color);
  border-radius: 8px;
}

.result-message.error {
  color: #f53f3f;
  border-color: #fca5a5;
}

.results-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 14px;
}

.result-card {
  padding: 16px;
  background: #fff;
  border: 1px solid var(--border-light);
  border-radius: 8px;
  box-shadow: var(--shadow-card);
}

.result-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 10px;
}

.result-title-block {
  min-width: 0;
}

.result-rank {
  display: inline-block;
  margin-right: 7px;
  padding: 2px 7px;
  color: #fff;
  background: #4e5969;
  border-radius: 3px;
  font-size: 11px;
  vertical-align: 2px;
}

h3 {
  display: inline;
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.result-meta {
  max-width: 680px;
  margin-top: 5px;
  color: var(--text-secondary);
  font-size: 12px;
}

.result-final {
  flex: 0 0 auto;
  text-align: right;
}

.result-final strong {
  display: block;
  color: #f53f3f;
  font-family: "SF Mono", "Menlo", monospace;
  font-size: 23px;
  line-height: 1;
}

.result-final span {
  color: var(--text-tertiary);
  font-size: 10px;
  letter-spacing: 0.4px;
  text-transform: uppercase;
}

.score-bars {
  display: grid;
  grid-template-columns: repeat(5, minmax(90px, 1fr));
  gap: 9px;
  padding: 11px 0;
  border-top: 1px solid var(--border-light);
  border-bottom: 1px solid var(--border-light);
  margin-bottom: 11px;
}

.result-content {
  position: relative;
  max-height: 150px;
  overflow: hidden;
  font-size: 13px;
  line-height: 1.7;
}

.result-content:not(.expanded)::after {
  position: absolute;
  right: 0;
  bottom: 0;
  left: 0;
  height: 38px;
  background: linear-gradient(transparent, #fff);
  content: "";
  pointer-events: none;
}

.result-content.expanded {
  max-height: none;
}

.result-toggle {
  margin-top: 8px;
  padding: 0;
  color: var(--primary-color);
  background: none;
  border: 0;
  cursor: pointer;
  font-size: 12px;
}

.result-toggle:hover {
  text-decoration: underline;
}

.matched-snippet {
  margin-top: 8px;
  padding: 7px 9px;
  overflow: hidden;
  color: var(--text-secondary);
  text-overflow: ellipsis;
  white-space: nowrap;
  background: #f6f8fa;
  border-left: 3px solid var(--primary-color);
  font-size: 12px;
}

@media (max-width: 1050px) {
  .debug-layout {
    flex-direction: column;
  }

  .debug-params {
    position: static;
    width: 100%;
    height: auto;
    min-height: 0;
  }

  .debug-results {
    width: 100%;
  }

  .result-stats,
  .score-bars {
    grid-template-columns: repeat(3, 1fr);
  }
}

@media (max-width: 720px) {
  .result-stats,
  .score-bars {
    grid-template-columns: repeat(2, 1fr);
  }

  .result-card-head {
    flex-direction: column;
  }

  .result-final {
    text-align: left;
  }
}
</style>
