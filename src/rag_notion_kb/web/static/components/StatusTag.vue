<template>
  <a-tooltip v-if="status === 'indexing'" position="top">
    <span class="status-with-progress">
      <a-tag color="arcoblue" size="small">{{ label }}</a-tag>
      <span class="status-progress">{{ normalizedProgress }}%</span>
    </span>
    <template #content>
      <div class="stage-tooltip">
        <div class="stage-tooltip-title">{{ stageLabel }}</div>
        <div v-for="step in steps" :key="step.pct" class="stage-step">
          <span
            class="stage-dot"
            :class="{
              done: normalizedProgress >= step.pct,
              active: step.active,
            }"
          ></span>
          <span :class="{ done: normalizedProgress >= step.pct }">
            {{ step.label }}
          </span>
        </div>
      </div>
    </template>
  </a-tooltip>
  <a-tag v-else :color="color" size="small">{{ label }}</a-tag>
</template>

<script>
const { computed } = Vue;

export default {
  name: 'StatusTag',
  props: {
    status: {
      type: String,
      default: 'pending',
    },
    progress: {
      type: [Number, String],
      default: 0,
    },
    stage: {
      type: String,
      default: '',
    },
  },
  setup(props) {
    const utils = window.UIUtils;
    const normalizedProgress = computed(() =>
      utils.clampNumber(props.progress, 0, 100, 0)
    );
    const label = computed(() => utils.vectorStatusLabel(props.status));
    const color = computed(() => {
      const colors = {
        pending: 'gray',
        indexing: 'arcoblue',
        indexed: 'green',
        failed: 'red',
      };
      return colors[props.status] || 'gray';
    });
    const stageLabel = computed(() =>
      utils.vectorStageLabel(props.stage, normalizedProgress.value)
    );
    const steps = computed(() => {
      const points = [
        { pct: 5, label: '准备' },
        { pct: 12, label: '图片' },
        { pct: 25, label: '分块' },
        { pct: 50, label: '解析' },
        { pct: 80, label: '写入' },
        { pct: 100, label: '完成' },
      ];
      let activeIndex = 0;
      for (let index = 0; index < points.length; index += 1) {
        if (normalizedProgress.value >= points[index].pct) {
          activeIndex = index + 1;
        }
      }
      return points.map((point, index) => ({
        ...point,
        active: index === activeIndex,
      }));
    });

    return {
      normalizedProgress,
      label,
      color,
      stageLabel,
      steps,
    };
  },
};
</script>

<style scoped>
.status-with-progress {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}

.status-progress {
  color: var(--text-secondary);
  font-family: "SF Mono", "Menlo", monospace;
  font-size: 11px;
}

.stage-tooltip {
  min-width: 150px;
}

.stage-tooltip-title {
  margin-bottom: 7px;
  padding-bottom: 7px;
  color: #fff;
  border-bottom: 1px solid rgba(255, 255, 255, 0.18);
  font-size: 12px;
  font-weight: 600;
}

.stage-step {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 3px 0;
  color: #d1d5db;
  font-size: 11px;
}

.stage-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #6b7280;
}

.stage-dot.done {
  background: #34d399;
}

.stage-dot.active {
  background: #fbbf24;
  animation: stage-pulse 1s ease-in-out infinite;
}

.stage-step span:last-child.done {
  color: #9ca3af;
}

@keyframes stage-pulse {
  0%,
  100% {
    opacity: 1;
  }

  50% {
    opacity: 0.35;
  }
}
</style>
