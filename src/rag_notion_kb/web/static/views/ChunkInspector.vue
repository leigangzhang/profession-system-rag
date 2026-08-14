<template>
  <div class="page-view inspector-view">
    <div class="page-stack inspector-stack">
      <PageHeader
        :title="detail ? detail.page_title : 'Chunk 详情'"
        :description="metaDescription"
        back
        @back="router.push('/pages')"
      >
        <template #actions>
          <a-button
            v-if="detail && detail.page_url"
            href="#"
            @click.prevent="openNotion"
          >
            <template #icon><IconLaunch /></template>
            打开 Notion
          </a-button>
          <a-button :loading="loading" @click="loadDetail">
            <template #icon><IconRefresh /></template>
            刷新
          </a-button>
        </template>
      </PageHeader>

      <div v-if="loading" class="state-panel">
        <a-spin size="28" />
        <span>正在加载页面内容…</span>
      </div>

      <div v-else-if="loadError" class="state-panel error-panel">
        <IconExclamationCircle />
        <span>{{ loadError }}</span>
        <a-button size="small" @click="loadDetail">重试</a-button>
      </div>

      <div v-else-if="detail" ref="splitContainer" class="detail-layout">
        <section ref="markdownPanel" class="detail-panel markdown-panel">
          <header class="detail-panel-header">
            <div>
              <h2>Markdown</h2>
              <span class="muted">原始页面内容</span>
            </div>
            <a-tag v-if="detail.status" size="small">
              {{ detail.status }}
            </a-tag>
          </header>
          <div
            ref="markdownContent"
            class="markdown-content detail-scroll"
            v-html="markdownHtml"
          ></div>
        </section>

        <button
          ref="splitter"
          type="button"
          class="detail-splitter"
          :aria-label="'调整面板宽度'"
          @pointerdown="startSplitDrag"
        ></button>

        <section ref="chunkPanel" class="detail-panel chunk-panel">
          <header class="detail-panel-header">
            <div>
              <h2>Chunks</h2>
              <span class="muted">{{ detail.chunks.length }} 个分块</span>
            </div>
          </header>

          <div class="embedding-stats">
            <div class="embedding-stat">
              <span>有效向量</span>
              <strong>
                {{ detail.embedding_stats.nonzero }}/{{
                  detail.embedding_stats.total
                }}
              </strong>
            </div>
            <div class="embedding-stat">
              <span>零向量</span>
              <strong
                :class="{ danger: detail.embedding_stats.zero > 0 }"
              >
                {{ detail.embedding_stats.zero }}
              </strong>
            </div>
            <div class="embedding-stat">
              <span>维度</span>
              <strong>{{ detail.embedding_stats.dim || '—' }}</strong>
            </div>
            <div
              class="embedding-stat sample-stat"
              :title="sampleTitle"
            >
              <span>样本</span>
              <strong class="mono">{{ sampleValues }}</strong>
            </div>
          </div>

          <div ref="chunkList" class="chunk-list detail-scroll">
            <div v-if="detail.chunks.length === 0" class="chunk-empty">
              <IconInbox />
              <span>该页面没有可展示的分块</span>
            </div>
            <article
              v-for="chunk in detail.chunks"
              :key="chunk.chunk_index"
              class="chunk-card"
              :class="{
                active: activeChunkIndex === chunk.chunk_index,
                expanded: expandedChunkIndex === chunk.chunk_index,
              }"
              :data-chunk-index="chunk.chunk_index"
              @click="selectChunk(chunk)"
            >
              <header class="chunk-card-header">
                <span class="chunk-index mono">#{{ chunk.chunk_index }}</span>
                <a-tag :color="chunkTypeColor(chunk.chunk_type)" size="small">
                  {{ chunk.chunk_type }}
                </a-tag>
                <span
                  class="chunk-path truncate"
                  :title="chunk.header_path"
                >
                  {{ chunk.header_path }}
                </span>
                <span class="chunk-size">{{ chunkSizeLabel(chunk) }}</span>
                <span
                  class="chunk-vector-dot"
                  :class="{ ok: chunk.vector_nonzero }"
                  :title="chunk.vector_nonzero ? '向量正常' : '零向量'"
                ></span>
              </header>

              <div
                v-if="expandedChunkIndex !== chunk.chunk_index"
                class="chunk-preview"
                v-html="chunkPreview(chunk)"
              ></div>
              <div
                v-else
                class="chunk-expanded-content"
                v-html="chunkExpanded(chunk)"
              ></div>
            </article>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>

<script>
const {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} = Vue;

export default {
  name: 'ChunkInspector',
  setup() {
    const api = window.RagApi;
    const utils = window.UIUtils;
    const toast = window.RagApp.toast;
    const router = window.RagApp.router;
    const route = router.currentRoute.value;

    const detail = ref(null);
    const loading = ref(true);
    const loadError = ref('');
    const markdownHtml = ref('');
    const activeChunkIndex = ref(null);
    const expandedChunkIndex = ref(null);

    const splitContainer = ref(null);
    const markdownPanel = ref(null);
    const chunkPanel = ref(null);
    const splitter = ref(null);
    const markdownContent = ref(null);
    const chunkList = ref(null);

    let observer = null;
    let syncingScroll = false;
    let documentMove = null;
    let documentUp = null;

    const metaDescription = computed(() => {
      if (!detail.value) return '查看页面 Markdown 与分块详情';
      const synced = utils.fmtTime(detail.value.last_synced_time);
      return `状态: ${detail.value.status} · 最近同步: ${synced}`;
    });

    const sampleValues = computed(() => {
      const samples = detail.value && detail.value.embedding_stats
        ? detail.value.embedding_stats.sample_first5 || []
        : [];
      if (!samples.length) return '—';
      return samples
        .slice(0, 3)
        .map((value) => Number(value).toFixed(4))
        .join(', ') + '…';
    });

    const sampleTitle = computed(() => {
      const samples = detail.value && detail.value.embedding_stats
        ? detail.value.embedding_stats.sample_first5 || []
        : [];
      return samples.map((value) => Number(value).toFixed(4)).join(', ');
    });

    function escapeHtml(value) {
      const element = document.createElement('div');
      element.textContent = String(value ?? '');
      return element.innerHTML;
    }

    function escapeAttr(value) {
      return escapeHtml(value).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function chunkTypeColor(type) {
      const colors = {
        text: 'blue',
        table: 'orange',
        code: 'green',
        image: 'purple',
      };
      return colors[type] || 'gray';
    }

    function chunkSizeLabel(chunk) {
      const text = utils.sanitizeImageContext(chunk.text);
      let label = `${text.length} chars`;
      if (text.length > 200) label += ` / ~${utils.estimateTokens(text)} tk`;
      return label;
    }

    function isImageOnly(text) {
      return /^\s*!\[(.*?)\]\((.*?)\)\s*$/.test(String(text || '').trim());
    }

    function chunkPreview(chunk) {
      const text = utils.sanitizeImageContext(chunk.text);
      const imageOnly = isImageOnly(text);

      if (chunk.chunk_type === 'image' && chunk.image_url) {
        const alt = utils.cleanImageAlt(
          (text.match(/\[Image:\s*([^\]]+)\]/) || [])[1]
        );
        return [
          '<div class="image-chunk-preview">',
          `<img src="${escapeAttr(chunk.image_url)}" alt="${escapeAttr(alt)}">`,
          `<span class="image-chunk-name truncate">${escapeHtml(alt)}</span>`,
          '</div>',
        ].join('');
      }

      if (imageOnly) {
        const match = text.trim().match(/^\s*!\[(.*?)\]\((.*?)\)\s*$/);
        const alt = utils.cleanImageAlt(match && match[1]);
        return `<span class="image-preview-hint">${escapeHtml(alt)}</span>`;
      }

      const preview =
        text.length > 400 ? `${text.slice(0, 400)}…` : text;
      return `<span class="plain-preview">${escapeHtml(preview)}</span>`;
    }

    function chunkExpanded(chunk) {
      const text = utils.sanitizeImageContext(chunk.text);
      if (chunk.chunk_type === 'image' && chunk.image_url) {
        const alt = utils.cleanImageAlt(
          (text.match(/\[Image:\s*([^\]]+)\]/) || [])[1]
        );
        return [
          '<div class="image-chunk-expanded">',
          `<img src="${escapeAttr(chunk.image_url)}" alt="${escapeAttr(alt)}">`,
          '</div>',
          `<div class="image-chunk-context markdown-content">${utils.renderChunkMarkdown(text)}</div>`,
        ].join('');
      }
      return `<div class="markdown-content">${utils.renderChunkMarkdown(text)}</div>`;
    }

    function bindRichContent(root) {
      if (!root) return;
      root.querySelectorAll('img').forEach((image) => {
        image.addEventListener('error', utils.handleImageError);
        if (image.complete && image.naturalWidth === 0) {
          image.dispatchEvent(new Event('error'));
        }
      });
      utils.highlightCodeBlocks(root);
    }

    function assignHeaderIds() {
      if (!markdownContent.value) return;
      let counter = 0;
      markdownContent.value
        .querySelectorAll('h1,h2,h3,h4,h5,h6')
        .forEach((heading) => {
          heading.id = `md-h-${counter}`;
          counter += 1;
        });
    }

    function findHeader(headerPath) {
      if (!headerPath || !markdownContent.value) return null;
      const segments = headerPath.split(' > ').map((part) => part.trim());
      const target = segments[segments.length - 1].replace(/^#+\s*/, '');
      const headers = markdownContent.value.querySelectorAll(
        'h1,h2,h3,h4,h5,h6'
      );
      for (const header of headers) {
        if (header.textContent.trim() === target) return header;
      }
      return null;
    }

    function scrollToHeader(header) {
      if (!header) return;
      header.scrollIntoView({ behavior: 'smooth', block: 'start' });
      header.classList.add('flash-highlight');
      setTimeout(() => header.classList.remove('flash-highlight'), 1500);
    }

    function selectChunk(chunk) {
      expandedChunkIndex.value =
        expandedChunkIndex.value === chunk.chunk_index
          ? null
          : chunk.chunk_index;
      activeChunkIndex.value = chunk.chunk_index;
      nextTick(() => {
        const card = chunkList.value && chunkList.value.querySelector(
          `.chunk-card[data-chunk-index="${chunk.chunk_index}"]`
        );
        if (card) bindRichContent(card);
        const header = findHeader(chunk.header_path);
        if (header) scrollToHeader(header);
      });
    }

    function activateChunk(index) {
      const card = chunkList.value && chunkList.value.querySelector(
        `.chunk-card[data-chunk-index="${String(index).replace(/"/g, '\\"')}"]`
      );
      if (card) card.click();
    }

    function setupScrollSync() {
      if (!markdownPanel.value || !chunkPanel.value) return;

      const left = markdownPanel.value.querySelector('.detail-scroll');
      const right = chunkPanel.value.querySelector('.detail-scroll');
      if (!left || !right) return;

      left.addEventListener('scroll', () => {
        if (syncingScroll) return;
        const leftMax = left.scrollHeight - left.clientHeight;
        const rightMax = right.scrollHeight - right.clientHeight;
        if (leftMax > 0 && rightMax > 0) {
          syncingScroll = true;
          right.scrollTop = (left.scrollTop / leftMax) * rightMax;
          requestAnimationFrame(() => {
            syncingScroll = false;
          });
        }
      });

      right.addEventListener('scroll', () => {
        if (syncingScroll) return;
        const leftMax = left.scrollHeight - left.clientHeight;
        const rightMax = right.scrollHeight - right.clientHeight;
        if (leftMax > 0 && rightMax > 0) {
          syncingScroll = true;
          left.scrollTop = (right.scrollTop / rightMax) * leftMax;
          requestAnimationFrame(() => {
            syncingScroll = false;
          });
        }
      });
    }

    function setupObserver() {
      if (observer) observer.disconnect();
      if (!markdownContent.value || !chunkList.value) return;

      const headerToChunk = {};
      detail.value.chunks.forEach((chunk) => {
        const segments = String(chunk.header_path || '')
          .split(' > ')
          .map((part) => part.trim());
        const title = segments[segments.length - 1].replace(/^#+\s*/, '');
        headerToChunk[title] = chunk.chunk_index;
      });

      observer = new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            if (!entry.isIntersecting) return;
            const title = entry.target.textContent.trim();
            const chunkIndex = headerToChunk[title];
            if (chunkIndex === undefined) return;
            activeChunkIndex.value = chunkIndex;
            const card = chunkList.value.querySelector(
              `.chunk-card[data-chunk-index="${chunkIndex}"]`
            );
            if (card) {
              card.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            }
          });
        },
        { rootMargin: '-10% 0px -70% 0px', threshold: 0 }
      );

      markdownContent.value
        .querySelectorAll('h1,h2,h3,h4,h5,h6')
        .forEach((heading) => observer.observe(heading));
    }

    function restoreSplitRatio() {
      if (!markdownPanel.value || !chunkPanel.value) return;
      const saved = Number(localStorage.getItem('kb-split-ratio'));
      const ratio =
        Number.isFinite(saved) && saved >= 20 && saved <= 80 ? saved : 50;
      markdownPanel.value.style.width = `${ratio}%`;
      chunkPanel.value.style.width = `${100 - ratio}%`;
    }

    function startSplitDrag(event) {
      if (window.innerWidth <= 767) return;
      const container = splitContainer.value;
      if (!container || !markdownPanel.value || !chunkPanel.value) return;
      event.preventDefault();
      splitter.value.setPointerCapture(event.pointerId);
      document.body.classList.add('splitter-dragging');

      const startX = event.clientX;
      const startWidth = markdownPanel.value.getBoundingClientRect().width;

      documentMove = (moveEvent) => {
        const containerWidth = container.getBoundingClientRect().width;
        const nextWidth = startWidth + moveEvent.clientX - startX;
        const ratio = Math.max(20, Math.min(80, (nextWidth / containerWidth) * 100));
        markdownPanel.value.style.width = `${ratio}%`;
        chunkPanel.value.style.width = `${100 - ratio}%`;
      };

      documentUp = () => {
        document.body.classList.remove('splitter-dragging');
        document.removeEventListener('pointermove', documentMove);
        document.removeEventListener('pointerup', documentUp);
        localStorage.setItem('kb-split-ratio', String(
          parseFloat(markdownPanel.value.style.width)
        ));
        documentMove = null;
        documentUp = null;
      };

      document.addEventListener('pointermove', documentMove);
      document.addEventListener('pointerup', documentUp);
    }

    function openNotion() {
      if (detail.value && detail.value.page_url) {
        window.open(detail.value.page_url, '_blank', 'noopener');
      }
    }

    async function loadDetail() {
      const pageId = route.params.page_id;
      if (!pageId) return;
      loading.value = true;
      loadError.value = '';
      activeChunkIndex.value = null;
      expandedChunkIndex.value = null;
      try {
        const nextDetail = await api.getPageDetail(pageId);
        detail.value = nextDetail;
        markdownHtml.value = utils.renderMarkdown(nextDetail.raw_markdown);
        await nextTick();
        assignHeaderIds();
        bindRichContent(markdownContent.value);
        bindRichContent(chunkList.value);
        restoreSplitRatio();
        setupScrollSync();
        setupObserver();

        const chunkQuery = Number(route.query.chunk);
        if (Number.isFinite(chunkQuery)) {
          activateChunk(chunkQuery);
        }
      } catch (error) {
        loadError.value = utils.errorMessage(error, '加载页面详情失败');
      } finally {
        loading.value = false;
      }
    }

    watch(
      () => [route.params.page_id, route.query.chunk],
      () => loadDetail()
    );

    onMounted(() => {
      loadDetail();
      window.addEventListener('resize', restoreSplitRatio);
    });

    onBeforeUnmount(() => {
      if (observer) observer.disconnect();
      if (documentMove) {
        document.removeEventListener('pointermove', documentMove);
      }
      if (documentUp) {
        document.removeEventListener('pointerup', documentUp);
      }
      window.removeEventListener('resize', restoreSplitRatio);
      document.body.classList.remove('splitter-dragging');
    });

    return {
      detail,
      loading,
      loadError,
      markdownHtml,
      activeChunkIndex,
      expandedChunkIndex,
      splitContainer,
      markdownPanel,
      chunkPanel,
      splitter,
      markdownContent,
      chunkList,
      metaDescription,
      sampleValues,
      sampleTitle,
      chunkTypeColor,
      chunkSizeLabel,
      chunkPreview,
      chunkExpanded,
      selectChunk,
      startSplitDrag,
      openNotion,
      loadDetail,
      router,
    };
  },
};
</script>

<style scoped>
.inspector-view,
.inspector-stack {
  height: 100%;
}

.inspector-stack {
  max-width: none;
}

.state-panel {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  min-height: 200px;
  color: var(--text-secondary);
  background: #fff;
  border: 1px solid var(--border-light);
  border-radius: 8px;
}

.error-panel {
  color: #f53f3f;
}

.detail-layout {
  display: flex;
  width: 100%;
  height: calc(100vh - 160px);
  min-height: 480px;
  overflow: hidden;
  background: #fff;
  border: 1px solid var(--border-light);
  border-radius: 8px;
  box-shadow: var(--shadow-card);
}

.detail-panel {
  display: flex;
  min-width: 0;
  width: 50%;
  flex-direction: column;
  overflow: hidden;
}

.markdown-panel {
  border-right: 1px solid var(--border-light);
}

.detail-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex: 0 0 auto;
  padding: 14px 18px;
  border-bottom: 1px solid var(--border-light);
}

.detail-panel-header h2 {
  margin: 0;
  color: var(--text-primary);
  font-size: 15px;
  font-weight: 600;
}

.detail-panel-header span {
  display: block;
  margin-top: 2px;
  font-size: 11px;
}

.detail-scroll {
  min-height: 0;
  flex: 1;
  overflow: auto;
  padding: 18px;
}

.detail-splitter {
  position: relative;
  z-index: 2;
  flex: 0 0 6px;
  width: 6px;
  margin-left: -1px;
  padding: 0;
  background: var(--border-color);
  border: 0;
  cursor: col-resize;
}

.detail-splitter:hover {
  background: var(--primary-color);
}

.embedding-stats {
  display: grid;
  flex: 0 0 auto;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  border-bottom: 1px solid var(--border-light);
  background: #fbfcfd;
}

.embedding-stat {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 14px;
  border-right: 1px solid var(--border-light);
  border-bottom: 1px solid var(--border-light);
}

.embedding-stat:nth-child(even) {
  border-right: 0;
}

.embedding-stat:nth-child(n + 3) {
  border-bottom: 0;
}

.embedding-stat span {
  color: var(--text-secondary);
  font-size: 11px;
}

.embedding-stat strong {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chunk-list {
  padding: 12px;
  background: #fbfcfd;
}

.chunk-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 8px;
  min-height: 180px;
  color: var(--text-tertiary);
}

.chunk-card {
  padding: 11px 12px;
  margin-bottom: 8px;
  background: #fff;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  cursor: pointer;
  transition: border-color 140ms ease, box-shadow 140ms ease;
}

.chunk-card:hover {
  border-color: #93b3ff;
  box-shadow: 0 1px 4px rgba(51, 112, 255, 0.1);
}

.chunk-card.active {
  border-left: 3px solid var(--primary-color);
  background: #f8fbff;
}

.chunk-card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  margin-bottom: 7px;
}

.chunk-index {
  padding: 1px 6px;
  color: #fff;
  background: #4e5969;
  border-radius: 3px;
  font-size: 10px;
}

.chunk-path {
  min-width: 0;
  flex: 1;
  color: var(--text-secondary);
  font-size: 11px;
}

.chunk-size {
  flex: 0 0 auto;
  color: var(--text-tertiary);
  font-family: "SF Mono", "Menlo", monospace;
  font-size: 9px;
}

.chunk-vector-dot {
  flex: 0 0 8px;
  width: 8px;
  height: 8px;
  background: #f53f3f;
  border-radius: 50%;
}

.chunk-vector-dot.ok {
  background: #00b42a;
}

.chunk-preview {
  max-height: 86px;
  overflow: hidden;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.65;
  word-break: break-word;
}

.plain-preview {
  white-space: pre-wrap;
}

.image-preview-hint {
  display: inline-block;
  padding: 2px 8px;
  color: #722ed1;
  background: #f5e8ff;
  border-radius: 3px;
}

.image-preview-hint::before {
  content: "▣ ";
  font-size: 9px;
}

.image-chunk-preview {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.image-chunk-preview img {
  flex: 0 0 54px;
  width: 54px;
  height: 54px;
  object-fit: contain;
  background: var(--bg-hover);
  border: 1px solid var(--border-color);
  border-radius: 4px;
}

.image-chunk-name {
  min-width: 0;
  color: var(--text-secondary);
}

.chunk-expanded-content :deep(.markdown-content) {
  font-size: 13px;
  line-height: 1.7;
}

.image-chunk-expanded {
  margin-bottom: 8px;
  text-align: center;
}

.image-chunk-expanded img {
  display: block;
  max-width: 100%;
  max-height: 260px;
  margin: 0 auto;
  border: 1px solid var(--border-color);
  border-radius: 4px;
}

.image-chunk-context {
  padding-top: 8px;
  color: var(--text-secondary);
  border-top: 1px dashed var(--border-color);
  font-size: 12px;
}

:global(.flash-highlight) {
  animation: flash-highlight 1.5s ease-in-out;
}

@keyframes flash-highlight {
  0%,
  100% {
    background: transparent;
  }

  50% {
    background: #fef3c7;
  }
}

@media (max-width: 767px) {
  .detail-layout {
    height: calc(100vh - 140px);
    min-height: 620px;
    flex-direction: column;
  }

  .detail-panel {
    width: 100% !important;
    height: 50%;
  }

  .markdown-panel {
    border-right: 0;
    border-bottom: 1px solid var(--border-light);
  }

  .detail-splitter {
    display: none;
  }
}
</style>
