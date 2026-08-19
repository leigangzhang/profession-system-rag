<template>
  <div class="page-view">
    <div class="page-stack history-stack">
      <PageHeader
        title="检索历史"
        description="回顾检索请求、来源分布与召回质量水位线，并回放历史结果快照。"
      >
        <template #actions>
          <a-popconfirm
            content="确定清空全部检索历史吗？此操作不可撤销。"
            position="br"
            @ok="clearHistory"
          >
            <a-button status="danger" :loading="clearing">
              <template #icon><IconDelete /></template>
              清空全部
            </a-button>
          </a-popconfirm>
        </template>
      </PageHeader>

      <div v-if="stats" class="history-stats">
        <div class="history-stat">
          <span>总查询次数</span>
          <strong>{{ stats.total_queries || 0 }}</strong>
          <small>全部来源</small>
        </div>
        <div class="history-stat source-stat">
          <span>来源分布</span>
          <div class="source-visual">
            <div class="source-pie" :style="sourcePieStyle"></div>
            <div class="source-lines">
              <span class="source-legend-item">
                <i class="source-dot debug"></i>debug {{ sourceCounts.debug }}
              </span>
              <span class="source-legend-item">
                <i class="source-dot mcp"></i>mcp {{ sourceCounts.mcp }}
              </span>
              <span class="source-legend-item">
                <i class="source-dot cli"></i>cli {{ sourceCounts.cli }}
              </span>
            </div>
          </div>
        </div>
        <div class="history-stat">
          <span>检索成功率</span>
          <strong :class="metricClass(stats.success_rate, true)">
            {{ fmtPct(stats.success_rate) }}
          </strong>
          <small>有结果请求占比</small>
        </div>
        <div class="history-stat">
          <span>Top分-P90</span>
          <strong>{{ formatScore(stats.top_score_p90) }}</strong>
          <small>结果最高分P90</small>
        </div>
        <div class="history-stat">
          <span>RAG检索平均时长</span>
          <strong>{{ fmtLatency(stats.average_latency_ms) }}</strong>
          <small>每次检索</small>
        </div>
        <div class="history-stat">
          <span>LLM摘要平均时长</span>
          <strong>{{ llmSummaryDurationLabel }}</strong>
          <small>已生成摘要记录</small>
        </div>
        <div class="history-stat">
          <span>摘要平均压缩率</span>
          <strong>{{ llmSummaryCompressionLabel }}</strong>
          <small>摘要Token / 输入Token</small>
        </div>
        <div class="history-stat">
          <span>整体评估</span>
          <strong :class="metricClass(stats.quality_score, true)">
            {{ fmtPct(stats.quality_score) }}
          </strong>
          <small>质量综合分</small>
        </div>
      </div>

      <div class="surface-panel">
        <div class="history-toolbar">
          <a-select
            v-model="sourceFilter"
            class="source-filter"
            @change="loadHistory"
          >
            <a-option value="all">全部来源</a-option>
            <a-option value="debug">debug</a-option>
            <a-option value="mcp">mcp</a-option>
            <a-option value="cli">cli</a-option>
          </a-select>
          <a-input-search
            v-model="keyword"
            class="history-search"
            placeholder="搜索查询内容"
            allow-clear
            @search="keyword = $event"
            @clear="keyword = ''"
          />
          <span class="history-count">
            {{ visibleHistory.length }} / {{ history.length }} 条记录
          </span>
        </div>

        <a-alert v-if="loadError" type="error" closable>
          {{ loadError }}
        </a-alert>

        <a-table
          :data="visibleHistory"
          :loading="loading"
          :pagination="false"
          :scroll="{ x: 1050 }"
          row-key="history_id"
          class="history-table"
        >
          <template #columns>
            <a-table-column title="查询问题" :width="360">
              <template #cell="{ record }">
                <div class="history-query truncate" :title="record.query">
                  {{ record.query }}
                </div>
              </template>
            </a-table-column>
            <a-table-column title="来源" :width="90">
              <template #cell="{ record }">
                <a-tag :color="sourceColor(record.source)" size="small">
                  {{ record.source }}
                </a-tag>
              </template>
            </a-table-column>
            <a-table-column title="TopK" :width="80">
              <template #cell="{ record }">
                <span class="mono">
                  {{ resultSummary(record).total_results || 0 }}
                </span>
              </template>
            </a-table-column>
            <a-table-column title="相似度" :width="160">
              <template #cell="{ record }">
                <span class="mono similarity">
                  [{{ similarityLabel(record) }}]
                </span>
              </template>
            </a-table-column>
            <a-table-column title="耗时" :width="90">
              <template #cell="{ record }">
                <span class="mono">{{ latencyLabel(record) }}</span>
              </template>
            </a-table-column>
            <a-table-column title="查询时间" :width="170">
              <template #cell="{ record }">
                <span class="time-cell">{{ fmtTime(record.created_at) }}</span>
              </template>
            </a-table-column>
            <a-table-column title="操作" :width="130" fixed="right">
              <template #cell="{ record }">
                <a-space :size="4">
                  <a-button
                    type="text"
                    size="small"
                    @click="previewHistory(record)"
                  >
                    预览
                  </a-button>
                  <a-popconfirm
                    content="确定删除这条检索历史吗？"
                    position="br"
                    @ok="deleteHistory(record)"
                  >
                    <a-button type="text" size="small" status="danger">
                      删除
                    </a-button>
                  </a-popconfirm>
                </a-space>
              </template>
            </a-table-column>
          </template>
          <template #empty>
            <EmptyState
              title="暂无检索历史"
              description="执行一次检索后，请求参数与结果快照会记录在这里。"
              icon="IconHistory"
            />
          </template>
        </a-table>
      </div>
    </div>
  </div>
</template>

<script>
const { computed, onMounted, ref } = Vue;

export default {
  name: 'SearchHistory',
  setup() {
    const api = window.RagApi;
    const utils = window.UIUtils;
    const toast = window.RagApp.toast;
    const router = window.RagApp.router;

    const history = ref([]);
    const stats = ref(null);
    const loading = ref(true);
    const loadError = ref('');
    const sourceFilter = ref('all');
    const keyword = ref('');
    const clearing = ref(false);

    const sourceCounts = computed(() => {
      const values = stats.value && stats.value.by_source
        ? stats.value.by_source
        : {};
      return {
        debug: Number(values.debug) || 0,
        mcp: Number(values.mcp) || 0,
        cli: Number(values.cli) || 0,
      };
    });

    const sourcePieStyle = computed(() => {
      const total = sourceCounts.value.debug + sourceCounts.value.mcp + sourceCounts.value.cli;
      if (!total) return { background: '#f2f3f5' };
      const debugEnd = (sourceCounts.value.debug / total) * 100;
      const mcpEnd = debugEnd + (sourceCounts.value.mcp / total) * 100;
      return {
        background: [
          `conic-gradient(#165dff 0% ${debugEnd}%,`,
          `#722ed1 ${debugEnd}% ${mcpEnd}%,`,
          `#86909c ${mcpEnd}% 100%)`,
        ].join(' '),
      };
    });

    const llmSummaryDurationLabel = computed(() => {
      return utils.fmtLatency(stats.value && stats.value.llm_summary_avg_duration_ms);
    });

    const llmSummaryCompressionLabel = computed(() => {
      const value = stats.value && stats.value.llm_summary_avg_compression_ratio;
      return utils.fmtPct(value);
    });

    const visibleHistory = computed(() => {
      const query = keyword.value.trim().toLowerCase();
      if (!query) return history.value;
      return history.value.filter((record) =>
        String(record.query || '')
          .toLowerCase()
          .includes(query)
      );
    });

    function sourceColor(source) {
      const colors = {
        debug: 'arcoblue',
        mcp: 'purple',
        cli: 'gray',
      };
      return colors[source] || 'gray';
    }

    function resultSummary(record) {
      return record.result_summary || {};
    }

    function similarityLabel(record) {
      const summary = resultSummary(record);
      const minimum =
        summary.min_score === undefined
          ? summary.top_score
          : summary.min_score;
      const maximum =
        summary.max_score === undefined
          ? summary.top_score
          : summary.max_score;
      return `${utils.formatScore(minimum)}, ${utils.formatScore(maximum)}`;
    }

    function latencyLabel(record) {
      const summary = resultSummary(record);
      if (summary.latency_ms === undefined) return '—';
      return utils.fmtLatency(summary.latency_ms);
    }

    function fmtTime(value) {
      return utils.fmtTime(value);
    }

    function fmtPct(value) {
      return utils.fmtPct(value);
    }

    function fmtLatency(value) {
      return utils.fmtLatency(value);
    }

    function formatScore(value) {
      return utils.formatScore(value);
    }

    function metricClass(value, goodWhenHigh) {
      const number = Number(value);
      if (!Number.isFinite(number)) return '';
      const good = goodWhenHigh ? number >= 0.8 : number <= 0.2;
      return good ? 'success' : 'danger';
    }

    async function loadHistory() {
      loading.value = true;
      loadError.value = '';
      const source = sourceFilter.value === 'all' ? null : sourceFilter.value;
      try {
        const [records, nextStats] = await Promise.all([
          api.listHistory(500, source),
          api.getHistoryStats(),
        ]);
        history.value = records || [];
        stats.value = nextStats || {};
      } catch (error) {
        loadError.value = utils.errorMessage(error, '加载检索历史失败');
      } finally {
        loading.value = false;
      }
    }

    function previewHistory(record) {
      router.push({
        path: '/search-debug',
        query: { history_id: record.history_id },
      });
    }

    async function deleteHistory(record) {
      try {
        await api.deleteHistory(record.history_id);
        toast('检索历史已删除', 'success');
        await loadHistory();
      } catch (error) {
        toast(utils.errorMessage(error, '删除检索历史失败'), 'error');
      }
    }

    async function clearHistory() {
      clearing.value = true;
      try {
        await api.clearHistory();
        toast('检索历史已清空', 'success');
        await loadHistory();
      } catch (error) {
        toast(utils.errorMessage(error, '清空检索历史失败'), 'error');
      } finally {
        clearing.value = false;
      }
    }

    onMounted(loadHistory);

    return {
      history,
      stats,
      loading,
      loadError,
      sourceFilter,
      keyword,
      clearing,
      sourceCounts,
      sourcePieStyle,
      llmSummaryDurationLabel,
      llmSummaryCompressionLabel,
      visibleHistory,
      sourceColor,
      resultSummary,
      similarityLabel,
      latencyLabel,
      fmtTime,
      fmtPct,
      fmtLatency,
      formatScore,
      metricClass,
      loadHistory,
      previewHistory,
      deleteHistory,
      clearHistory,
    };
  },
};
</script>

<style scoped>
.history-stack {
  max-width: 1680px;
}

.history-stats {
  display: grid;
  grid-template-columns: repeat(8, minmax(120px, 1fr));
  gap: 10px;
}

.history-stat {
  display: flex;
  min-height: 96px;
  flex-direction: column;
  justify-content: center;
  padding: 13px 14px;
  background: #fff;
  border: 1px solid var(--border-light);
  border-radius: 8px;
  box-shadow: var(--shadow-card);
}

.history-stat > span {
  color: var(--text-secondary);
  font-size: 11px;
  letter-spacing: 0.3px;
  text-transform: uppercase;
}

.history-stat strong {
  margin: 5px 0 2px;
  color: var(--text-primary);
  font-family: "SF Mono", "Menlo", monospace;
  font-size: 21px;
  line-height: 1.2;
}

.history-stat small {
  color: var(--text-tertiary);
  font-size: 10px;
}

.source-visual {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
}

.source-pie {
  flex: 0 0 46px;
  width: 46px;
  height: 46px;
  border: 1px solid var(--border-light);
  border-radius: 50%;
}

.source-lines {
  display: flex;
  min-width: 0;
  flex-direction: column;
  flex-wrap: wrap;
  gap: 3px;
}

.source-legend-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--text-secondary);
  font-size: 10px;
  white-space: nowrap;
}

.source-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
}

.source-dot.debug {
  background: #165dff;
}

.source-dot.mcp {
  background: #722ed1;
}

.source-dot.cli {
  background: #86909c;
}

.score-lines {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: 7px;
  font-family: "SF Mono", "Menlo", monospace;
  font-size: 11px;
}

.score-lines span {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  color: var(--text-primary);
}

.score-lines em {
  color: var(--text-tertiary);
  font-style: normal;
}

.history-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}

.source-filter {
  width: 130px;
}

.history-search {
  width: min(420px, 100%);
}

.history-count {
  margin-left: auto;
  color: var(--text-tertiary);
  font-size: 12px;
  white-space: nowrap;
}

.history-query {
  max-width: 100%;
  color: var(--text-primary);
  font-weight: 500;
}

.similarity {
  font-size: 11px;
  white-space: nowrap;
}

.time-cell {
  color: var(--text-secondary);
  font-size: 12px;
  white-space: nowrap;
}

@media (max-width: 1250px) {
  .history-stats {
    grid-template-columns: repeat(4, 1fr);
  }
}

@media (max-width: 700px) {
  .history-stats {
    grid-template-columns: repeat(2, 1fr);
  }

  .history-toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .source-filter,
  .history-search {
    width: 100%;
  }

  .history-count {
    margin-left: 0;
  }
}
</style>
