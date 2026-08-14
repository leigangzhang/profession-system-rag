(function () {
  'use strict';

  const { createApp, h, reactive, nextTick } = Vue;
  const { createRouter, createWebHashHistory } = VueRouter;
  const { loadModule } = window['vue3-sfc-loader'];

  function syncPathnameToHash() {
    const path = window.location.pathname.replace(/\/+$/, '');
    if (!path || path === '/index.html') return;

    const apiLike =
      path === '/api' ||
      path.startsWith('/api/') ||
      path === '/static' ||
      path.startsWith('/static/') ||
      path === '/images' ||
      path.startsWith('/images/');
    if (apiLike || window.location.hash) return;

    const search = window.location.search;
    history.replaceState(null, '', `${path}${search}#${path}${search}`);
  }

  const loadOptions = {
    moduleCache: {
      vue: Vue,
    },
    async getFile(url) {
      const cleanUrl = url.split('?')[0];
      const response = await fetch(cleanUrl);
      if (!response.ok) {
        throw new Error(`无法加载组件 ${cleanUrl} (${response.status})`);
      }
      return response.text();
    },
    addStyle(textContent) {
      const style = document.createElement('style');
      style.setAttribute('data-sfc-style', '1');
      style.textContent = textContent;
      document.head.appendChild(style);
    },
  };

  function loadSfc(path) {
    return loadModule(path, loadOptions);
  }

  function view(path) {
    return () => loadSfc(path);
  }

  const globalState = reactive({
    collapsed: false,
    routeTitle: 'Root 管理',
    workerStatus: { running: false, max_concurrent: 0 },
    workerPolling: false,
  });

  const api = window.RagApi;
  const utils = window.UIUtils;

  function toast(message, type = 'info') {
    const Message = ArcoVue && ArcoVue.Message;
    const method = Message && Message[type] ? type : 'info';
    if (Message && typeof Message[method] === 'function') {
      Message[method](String(message || ''));
    } else {
      console[type === 'error' ? 'error' : 'log'](message);
    }
  }

  async function refreshWorkerStatus() {
    try {
      globalState.workerStatus = await api.getWorkerStatus();
    } catch {
      globalState.workerStatus = { running: false, max_concurrent: 0 };
    }
  }

  function startWorkerPolling() {
    if (globalState.workerPolling) return;
    globalState.workerPolling = true;
    refreshWorkerStatus();
    setInterval(() => {
      if (!document.hidden) refreshWorkerStatus();
    }, 15000);
  }

  const routes = [
    { path: '/', redirect: '/roots' },
    {
      path: '/roots',
      name: 'roots',
      component: view('/static/views/RootManager.vue'),
      meta: { title: 'Root 管理', menuKey: 'roots', icon: 'folder' },
    },
    {
      path: '/pages',
      name: 'pages',
      component: view('/static/views/PageList.vue'),
      meta: { title: '页面列表', menuKey: 'pages', icon: 'file' },
    },
    {
      path: '/page/:page_id',
      name: 'page',
      component: view('/static/views/ChunkInspector.vue'),
      meta: { title: 'Chunk 详情', menuKey: 'pages', icon: 'file' },
    },
    {
      path: '/inspect/:page_id',
      redirect: (to) => ({
        name: 'page',
        params: { page_id: to.params.page_id },
        query: to.query,
      }),
    },
    {
      path: '/search-debug',
      name: 'search-debug',
      component: view('/static/views/SearchDebug.vue'),
      meta: { title: '检索调试', menuKey: 'search-debug', icon: 'search' },
    },
    {
      path: '/search-history',
      name: 'search-history',
      component: view('/static/views/SearchHistory.vue'),
      meta: { title: '检索历史', menuKey: 'search-history', icon: 'history' },
    },
    { path: '/history', redirect: '/search-history' },
    { path: '/:pathMatch(.*)*', redirect: '/roots' },
  ];

  syncPathnameToHash();

  const router = createRouter({
    history: createWebHashHistory(),
    routes,
    scrollBehavior() {
      return { top: 0 };
    },
  });

  router.afterEach((to) => {
    globalState.routeTitle = to.meta && to.meta.title
      ? to.meta.title
      : 'RAG Notion KB';
    document.title = `${globalState.routeTitle} · RAG Notion KB`;
  });

  async function bootstrap() {
    const sharedPaths = [
      '/static/components/AppLayout.vue',
      '/static/components/PageHeader.vue',
      '/static/components/EmptyState.vue',
      '/static/components/StatusTag.vue',
      '/static/components/ScoreBar.vue',
      '/static/components/PageTree.vue',
      '/static/components/SyncProgress.vue',
    ];
    const sharedComponents = await Promise.all(
      sharedPaths.map((path) => loadSfc(path))
    );
    const AppLayout = sharedComponents[0];
    const app = createApp({
      render() {
        return h(AppLayout);
      },
    });

    app.use(router);
    app.use(ArcoVue);
    app.use(ArcoVueIcon);
    sharedComponents.forEach((component) => {
      if (component && component.name) {
        app.component(component.name, component);
      }
    });
    app.provide('globalState', globalState);
    app.provide('toast', toast);
    app.config.globalProperties.$toast = toast;

    await router.isReady();
    await nextTick();
    app.mount('#app');
    startWorkerPolling();
  }

  window.RagApp = {
    state: globalState,
    api,
    toast,
    openPage: utils.openPage,
    router,
  };

  bootstrap().catch((error) => {
    console.error(error);
    const root = document.getElementById('app');
    root.innerHTML = '';
    const message = document.createElement('div');
    message.className = 'app-boot';
    const inner = document.createElement('div');
    inner.className = 'app-load-error';
    inner.textContent = `前端初始化失败: ${error.message}`;
    message.appendChild(inner);
    root.appendChild(message);
  });
})();
