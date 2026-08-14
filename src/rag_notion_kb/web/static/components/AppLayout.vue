<template>
  <a-layout class="app-layout">
    <a-layout-sider
      v-model:collapsed="collapsed"
      class="app-sider"
      :width="220"
      :collapsed-width="64"
      :breakpoint="'md'"
      :hide-trigger="true"
      @collapse="state.collapsed = $event"
    >
      <div class="brand" :class="{ collapsed }" @click="goHome">
        <div class="brand-mark">KB</div>
        <div v-if="!collapsed" class="brand-copy">
          <strong>RAG Notion KB</strong>
          <span>知识库工作台</span>
        </div>
      </div>

      <a-menu
        class="app-menu"
        :selected-keys="[activeMenu]"
        :collapsed="collapsed"
        @menu-item-click="navigate"
      >
        <a-menu-item key="roots">
          <template #icon><IconFolder /></template>
          Root 管理
        </a-menu-item>
        <a-menu-item key="pages">
          <template #icon><IconFile /></template>
          页面列表
        </a-menu-item>
        <a-menu-item key="search-debug">
          <template #icon><IconSearch /></template>
          检索调试
        </a-menu-item>
        <a-menu-item key="search-history">
          <template #icon><IconHistory /></template>
          检索历史
        </a-menu-item>
      </a-menu>

      <div class="sider-footer">
        <div
          class="worker-state"
          :class="state.workerStatus.running ? 'is-running' : 'is-idle'"
          :title="workerTitle"
        >
          <span class="worker-dot"></span>
          <span v-if="!collapsed" class="worker-copy">
            {{ state.workerStatus.running ? '向量 Worker 运行中' : '向量 Worker 未启动' }}
          </span>
        </div>
      </div>
    </a-layout-sider>

    <a-layout class="app-main">
      <a-layout-header class="app-header">
        <div class="header-left">
          <a-button
            class="collapse-button"
            type="text"
            shape="circle"
            :aria-label="collapsed ? '展开侧边栏' : '收起侧边栏'"
            @click="collapsed = !collapsed"
          >
            <template #icon>
              <IconMenuUnfold v-if="collapsed" />
              <IconMenuFold v-else />
            </template>
          </a-button>
          <div class="header-title">
            <span class="header-kicker">RAG Notion KB</span>
            <strong>{{ state.routeTitle }}</strong>
          </div>
        </div>
        <div class="header-right">
          <a-tag
            v-if="state.workerStatus.max_concurrent"
            color="arcoblue"
            size="small"
          >
            并发 {{ state.workerStatus.max_concurrent }}
          </a-tag>
        </div>
      </a-layout-header>

      <a-layout-content class="app-content">
        <router-view v-slot="{ Component }">
          <transition name="page-fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </a-layout-content>
    </a-layout>
  </a-layout>
</template>

<script>
const { computed, inject, ref } = Vue;

export default {
  name: 'AppLayout',
  setup() {
    const state = inject('globalState');
    const router = window.RagApp.router;
    const collapsed = ref(
      typeof window !== 'undefined' && window.innerWidth < 768
    );

    const activeMenu = computed(() => {
      const route = router.currentRoute.value;
      if (route.name === 'page') return 'pages';
      return (route.meta && route.meta.menuKey) || 'roots';
    });

    const workerTitle = computed(() =>
      state.workerStatus.running
        ? `Worker 运行中，最大并发 ${state.workerStatus.max_concurrent || 0}`
        : 'Worker 未启动'
    );

    function navigate(key) {
      const targets = {
        roots: '/roots',
        pages: '/pages',
        'search-debug': '/search-debug',
        'search-history': '/search-history',
      };
      if (targets[key]) router.push(targets[key]);
    }

    function goHome() {
      router.push('/roots');
    }

    return {
      state,
      collapsed,
      activeMenu,
      workerTitle,
      navigate,
      goHome,
    };
  },
};
</script>

<style scoped>
.app-layout {
  width: 100%;
  height: 100%;
  overflow: hidden;
}

.app-sider {
  position: relative;
  z-index: 2;
  height: 100%;
  overflow: hidden;
  background: #fff;
  box-shadow: inset -1px 0 0 var(--border-light);
}

.brand {
  display: flex;
  align-items: center;
  gap: 11px;
  height: 68px;
  padding: 0 18px;
  cursor: pointer;
  border-bottom: 1px solid var(--border-light);
}

.brand.collapsed {
  justify-content: center;
  padding: 0;
}

.brand-mark {
  display: grid;
  flex: 0 0 36px;
  width: 36px;
  height: 36px;
  place-items: center;
  color: #fff;
  background: var(--primary-color);
  border-radius: 9px;
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.5px;
}

.brand-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
  line-height: 1.2;
}

.brand-copy strong {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.brand-copy span {
  margin-top: 3px;
  color: var(--text-tertiary);
  font-size: 11px;
}

.app-menu {
  width: calc(100% - 16px);
  margin: 10px 8px 0;
  border-radius: 6px;
}

.sider-footer {
  position: absolute;
  right: 0;
  bottom: 0;
  left: 0;
  padding: 12px 14px;
  background: #fff;
  border-top: 1px solid var(--border-light);
}

.worker-state {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--bg-hover);
  font-size: 12px;
}

.worker-dot {
  flex: 0 0 8px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #c9cdd4;
}

.worker-state.is-running .worker-dot {
  background: #00b42a;
  box-shadow: 0 0 0 4px rgba(0, 180, 42, 0.1);
}

.worker-copy {
  overflow: hidden;
  color: var(--text-secondary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.app-main {
  min-width: 0;
  height: 100%;
  overflow: hidden;
}

.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 56px;
  padding: 0 20px;
  background: #fff;
  border-bottom: 1px solid var(--border-light);
}

.header-left,
.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.collapse-button {
  color: var(--text-secondary);
}

.header-title {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}

.header-title strong {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 16px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.header-kicker {
  display: none;
  color: var(--text-tertiary);
  font-size: 12px;
}

.app-content {
  height: calc(100% - 56px);
  overflow: auto;
  background: var(--bg-body);
}

.page-fade-enter-active,
.page-fade-leave-active {
  transition: opacity 120ms ease;
}

.page-fade-enter-from,
.page-fade-leave-to {
  opacity: 0;
}

@media (max-width: 767px) {
  .app-header {
    padding: 0 12px;
  }

  .header-kicker {
    display: none;
  }

  .worker-state {
    justify-content: center;
  }
}
</style>
