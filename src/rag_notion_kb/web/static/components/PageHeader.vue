<template>
  <header class="page-header">
    <div class="page-header-copy">
      <a-space :size="8" align="center">
        <a-button
          v-if="back"
          type="text"
          shape="circle"
          size="small"
          aria-label="返回"
          @click="$emit('back')"
        >
          <template #icon><IconArrowLeft /></template>
        </a-button>
        <div>
          <h1>{{ title }}</h1>
          <p v-if="description">{{ description }}</p>
        </div>
      </a-space>
    </div>
    <div class="page-header-actions">
      <slot name="actions"></slot>
    </div>
  </header>
</template>

<script>
export default {
  name: 'PageHeader',
  props: {
    title: {
      type: String,
      required: true,
    },
    description: {
      type: String,
      default: '',
    },
    back: {
      type: Boolean,
      default: false,
    },
  },
  emits: ['back'],
};
</script>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  min-width: 0;
}

.page-header-copy {
  min-width: 0;
}

h1 {
  margin: 0;
  color: var(--text-primary);
  font-size: 20px;
  font-weight: 600;
  line-height: 1.35;
}

p {
  margin: 4px 0 0;
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.5;
}

.page-header-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
}

@media (max-width: 640px) {
  .page-header {
    flex-direction: column;
  }

  .page-header-actions {
    width: 100%;
    flex-wrap: wrap;
  }
}
</style>
