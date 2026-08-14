(function (global) {
  'use strict';

  const markedInstance = global.marked;

  function clampNumber(value, min, max, fallback) {
    const number = Number(value);
    if (!Number.isFinite(number)) return fallback;
    return Math.min(max, Math.max(min, number));
  }

  function normalizeTextBullets(text) {
    return String(text || '')
      .split('\n')
      .map((line) =>
        /^\s*•\s/.test(line)
          ? line.replace(/^(\s*)•\s*/, '$1- ')
          : line
      )
      .join('\n');
  }

  function configureMarked() {
    if (markedInstance && typeof markedInstance.setOptions === 'function') {
      markedInstance.setOptions({ breaks: true, gfm: true });
    }
  }

  function renderMarkdown(raw) {
    if (!raw) return '<em>暂无 Markdown 内容</em>';
    configureMarked();
    return markedInstance.parse(normalizeTextBullets(raw));
  }

  function renderChunkMarkdown(text) {
    if (!text) return '';
    const lines = normalizeTextBullets(text).split('\n');
    const fixed = [];

    for (let index = 0; index < lines.length; index += 1) {
      const line = lines[index];
      const previous = index > 0 ? lines[index - 1] : '';
      const isListItem = /^\s*[-*+]\s/.test(line) || /^\s*\d+\.\s/.test(line);
      const isBlank = /^\s*$/.test(line);
      const previousIsListItem =
        /^\s*[-*+]\s/.test(previous) || /^\s*\d+\.\s/.test(previous);
      const startsWithIndent = /^\s/.test(line);

      if (!isListItem && !isBlank && previousIsListItem && !startsWithIndent) {
        fixed.push('');
      }
      fixed.push(line);
    }

    configureMarked();
    return markedInstance.parse(fixed.join('\n'));
  }

  function cleanImageAlt(alt) {
    let value = String(alt || '')
      .split('?')[0]
      .split('#')[0]
      .replace(/\\/g, '/')
      .split('/')
      .pop() || 'image';
    return value.length > 120 ? value.slice(0, 120) : value;
  }

  function sanitizeImageContext(text) {
    return String(text || '').replace(
      /\[Image:\s*([^\]]+)\]/g,
      (match, alt) => `[Image: ${cleanImageAlt(alt)}]`
    );
  }

  function estimateTokens(text) {
    const value = String(text || '');
    const chineseChars = (
      value.match(/[\u4e00-\u9fff\u3400-\u4dbf]/g) || []
    ).length;
    return Math.round(chineseChars / 1.5 + (value.length - chineseChars) / 4);
  }

  function fmtTime(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString('zh-CN', { hour12: false });
  }

  function fmtTimeShort(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    const pad = (number) => String(number).padStart(2, '0');
    return [
      date.getFullYear(),
      '-',
      pad(date.getMonth() + 1),
      '-',
      pad(date.getDate()),
      ' ',
      pad(date.getHours()),
      ':',
      pad(date.getMinutes()),
      ':',
      pad(date.getSeconds()),
    ].join('');
  }

  function formatScore(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
      return '—';
    }
    return Number(value).toFixed(4);
  }

  function fmtPct(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return `${(number * 100).toFixed(1)}%`;
  }

  function fmtLatency(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return number < 1000
      ? `${number.toFixed(0)}ms`
      : `${(number / 1000).toFixed(2)}s`;
  }

  function inferVectorStage(progress) {
    const value = Number(progress) || 0;
    if (value >= 80) return 'storing';
    if (value >= 50) return 'embedding';
    if (value >= 25) return 'chunking';
    if (value >= 10) return 'extracting_images';
    return 'preparing';
  }

  function vectorStageLabel(stage, progress) {
    const labels = {
      pending: '等待中',
      preparing: '准备中',
      extracting_images: '提取图片',
      validating: '校验中',
      chunking: '分块中',
      interleaving: '排序中',
      embedding: '解析',
      storing: '写入 Milvus',
      done: '完成',
      failed: '失败',
    };
    return labels[stage] || labels[inferVectorStage(progress)] || '处理中';
  }

  function vectorStatusLabel(status) {
    const labels = {
      pending: '待解析',
      indexing: '解析中',
      indexed: '已解析',
      failed: '解析失败',
    };
    return labels[status] || status || '—';
  }

  function highlightCodeBlocks(container) {
    if (!container || typeof global.hljs === 'undefined') return;
    try {
      global.hljs.configure({ languages: [] });
    } catch {
      // The global build may not support configure, highlighting still works.
    }
    container.querySelectorAll('pre code').forEach((element) => {
      try {
        global.hljs.highlightElement(element);
      } catch {
        // A malformed code block should not break the rest of the page.
      }
    });
  }

  function handleImageError(event) {
    const image = event.currentTarget || event.target;
    if (!image || image.dataset.fallbackApplied) return;
    image.dataset.fallbackApplied = '1';
    const fallback = document.createElement('span');
    fallback.className = 'image-fallback';
    fallback.textContent = `⚠ ${image.alt || '图片'} (${image.src})`;
    if (image.parentNode) {
      image.parentNode.replaceChild(fallback, image);
    }
  }

  function openPage(pageId, chunkIndex = null) {
    const query = chunkIndex === null ? '' : `?chunk=${encodeURIComponent(chunkIndex)}`;
    location.hash = `#/page/${encodeURIComponent(pageId)}${query}`;
  }

  function errorMessage(error, fallback = '操作失败') {
    if (!error) return fallback;
    if (typeof error === 'string') return error;
    if (error.detail) return error.detail;
    return error.message || fallback;
  }

  global.UIUtils = {
    clampNumber,
    normalizeTextBullets,
    renderMarkdown,
    renderChunkMarkdown,
    cleanImageAlt,
    sanitizeImageContext,
    estimateTokens,
    fmtTime,
    fmtTimeShort,
    formatScore,
    fmtPct,
    fmtLatency,
    inferVectorStage,
    vectorStageLabel,
    vectorStatusLabel,
    highlightCodeBlocks,
    handleImageError,
    openPage,
    errorMessage,
  };
})(window);
