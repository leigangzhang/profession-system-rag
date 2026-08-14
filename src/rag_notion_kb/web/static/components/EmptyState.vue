<template>
  <div class="empty-state">
    <div class="empty-icon">
      <component :is="iconComponent" />
    </div>
    <h3>{{ title }}</h3>
    <p v-if="description">{{ description }}</p>
    <div v-if="$slots.default" class="empty-actions">
      <slot></slot>
    </div>
  </div>
</template>

<script>
const { computed } = Vue;

export default {
  name: 'EmptyState',
  props: {
    title: {
      type: String,
      default: '暂无数据',
    },
    description: {
      type: String,
      default: '',
    },
    icon: {
      type: String,
      default: 'IconInbox',
    },
  },
  setup(props) {
    const iconComponent = computed(() => props.icon);
    return { iconComponent };
  },
};
</script>

<style scoped>
.empty-state {
  display: flex;
  align-items: center;
  flex-direction: column;
  padding: 48px 20px;
  color: var(--text-secondary);
  text-align: center;
}

.empty-icon {
  display: grid;
  width: 58px;
  height: 58px;
  margin-bottom: 12px;
  place-items: center;
  color: #94a3b8;
  background: var(--bg-hover);
  border-radius: 50%;
  font-size: 27px;
}

h3 {
  margin: 0;
  color: var(--text-primary);
  font-size: 16px;
  font-weight: 600;
}

p {
  max-width: 480px;
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.6;
}

.empty-actions {
  margin-top: 16px;
}
</style>
