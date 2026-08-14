<template>
  <div class="score-bar">
    <div class="score-bar-label">
      <span>{{ label }}</span>
      <span class="mono">{{ display }}</span>
    </div>
    <a-tooltip :content="tooltip" position="top">
      <div class="score-track">
        <span
          class="score-fill"
          :style="{ width: `${width}%`, background: color }"
        ></span>
      </div>
    </a-tooltip>
  </div>
</template>

<script>
const { computed } = Vue;

export default {
  name: 'ScoreBar',
  props: {
    label: {
      type: String,
      required: true,
    },
    value: {
      type: [Number, String],
      default: null,
    },
    color: {
      type: String,
      default: '#3370ff',
    },
  },
  setup(props) {
    const utils = window.UIUtils;
    const numeric = computed(() => {
      const value = Number(props.value);
      return Number.isFinite(value) ? value : 0;
    });
    const display = computed(() => utils.formatScore(props.value));
    const width = computed(() => Math.max(0, Math.min(100, numeric.value * 100)));
    const tooltip = computed(() => `${props.label}: ${utils.formatScore(props.value)}`);

    return { display, width, tooltip };
  },
};
</script>

<style scoped>
.score-bar {
  min-width: 90px;
}

.score-bar-label {
  display: flex;
  justify-content: space-between;
  gap: 6px;
  margin-bottom: 5px;
  color: var(--text-secondary);
  font-size: 11px;
}

.score-track {
  height: 6px;
  overflow: hidden;
  background: #eef0f3;
  border-radius: 3px;
}

.score-fill {
  display: block;
  height: 100%;
  border-radius: 3px;
  transition: width 240ms ease;
}
</style>
