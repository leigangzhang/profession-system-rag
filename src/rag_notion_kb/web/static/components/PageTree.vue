<template>
  <div class="page-tree">
    <div v-for="node in nodes" :key="node.page_id" class="tree-node">
      <div class="tree-row" :style="{ paddingLeft: `${depth * 18}px` }">
        <button
          v-if="node.children && node.children.length"
          type="button"
          class="tree-arrow"
          :class="{ expanded: expanded }"
          :aria-label="expanded ? '折叠' : '展开'"
          @click="expanded = !expanded"
        >
          <IconRight />
        </button>
        <span v-else class="tree-arrow-placeholder"></span>

        <span class="tree-title truncate" :title="node.title">
          {{ node.title }}
        </span>

        <span class="tree-status">
          <a-tag
            v-if="statusFor(node).label"
            :color="statusFor(node).color"
            size="small"
          >
            {{ statusFor(node).label }}
          </a-tag>
        </span>

        <span class="tree-freshness">
          <a-tag
            v-if="freshnessFor(node).label"
            :color="freshnessFor(node).color"
            size="small"
          >
            {{ freshnessFor(node).label }}
          </a-tag>
        </span>

        <a-checkbox
          :model-value="Boolean(selected[node.page_id])"
          :disabled="syncing"
          @change="onToggle(node, $event)"
        />
      </div>

      <div v-if="node.children && node.children.length" v-show="expanded">
        <PageTree
          :nodes="node.children"
          :depth="depth + 1"
          :selected="selected"
          :sync-info="syncInfo"
          :syncing="syncing"
          @toggle="$emit('toggle', $event)"
        />
      </div>
    </div>
  </div>
</template>

<script>
const { ref, watch } = Vue;

export default {
  name: 'PageTree',
  props: {
    nodes: {
      type: Array,
      default: () => [],
    },
    depth: {
      type: Number,
      default: 0,
    },
    selected: {
      type: Object,
      required: true,
    },
    syncInfo: {
      type: Object,
      default: () => ({}),
    },
    syncing: {
      type: Boolean,
      default: false,
    },
  },
  emits: ['toggle'],
  setup(props, { emit }) {
    const expanded = ref(props.depth < 2);

    watch(
      () => props.nodes,
      () => {
        if (props.depth < 2) expanded.value = true;
      }
    );

    function statusFor(node) {
      const info = props.syncInfo[node.page_id];
      const status = info && info.status;
      const labels = {
        syncing: '同步中',
        synced: '已同步',
        fetched: '已同步',
        indexed: '已同步',
        indexing: '索引中',
        skipped: '已跳过',
        failed: '失败',
      };
      const colors = {
        syncing: 'arcoblue',
        synced: 'green',
        fetched: 'green',
        indexed: 'green',
        indexing: 'arcoblue',
        skipped: 'gray',
        failed: 'red',
      };
      return {
        label: labels[status] || '',
        color: colors[status] || 'gray',
      };
    }

    function freshnessFor(node) {
      const info = props.syncInfo[node.page_id];
      if (
        !info ||
        !info.syncedAt ||
        !node.last_edited_time ||
        ['skipped', 'failed', 'syncing'].includes(info.status)
      ) {
        return { label: '', color: 'gray' };
      }
      const fresh = new Date(info.syncedAt) >= new Date(node.last_edited_time);
      return {
        label: fresh ? '已更新' : '待更新',
        color: fresh ? 'green' : 'orange',
      };
    }

    return {
      expanded,
      statusFor,
      freshnessFor,
      onToggle(node, value) {
        emit('toggle', {
          pageId: node.page_id,
          checked: value,
        });
      },
    };
  },
};
</script>

<style scoped>
.page-tree {
  font-size: 13px;
}

.tree-node {
  min-width: 680px;
}

.tree-row {
  display: grid;
  grid-template-columns: 22px minmax(220px, 1fr) 110px 88px 40px;
  align-items: center;
  gap: 8px;
  min-height: 38px;
  padding: 4px 8px 4px 4px;
  border-radius: 5px;
}

.tree-row:hover {
  background: var(--bg-hover);
}

.tree-arrow {
  display: grid;
  width: 20px;
  height: 20px;
  padding: 0;
  place-items: center;
  color: var(--text-secondary);
  background: none;
  border: 0;
  border-radius: 4px;
  cursor: pointer;
}

.tree-arrow:hover {
  background: var(--border-light);
}

.tree-arrow :deep(svg) {
  width: 13px;
  height: 13px;
  transition: transform 120ms ease;
}

.tree-arrow.expanded :deep(svg) {
  transform: rotate(90deg);
}

.tree-arrow-placeholder {
  width: 20px;
}

.tree-title {
  color: var(--text-primary);
}

.tree-status,
.tree-freshness {
  display: flex;
  justify-content: flex-start;
}

@media (max-width: 760px) {
  .tree-node {
    min-width: 580px;
  }

  .tree-row {
    grid-template-columns: 22px minmax(170px, 1fr) 90px 70px 36px;
  }
}
</style>
