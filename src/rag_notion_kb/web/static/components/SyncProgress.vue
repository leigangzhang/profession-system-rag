<template>
  <a-modal
    :visible="visible"
    :footer="false"
    :mask-closable="false"
    :closable="task && task.status !== 'running'"
    title="批量同步进度"
    @cancel="$emit('update:visible', false)"
  >
    <div v-if="!task" class="sync-progress-empty">
      <a-spin :size="20" />
      <span>正在启动同步任务…</span>
    </div>
    <div v-else class="sync-progress">
      <div class="sync-progress-main">
        <div>
          <div class="sync-status-line">
            <a-tag
              :color="
                task.status === 'completed'
                  ? 'green'
                  : task.status === 'failed'
                    ? 'red'
                    : 'arcoblue'
              "
              size="small"
            >
              {{ statusLabel }}
            </a-tag>
            <span class="mono">
              {{ task.completed_pages }} / {{ task.total_pages }}
            </span>
          </div>
          <div v-if="task.current_page_title" class="sync-current-page truncate">
            当前: {{ task.current_page_title }}
          </div>
          <div v-if="task.error_message" class="sync-error">
            {{ task.error_message }}
          </div>
        </div>
        <a-progress
          :percent="percent"
          :status="task.status === 'failed' ? 'danger' : task.status === 'completed' ? 'success' : 'normal'"
          :show-text="true"
          :stroke-width="10"
        />
      </div>
      <div v-if="task.status !== 'running'" class="sync-progress-actions">
        <a-button type="primary" @click="$emit('update:visible', false)">
          关闭
        </a-button>
      </div>
    </div>
  </a-modal>
</template>

<script>
const { computed, onBeforeUnmount, watch } = Vue;

export default {
  name: 'SyncProgress',
  props: {
    visible: {
      type: Boolean,
      default: false,
    },
    syncId: {
      type: String,
      default: '',
    },
  },
  emits: ['update:visible', 'finish'],
  setup(props, { emit }) {
    const api = window.RagApi;
    const task = Vue.ref(null);
    let timer = null;

    const percent = computed(() => {
      if (!task.value || !task.value.total_pages) return 0;
      return Math.round(
        (task.value.completed_pages / task.value.total_pages) * 100
      );
    });

    const statusLabel = computed(() => {
      const labels = {
        running: '同步中',
        completed: '同步完成',
        failed: '同步失败',
      };
      return labels[task.value && task.value.status] || '同步中';
    });

    function clearTimer() {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    }

    async function poll() {
      if (!props.syncId) return;
      try {
        const nextTask = await api.getSyncProgress(props.syncId);
        task.value = nextTask;
        if (nextTask.status === 'running') {
          timer = setTimeout(poll, 1000);
        } else {
          emit('finish', nextTask);
        }
      } catch (error) {
        task.value = {
          status: 'failed',
          error_message: UIUtils.errorMessage(error),
        };
        emit('finish', task.value);
      }
    }

    watch(
      () => [props.visible, props.syncId],
      ([visible, syncId]) => {
        clearTimer();
        task.value = null;
        if (visible && syncId) poll();
      }
    );

    onBeforeUnmount(clearTimer);

    return {
      task,
      percent,
      statusLabel,
    };
  },
};
</script>

<style scoped>
.sync-progress-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  min-height: 120px;
  color: var(--text-secondary);
}

.sync-progress-main {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  min-height: 92px;
}

.sync-status-line {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.sync-current-page {
  max-width: 330px;
  color: var(--text-secondary);
  font-size: 13px;
}

.sync-error {
  max-width: 330px;
  margin-top: 6px;
  color: #f53f3f;
  font-size: 12px;
  word-break: break-word;
}

.sync-progress-main :deep(.arco-progress) {
  width: 160px;
}

.sync-progress-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

@media (max-width: 640px) {
  .sync-progress-main {
    flex-direction: column;
    gap: 14px;
  }

  .sync-progress-main :deep(.arco-progress) {
    width: 100%;
  }
}
</style>
