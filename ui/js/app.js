/* 简历速筛 · 前端主逻辑 */
(function () {
  'use strict';

  const DEGREE_RANK = { '': 0, '中专/高中': 1, '大专': 2, '本科': 3, '硕士': 4, '博士': 5 };

  const state = {
    api: null,
    ready: false,
    config: null,
    meta: {},
    roles: [],
    roleGroups: [],
    candidates: [],
    stats: null,
    review: null,
    view: 'screen',
    layout: 'list',
    rarityFilter: '',
    search: '',
    sortBy: 'score',
    sortDesc: true,
    filterYears: '',
    filterDegree: '',
    selected: new Set(),
    filteredIds: [],
    reviewQueue: [],
    queueIndex: 0,
    current: null,
    drawerId: null,
    busy: false,
    apiInfo: null,
    aiStatus: null,
    queue: null,
    eval: null,
  };

  // ============ 基础工具 ============
  const $ = (id) => document.getElementById(id);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function fmtScore(value) {
    return (Number(value) || 0).toFixed(1);
  }

  function initial(name) {
    const text = String(name || '?').trim();
    if (!text) return '?';
    // 中文取姓，英文取首字母
    if (/^[\u4e00-\u9fa5]/.test(text)) return text.slice(0, 1);
    return text.slice(0, 1).toUpperCase();
  }

  function toast(message, type) {
    const icons = { success: '✓', error: '✕', warn: '!', info: 'ℹ' };
    const node = document.createElement('div');
    node.className = 'toast ' + (type || 'info');
    node.innerHTML = `<span>${icons[type] || icons.info}</span><span>${escapeHtml(message)}</span>`;
    $('toasts').appendChild(node);
    setTimeout(() => {
      node.classList.add('out');
      setTimeout(() => node.remove(), 220);
    }, type === 'error' ? 6200 : 3600);
  }

  // ============ 音效 ============
  let audioCtx = null;
  function soundOn() {
    return state.config ? state.config.ui.sound : true;
  }
  function beep(freqs, duration, gainValue) {
    if (!soundOn()) return;
    try {
      if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const now = audioCtx.currentTime;
      freqs.forEach((freq, index) => {
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.type = 'sine';
        osc.frequency.value = freq;
        const start = now + index * 0.06;
        gain.gain.setValueAtTime(0.0001, start);
        gain.gain.exponentialRampToValueAtTime(gainValue || 0.05, start + 0.015);
        gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
        osc.connect(gain).connect(audioCtx.destination);
        osc.start(start);
        osc.stop(start + duration + 0.02);
      });
    } catch (err) { /* 静默 */ }
  }
  const sfx = {
    like: () => beep([660, 880], 0.15, 0.045),
    pass: () => beep([300, 220], 0.14, 0.04),
    skip: () => beep([440], 0.09, 0.03),
    click: () => beep([700], 0.05, 0.02),
    ssr: () => beep([784, 1047, 1319], 0.26, 0.055),
  };

  // ============ 桥接 ============
  async function call(method, ...args) {
    if (!state.api) throw new Error('本地服务未就绪');
    const result = await state.api[method](...args);
    if (!result) throw new Error('本地服务无响应');
    if (result.ok === false) throw new Error(result.error || '操作失败');
    return result;
  }

  // ============ 启动 ============
  async function boot() {
    bindEvents();
    if (!window.pywebview) {
      await new Promise((resolve) => {
        window.addEventListener('pywebviewready', resolve, { once: true });
        setTimeout(resolve, 4000);
      });
    }
    if (!window.pywebview || !window.pywebview.api) {
      toast('未能连接本地内核，请重启应用', 'error');
      return;
    }
    state.api = window.pywebview.api;

    try {
      const data = await call('bootstrap');
      applyBootstrap(data);
      syncSettingsForm();
      syncJobPicker();
      await refreshAiStatus();
      buildQueue();
      renderAll();
    } catch (err) {
      toast('初始化失败：' + err.message, 'error');
    }
  }

  function applyBootstrap(data) {
    state.config = data.config;
    state.meta = data.meta || {};
    state.roles = data.roles || [];
    state.roleGroups = data.role_groups || [];
    state.candidates = data.candidates || [];
    state.stats = data.stats;
    state.queue = data.queue || null;
    state.eval = data.eval || null;
    state.review = data.review || null;
    state.apiInfo = (data.meta && data.meta.api) || null;
    state.ready = true;
    if (state.config.ui.view === 'grid') setLayout('grid', false);
    $('sidebarFooter').textContent = `v${state.meta.version || '2.1'} · 数据仅保存在本机`;
  }

  function applyPayload(result) {
    if (result.candidates) state.candidates = result.candidates;
    if (result.stats) state.stats = result.stats;
    if (result.review) state.review = result.review;
    if (result.eval) state.eval = result.eval;
    if (result.queue) state.queue = result.queue;
  }

  // ============ 事件 ============
  function bindEvents() {
    $$('.nav-btn').forEach((btn) => {
      btn.addEventListener('click', () => switchView(btn.dataset.view));
    });

    $('btnImport').addEventListener('click', importViaDialog);
    $('btnImportSide').addEventListener('click', importViaDialog);
    $('btnImportFolder').addEventListener('click', importFolder);
    $('btnLoadSamples').addEventListener('click', loadSamples);
    $('btnExport').addEventListener('click', exportAll);

    $('searchInput').addEventListener('input', (event) => {
      state.search = event.target.value.trim().toLowerCase();
      if (state.view === 'library') renderLibrary();
    });

    // 侧栏稀有度
    $$('.sidebar .side-item[data-rarity]').forEach((btn) => {
      btn.addEventListener('click', () => {
        state.rarityFilter = btn.dataset.rarity;
        switchView('library');
        syncRarityTabs();
        renderLibrary();
      });
    });

    // 简历库筛选
    $$('#rarityTabs button').forEach((btn) => {
      btn.addEventListener('click', () => {
        state.rarityFilter = btn.dataset.rarity;
        syncRarityTabs();
        renderLibrary();
      });
    });
    $('filterYears').addEventListener('change', (e) => { state.filterYears = e.target.value; renderLibrary(); });
    $('filterDegree').addEventListener('change', (e) => { state.filterDegree = e.target.value; renderLibrary(); });
    $('sortBy').addEventListener('change', (e) => { state.sortBy = e.target.value; renderLibrary(); });
    $('btnSortOrder').addEventListener('click', () => {
      state.sortDesc = !state.sortDesc;
      $('btnSortOrder').textContent = state.sortDesc ? '↓' : '↑';
      renderLibrary();
    });
    $$('#layoutToggle button').forEach((btn) => {
      btn.addEventListener('click', () => {
        setLayout(btn.dataset.layout);
        renderLibrary();
      });
    });

    $('btnExportSelected').addEventListener('click', exportSelected);
    $('btnDeleteSelected').addEventListener('click', deleteSelected);
    $('btnClearSelect').addEventListener('click', () => { state.selected.clear(); renderLibrary(); });
    $('btnSelectAll').addEventListener('click', selectAllVisible);
    $('btnInvertSelect').addEventListener('click', invertSelection);
    $('chkSelectAll').addEventListener('change', (e) => {
      if (e.target.checked) selectAllVisible();
      else { state.selected.clear(); renderLibrary(); }
    });

    $('btnCloseDrawer').addEventListener('click', closeDrawer);
    $('drawerMask').addEventListener('click', closeDrawer);

    $('btnSaveConfig').addEventListener('click', saveSettings);
    $('btnRescore').addEventListener('click', rescoreAll);
    $('btnResetConfig').addEventListener('click', resetSettings);
    $('btnClearAll').addEventListener('click', clearAll);
    $('btnOpenDataDir').addEventListener('click', () => {
      call('open_data_dir').catch((e) => toast(e.message, 'error'));
    });

    $('btnApiToggle').addEventListener('click', toggleApi);
    $('btnApiToken').addEventListener('click', regenerateToken);
    $('btnApiDocs').addEventListener('click', showApiDocs);

    ['ssrRange', 'srRange'].forEach((id) => {
      $(id).addEventListener('input', () => {
        updateThresholdPreview();
        paintAllRanges();
      });
    });

    // 岗位选择器（顶部）
    $('jobSelect').addEventListener('change', (e) => {
      if (!e.target.value) return;
      chooseJob(e.target.value);
    });

    // AI 开关（速筛页）
    $('btnQueueStart').addEventListener('click', startQueue);
    $('btnQueuePause').addEventListener('click', pauseQueue);
    $('btnQueueRetry').addEventListener('click', retryFailed);
    $('btnAiTest').addEventListener('click', testAiConnection);
    $('btnRequeueAll').addEventListener('click', requeueAll);
    $('btnAiSidebar').addEventListener('click', () => switchView('settings'));

    // 音效
    $('btnSound').addEventListener('click', async () => {
      state.config.ui.sound = !state.config.ui.sound;
      updateSoundButton();
      if (state.config.ui.sound) sfx.click();
      try {
        const result = await call('save_config', state.config);
        state.config = result.config;
      } catch (err) { /* 静默 */ }
    });

    // 拖拽
    let dragDepth = 0;
    window.addEventListener('dragenter', (e) => {
      e.preventDefault();
      dragDepth += 1;
      if (dragDepth === 1 && hasFiles(e)) $('dropMask').classList.remove('hidden');
    });
    window.addEventListener('dragover', (e) => e.preventDefault());
    window.addEventListener('dragleave', (e) => {
      e.preventDefault();
      dragDepth = Math.max(0, dragDepth - 1);
      if (dragDepth === 0) $('dropMask').classList.add('hidden');
    });
    window.addEventListener('drop', (e) => {
      e.preventDefault();
      dragDepth = 0;
      $('dropMask').classList.add('hidden');
      handleDrop(e);
    });

    // 快捷键
    document.addEventListener('keydown', (e) => {
      const typing = /input|textarea|select/i.test(e.target.tagName || '');
      if (e.key === 'Escape') {
        if (!$('drawer').classList.contains('hidden')) closeDrawer();
        else if (!$('dropMask').classList.contains('hidden')) $('dropMask').classList.add('hidden');
        return;
      }
      if (typing) return;
      // 弹窗打开时不要响应速筛快捷键，否则确认框背后的简历会被误评
      if (document.querySelector('.modal-layer')) return;

      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'o') {
        e.preventDefault();
        importViaDialog();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
        e.preventDefault();
        $('searchInput').focus();
        return;
      }
      // Ctrl+A：简历库全选（输入框内保留浏览器默认行为）
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a' && state.view === 'library') {
        if (typing) return;
        e.preventDefault();
        selectAllVisible();
        return;
      }

      if (state.view === 'screen' && state.current) {
        if (e.key === 'ArrowLeft') { e.preventDefault(); decide('passed'); return; }
        if (e.key === 'ArrowRight') { e.preventDefault(); decide('liked'); return; }
        if (e.key === 'ArrowDown') { e.preventDefault(); decide('skipped'); return; }
        if (e.key === 'Enter') {
          e.preventDefault();
          openDrawer(state.current.id);
          return;
        }
      }

      if (e.key === '/') {
        e.preventDefault();
        $('searchInput').focus();
      }
    });

    window.addEventListener('resize', debounce(() => {
      if (state.view === 'stats') renderCharts();
    }, 200));

    // 进度回调
    window.__importProgress = (payload) => {
      const done = Math.max(0, (payload.index || 1) - 1);
      updateProgress(done, payload.total || 0, payload.filename || '');
    };
  }

  function hasFiles(event) {
    const types = event.dataTransfer && event.dataTransfer.types;
    return types && Array.from(types).indexOf('Files') !== -1;
  }

  function paintRange(input) {
    const min = Number(input.min) || 0;
    const max = Number(input.max) || 100;
    const value = Number(input.value) || 0;
    const percent = max > min ? ((value - min) / (max - min)) * 100 : 0;
    // WebView2 对 range 伪元素支持有限，直接把渐变画在元素背景上最稳
    input.style.background =
      `linear-gradient(90deg, #1877f2 0%, #1877f2 ${percent}%, #dfe3e8 ${percent}%, #dfe3e8 100%)`;
    input.style.backgroundSize = '100% 8px';
    input.style.backgroundPosition = 'center';
    input.style.backgroundRepeat = 'no-repeat';
    input.style.borderRadius = '4px';
    input.style.setProperty('--fill', percent.toFixed(1) + '%');
  }

  function paintAllRanges() {
    $$('input[type=range]').forEach(paintRange);
  }

  function debounce(fn, wait) {
    let timer = null;
    return function () {
      clearTimeout(timer);
      timer = setTimeout(fn, wait);
    };
  }

  // ============ 视图 ============
  function switchView(view) {
    state.view = view;
    $$('.nav-btn').forEach((btn) => btn.classList.toggle('active', btn.dataset.view === view));
    $$('.view').forEach((node) => node.classList.toggle('active', node.id === 'view-' + view));
    if (view === 'library') renderLibrary();
    if (view === 'stats') renderStats();
    if (view === 'settings') syncSettingsForm();
    if (view === 'screen') renderScreen();
  }

  function setLayout(layout, persist) {
    state.layout = layout === 'grid' ? 'grid' : 'list';
    $$('#layoutToggle button').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.layout === state.layout);
    });
    if (persist !== false && state.config) state.config.ui.view = state.layout;
  }

  // ============ 导入 ============
  async function importViaDialog() {
    if (state.busy) return;
    try {
      const picked = await call('pick_files');
      if (!picked.paths || !picked.paths.length) return;
      await importPaths(picked.paths);
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  async function importFolder() {
    if (state.busy) return;
    try {
      const folder = await call('pick_folder');
      if (!folder.path) return;
      const scan = await call('scan_folder', folder.path);
      if (!scan.paths.length) {
        toast('该文件夹内没有找到 PDF / DOCX / TXT 文件', 'warn');
        return;
      }
      await importPaths(scan.paths);
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  async function loadSamples() {
    try {
      const result = await call('load_samples');
      if (!result.paths || !result.paths.length) {
        toast('未找到示例简历', 'warn');
        return;
      }
      await importPaths(result.paths);
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  function handleDrop(event) {
    const files = Array.from(event.dataTransfer.files || []);
    if (!files.length) return;
    const valid = files.filter((f) => /\.(pdf|docx|txt|md)$/i.test(f.name));
    if (!valid.length) {
      toast('请拖入 PDF / DOCX / TXT 格式的简历', 'error');
      return;
    }
    if (valid.length < files.length) {
      toast(`已忽略 ${files.length - valid.length} 个不支持的文件`, 'warn');
    }
    // WebView2 下拿不到 f.path，统一走字节通道（后端落盘后按原始文件名归档）
    importDroppedFiles(valid);
  }

  async function importDroppedFiles(files) {
    if (state.busy) return;
    state.busy = true;
    showProgress(0, files.length, '读取中…');

    let okCount = 0;
    const failures = [];
    try {
      for (let index = 0; index < files.length; index += 1) {
        const file = files[index];
        updateProgress(index, files.length, file.name);
        try {
          if (file.size > 25 * 1024 * 1024) {
            failures.push({ filename: file.name, error: '文件超过 25MB 限制' });
            continue;
          }
          const buffer = await file.arrayBuffer();
          const b64 = arrayBufferToBase64(buffer);
          const result = await call('import_bytes', file.name, b64);
          applyPayload(result);
          const imported = result.imported || [];
          if (imported.length) okCount += imported.length;
          (result.duplicates || []).forEach((d) => {
            failures.push({ filename: d.filename, error: '内容与已有简历重复，已跳过' });
          });
          (result.failures || []).forEach((f) => failures.push(f));
        } catch (err) {
          failures.push({ filename: file.name, error: err.message });
        }
      }
    } finally {
      hideProgress();
      state.busy = false;
    }

    if (okCount) toast(`成功导入 ${okCount} 份简历`, 'success');
    failures.forEach((f) => toast(`${f.filename}：${f.error}`, 'error'));
    if (!okCount && !failures.length) toast('没有可导入的文件', 'warn');

    try {
      const latest = await call('list_candidates');
      applyPayload(latest);
    } catch (err) { /* 刷新失败忽略 */ }
    buildQueue();
    renderAll();

    if (okCount) {
      const hasSSR = state.candidates.some((c) => c.rarity === 'SSR');
      if (hasSSR) sfx.ssr(); else sfx.like();
    }
  }

  /** ArrayBuffer -> base64（分块处理，避免大文件爆栈） */
  function arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    const chunk = 0x8000;
    let binary = '';
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
  }

  async function importPaths(paths) {
    if (!paths.length || state.busy) return;
    state.busy = true;
    showProgress(0, paths.length, '准备中…');
    try {
      const result = await call('import_files', paths);
      applyPayload(result);
      hideProgress();
      reportImport(result);
      buildQueue();
      renderAll();
      if (result.imported && result.imported.length) {
        const hasSSR = result.imported.some((item) => item.rarity === 'SSR');
        if (hasSSR) sfx.ssr(); else sfx.like();
      }
    } catch (err) {
      hideProgress();
      toast('导入失败：' + err.message, 'error');
    } finally {
      state.busy = false;
    }
  }

  function reportImport(result) {
    const imported = result.imported || [];
    const duplicates = result.duplicates || [];
    const failures = result.failures || [];
    if (imported.length) toast(`成功导入 ${imported.length} 份简历`, 'success');
    if (duplicates.length) toast(`${duplicates.length} 份重复简历已跳过`, 'info');
    failures.forEach((item) => toast(`${item.filename}：${item.error}`, 'error'));
    if (!imported.length && !duplicates.length && !failures.length) {
      toast('没有可导入的文件', 'warn');
    }
  }

  // ============ 进度 ============
  function showProgress(done, total, name) {
    $('progressOverlay').classList.remove('hidden');
    updateProgress(done, total, name);
  }
  function updateProgress(done, total, name) {
    const percent = total ? Math.min(100, Math.round((done / total) * 100)) : 0;
    $('progressFill').style.width = percent + '%';
    $('progressPercent').textContent = percent + '%';
    $('progressFile').textContent = name || '';
  }
  function hideProgress() {
    $('progressOverlay').classList.add('hidden');
  }

  // ============ 速筛队列 ============
  function buildQueue() {
    // 只把已完成 AI 评估的简历排入速筛队列——没分数的简历没法判断
    const pending = state.candidates.filter(
      (item) => !item.review_verdict && item.eval_status === 'done');
    pending.sort((a, b) => b.overall_score - a.overall_score);
    state.reviewQueue = pending;
    state.queueIndex = 0;
    state.current = null;
  }

  function currentQueueItem() {
    if (!state.reviewQueue.length) return null;
    return state.reviewQueue[Math.min(state.queueIndex, state.reviewQueue.length - 1)] || null;
  }

  function refreshQueueFromState() {
    // 保持当前位置：把已评价的从队列剔除
    const reviewed = new Set(
      state.candidates.filter((c) => c.review_verdict).map((c) => c.id)
    );
    const currentId = state.current ? state.current.id : null;
    state.reviewQueue = state.reviewQueue.filter((item) => !reviewed.has(item.id));
    if (currentId) {
      const index = state.reviewQueue.findIndex((item) => item.id === currentId);
      state.queueIndex = index >= 0 ? index : Math.min(state.queueIndex, Math.max(0, state.reviewQueue.length - 1));
    }
  }

  function renderScreen() {
    const stage = $('screenStage');
    const total = state.candidates.length;
    // 优先使用后端 review 汇总；缺失时按本地评价标记推算
    const done = state.review
      ? Math.max(0, total - state.review.pending)
      : state.candidates.filter((item) => item.review_verdict).length;
    const pending = Math.max(0, total - done);

    $('screenProgressText').textContent = `${done} / ${total}`;
    $('screenRemaining').textContent = `剩余 ${pending} 份`;
    $('screenProgressBar').style.width = (total ? (done / total) * 100 : 0) + '%';

    // 未选择岗位时不进入速筛，先让用户明确要招什么岗
    if (!state.config || !state.config.role) {
      state.current = null;
      stage.innerHTML = needJobHtml();
      bindJobOptions();
      const qb = $('queueBar');
      if (qb) qb.classList.add('hidden');
      const qp = $('queueProgress');
      if (qp) qp.classList.add('hidden');
      return;
    }
    const qb2 = $('queueBar');
    if (qb2) qb2.classList.remove('hidden');

    refreshQueueFromState();
    const item = currentQueueItem();

    if (!item) {
      state.current = null;
      stage.innerHTML = doneEmptyHtml(total);
      bindDoneActions();
      return;
    }

    state.current = item;
    stage.innerHTML = screenCardHtml(item);
    bindScreenActions(item);
    if (!state.aiStatus) refreshAiStatus();
  }

  /** 速筛队列为空时的三种情况：没导入 / 还没评估 / 全部看完了 */
  function doneEmptyHtml(total) {
    if (!total) {
      return `
        <div class="empty-block">
          <div class="empty-icon">◉</div>
          <h3>简历库是空的</h3>
          <p>导入 PDF / DOCX / TXT 简历后，AI 会逐份评估，这里会按分数从高到低请你逐份过一遍。</p>
          <div class="empty-actions">
            <button class="btn btn-primary" id="doneImport">导入简历</button>
            <button class="btn" id="doneSamples">载入示例</button>
          </div>
        </div>`;
    }

    const evaluated = (state.eval && state.eval.done) || 0;
    if (!evaluated) {
      const errored = (state.eval && state.eval.error) || 0;
      const waiting = (state.eval && state.eval.pending) || 0;
      return `
        <div class="empty-block">
          <div class="empty-icon">◎</div>
          <h3>${errored && !waiting ? '所有简历都评估失败' : '还没有已评估的简历'}</h3>
          <p>${
            errored && !waiting
              ? `共 ${errored} 份评估失败。点速筛页上方的「重试失败」重新排队，或到设置页检查 AI 配置。`
              : waiting
                ? `有 ${waiting} 份简历等待 AI 评估。点上方「开始评估」执行；未配置 AI 时请先到设置页填写 API Key。`
                : '导入简历后点上方「开始评估」，AI 评估完成后简历会出现在这里。'
          }</p>
          <div class="empty-actions">
            <button class="btn btn-primary" id="doneStartQueue">开始评估</button>
            <button class="btn" id="doneSettings">AI 设置</button>
          </div>
        </div>`;
    }

    return `
      <div class="empty-block">
        <div class="empty-icon">✓</div>
        <h3>这一轮看完了</h3>
        <p>共 ${total} 份简历，已全部给出结论。可以导出结果，或重新开始一轮速筛。</p>
        <div class="empty-actions">
          <button class="btn btn-primary" id="doneExport">导出结果</button>
          <button class="btn" id="doneReset">重新开始速筛</button>
        </div>
      </div>`;
  }

  function bindDoneActions() {
    const bind = (id, handler) => {
      const node = $(id);
      if (node) node.addEventListener('click', handler);
    };
    bind('doneImport', importViaDialog);
    bind('doneSamples', loadSamples);
    bind('doneStartQueue', startQueue);
    bind('doneSettings', () => switchView('settings'));
    bind('doneExport', exportAll);
    bind('doneReset', resetReviews);
  }

  function needJobHtml() {
    const groups = state.roleGroups || [];    const labelOf = {};
    (state.roles || []).forEach((r) => { labelOf[r.key] = r.label; });

    const groupsHtml = groups.map((group) => {
      const keys = group.keys.filter((k) => k !== 'general');
      if (!keys.length) return '';
      return `
        <div class="job-group">
          <h4>${escapeHtml(group.label)}</h4>
          <div class="job-options">
            ${keys.map((k) => `<button class="job-option" data-role="${k}">${escapeHtml(labelOf[k] || k)}</button>`).join('')}
          </div>
        </div>`;
    }).join('');

    return `
      <div class="need-job" style="max-width:760px">
        <div class="icon">◎</div>
        <h3>先选择招聘岗位</h3>
        <p>
          不同岗位看重的能力不同。选定岗位后，系统会按该岗位的技能画像与要求评分，
          再把简历排进速筛队列——比混在一起筛要准得多。
        </p>
        ${groupsHtml}
        <div class="job-options" style="margin-top:8px">
          <button class="job-option" data-role="auto">不确定，自动识别</button>
          <button class="job-option" data-role="__custom">自定义岗位…</button>
        </div>

        <div class="custom-role-box hidden" id="customRoleBox">
          <h4>自定义招聘岗位</h4>
          <p>适用于列表里没有的岗位。填入岗位名称和该岗位最看重的关键词，系统按这些词评估匹配度。</p>
          <label class="field">
            <span>岗位名称</span>
            <input type="text" id="customRoleLabel" placeholder="例如：短视频编导" maxlength="40">
          </label>
          <label class="field">
            <span>岗位关键词（逗号分隔）</span>
            <textarea id="customRoleKeywords" rows="2" placeholder="例如：脚本, 拍摄, 剪辑, 选题, 爆款"></textarea>
          </label>
          <button class="btn btn-primary btn-block" id="btnSaveCustomRole">使用这个岗位开始评分</button>
        </div>
      </div>`;
  }

  function bindJobOptions() {
    $$('#screenStage [data-role]').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (btn.dataset.role === '__custom') {
          $('customRoleBox').classList.toggle('hidden');
          $('customRoleLabel').focus();
          return;
        }
        chooseJob(btn.dataset.role);
      });
    });

    const saveBtn = $('btnSaveCustomRole');
    if (saveBtn) saveBtn.addEventListener('click', saveCustomRole);
  }

  async function saveCustomRole() {
    const label = $('customRoleLabel').value.trim();
    const keywords = $('customRoleKeywords').value
      .split(/[,，、\s]+/).map((w) => w.trim()).filter(Boolean);

    if (!label) {
      toast('请填写岗位名称', 'warn');
      $('customRoleLabel').focus();
      return;
    }
    if (!keywords.length) {
      toast('请至少填写一个岗位关键词', 'warn');
      $('customRoleKeywords').focus();
      return;
    }

    try {
      const updated = {
        ...state.config,
        role: 'custom',
        custom_role: { label, keywords },
      };
      const result = await call('save_config', updated);
      state.config = result.config;
      applyPayload(result);
      buildQueue();
      syncJobPicker();
      renderAll();
      const doneCount = (state.eval || {}).done || 0;
      toast(doneCount > 0
        ? `已切换到「${label}」。已有分数来自上一个岗位，建议点「全部重新评估」`
        : `已切换到自定义岗位「${label}」`, 'success');
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  async function chooseJob(roleKey) {
    try {
      const updated = { ...state.config, role: roleKey };
      const result = await call('save_config', updated);
      state.config = result.config;
      applyPayload(result);
      buildQueue();
      syncJobPicker();
      renderAll();
      const label = (state.roles.find((r) => r.key === roleKey) || {}).label || '自动识别';
      const done = (state.eval || {}).done || 0;
      toast(done > 0
        ? `已切换到「${label}」。已有分数来自上一个岗位，建议点「全部重新评估」刷新`
        : `已切换到「${label}」`, 'success');
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  function syncJobPicker() {
    const picker = $('jobPicker');
    const select = $('jobSelect');
    const current = state.config ? (state.config.role || '') : '';
    select.innerHTML = '<option value="">选择岗位</option>' +
      (state.roles || []).filter((r) => r.key !== 'general')
        .map((r) => `<option value="${r.key}">${escapeHtml(r.label)}</option>`).join('') +
      '<option value="auto">自动识别</option>' +
      '<option value="general">通用岗位</option>';
    select.value = current;
    picker.classList.toggle('missing', !current);
    $('jobWarn').classList.toggle('hidden', !!current);

    // 同步设置页下拉
    const roleSelect = $('roleSelect');
    if (roleSelect) roleSelect.value = current || '';
  }

  function screenCardHtml(item) {
    const rarity = item.rarity || 'R';
    const payload = item.payload || {};
    const dims = item.dimensions || [];
    const strengths = item.strengths || [];
    const weaknesses = item.weaknesses || [];
    const flash = item.news_flash || item.summary || '暂无评语';
    const roleLabel = item.detected_role_label || item.role || '未识别岗位';

    const dimTiles = dims.map((dim) => {
      const na = dim.available === false;
      return `<div class="score-tile ${na ? 'na' : ''}" title="${escapeHtml(dim.detail || '')}">
        <div class="k">${escapeHtml(dim.label)}</div>
        <div class="v">${na ? '—' : fmtScore(dim.score)}</div>
      </div>`;
    }).join('');

    const metaChips = [
      item.years ? `${Number(item.years).toFixed(1)} 年经验` : '年限未识别',
      item.degree || '学历未知',
      item.school ? `${item.school}${item.school_tier_label ? ' · ' + item.school_tier_label : ''}` : '',
      item.major || '',
    ].filter(Boolean).map((text) => `<span class="chip">${escapeHtml(text)}</span>`).join('');

    const statusBadge = item.status === 'rejected'
      ? '<span class="status-badge rejected">淘汰</span>'
      : item.status === 'flagged'
        ? '<span class="status-badge flagged">待复核</span>'
        : '<span class="status-badge pass">通过</span>';

    // 联系方式：有就展示完整号码/邮箱，没有则明确标注
    const contact = payload.contact || {};
    const phone = contact.phone || '';
    const email = contact.email || '';
    const contactHtml = `
      <div class="contact-line">
        ${phone
          ? `<span class="contact-item"><span class="icon">☎</span>${escapeHtml(phone)}</span>`
          : '<span class="contact-item empty"><span class="icon">☎</span>未识别电话</span>'}
        ${email
          ? `<span class="contact-item"><span class="icon">✉</span>${escapeHtml(email)}</span>`
          : '<span class="contact-item empty"><span class="icon">✉</span>未识别邮箱</span>'}
      </div>`;

    // 教育经历：逐段列出院校全名 + 层级 + 专业 + 时间
    const entries = payload.profile && payload.profile.education_entries
      ? payload.profile.education_entries : [];
    const eduHtml = entries.length
      ? `<div class="edu-list">${entries.map((e) => `
          <div class="edu-item">
            <div class="edu-period">${escapeHtml(e.period || '—')}</div>
            <div class="edu-body">
              <div class="edu-school">${escapeHtml(e.school || '院校未识别')}${
                e.tier_label ? `<span class="edu-tier ${['c9','c985','c211','overseas_top','double_first','overseas_known'].includes(e.tier) ? '' : 'plain'}">${escapeHtml(e.tier_label)}</span>` : ''
              }</div>
              <div class="edu-major">${escapeHtml(e.degree || '')}${e.major ? ' · ' + escapeHtml(e.major) : ''}</div>
            </div>
          </div>`).join('')}</div>`
      : '<div class="dim-note">简历中未识别到结构化的教育经历</div>';

    const aiUsed = (payload.score_source === 'ai');
    const aiBadge = aiUsed
      ? `<span class="ai-badge" title="评分与优缺点由 AI 生成">AI 评估</span>`
      : '';

    return `
      <div class="screen-card" id="screenCard">
        <div class="screen-cover cover-${rarity.toLowerCase()}">
          <div class="screen-topright">
            ${aiBadge}
            <span class="rarity-badge ${rarity.toLowerCase()}">${rarity}</span>
            ${statusBadge}
          </div>
          <div class="screen-avatar ${rarity.toLowerCase()}">${escapeHtml(initial(item.name))}</div>
        </div>
        <div class="screen-body">
          <div class="screen-name-row">
            <span class="screen-name">${escapeHtml(item.name)}</span>
            <span class="screen-sub">${escapeHtml(roleLabel)}</span>
            <span style="margin-left:auto;font-size:22px;font-weight:700">${
              item.eval_status === 'done'
                ? `${fmtScore(item.overall_score)}<span style="font-size:12px;color:var(--text-3);font-weight:500"> 分</span>`
                : `<span class="pending-score">${item.eval_status === 'error' ? '评估失败' : '待评估'}</span>`
            }</span>
          </div>
          <div class="screen-meta-list">${metaChips}</div>
          ${contactHtml}

          <div class="verdict-line">${escapeHtml(flash)}</div>

          <div class="pros-cons">
            <div class="pros-col">
              <h4>优点</h4>
              <ul>${strengths.length ? strengths.map((s) => `<li>${escapeHtml(s)}</li>`).join('') : '<li>暂无</li>'}</ul>
            </div>
            <div class="cons-col">
              <h4>不足</h4>
              <ul>${weaknesses.length ? weaknesses.map((s) => `<li>${escapeHtml(s)}</li>`).join('') : '<li>暂无</li>'}</ul>
            </div>
          </div>

          <div class="score-panel">${dimTiles}</div>

          <div style="margin-bottom:16px">
            <div style="font-size:12px;font-weight:700;color:var(--text-3);letter-spacing:.4px;margin-bottom:8px">教育经历</div>
            ${eduHtml}
          </div>

          <div class="screen-actions">
            <button class="action-btn action-pass" data-verdict="passed">✕ 不合适</button>
            <button class="action-btn action-skip" data-verdict="skipped">↓ 稍后</button>
            <button class="action-btn action-like" data-verdict="liked">♥ 喜欢</button>
          </div>
        </div>
      </div>`;
  }

  function bindScreenActions(item) {
    $$('#screenStage [data-verdict]').forEach((btn) => {
      btn.addEventListener('click', () => decide(btn.dataset.verdict));
    });
    const card = $('screenCard');
    if (card) {
      card.addEventListener('click', (event) => {
        if (event.target.closest('[data-verdict]')) return;
        openDrawer(item.id);
      });
    }
  }

  async function decide(verdict) {
    if (!state.current || state.busy) return;
    const item = state.current;
    state.busy = true;

    const card = $('screenCard');
    if (card && state.config.ui.animation) {
      card.classList.add(verdict === 'liked' ? 'leaving-right'
        : verdict === 'passed' ? 'leaving-left' : 'leaving-up');
    }
    if (verdict === 'liked') sfx.like();
    else if (verdict === 'passed') sfx.pass();
    else sfx.skip();

    try {
      const result = await call('review_candidate', item.id, verdict, '');
      applyPayload(result);
      // 标记当前项已评价，队列自然前进
      state.reviewQueue = state.reviewQueue.filter((row) => row.id !== item.id);
      state.queueIndex = Math.min(state.queueIndex, Math.max(0, state.reviewQueue.length - 1));
      state.current = null;
      renderChrome();
      setTimeout(() => renderScreen(), state.config.ui.animation ? 180 : 0);
    } catch (err) {
      toast(err.message, 'error');
      renderScreen();
    } finally {
      state.busy = false;
    }
  }

  // ============ 评估队列 ============

  async function startQueue() {
    if (!state.aiStatus) {
      await refreshAiStatus();
    }
    if (!state.aiStatus || !state.aiStatus.ready) {
      toast(state.aiStatus && state.aiStatus.reason
        ? state.aiStatus.reason : '请先在设置页配置 AI', 'warn');
      switchView('settings');
      return;
    }
    try {
      const result = await call('queue_start');
      applyQueue(result);
      toast('已开始 AI 评估', 'success');
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  async function pauseQueue() {
    try {
      const result = await call('queue_pause');
      applyQueue(result);
      toast('已暂停，当前这份完成后停下', 'info');
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  async function retryFailed() {
    try {
      const result = await call('queue_retry_failed');
      applyPayload(result);
      applyQueue(result);
      toast('失败的简历已重新排队', 'success');
      if (state.aiStatus && state.aiStatus.ready) {
        await call('queue_start');
      }
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  async function requeueAll() {
    const ok = await confirmDialog(
      '全部重新评估',
      '将把所有简历标记为待评估（含已评估的），下一轮队列会用当前岗位与规则重新问 AI。'
    );
    if (!ok) return;
    try {
      const result = await call('queue_requeue_all');
      applyPayload(result);
      applyQueue(result);
      toast('已全部重新排队，点「开始评估」执行', 'success');
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  function applyQueue(result) {
    if (result.queue) state.queue = result.queue;
    if (result.eval) state.eval = result.eval;
    renderQueueBar();
    renderChrome();
  }

  /** 队列状态栏 */
  function renderQueueBar() {
    const bar = $('queueBar');
    if (!bar) return;
    const q = state.queue || {};
    const ev = state.eval || {};
    const aiReady = !!(state.aiStatus && state.aiStatus.ready);

    const stateText = $('queueStateText');
    const dot = $('queueDot');
    const detail = $('queueDetail');
    const progressWrap = $('queueProgress');
    const fill = $('queueProgressFill');

    dot.className = 'queue-dot';
    if (!aiReady) {
      stateText.textContent = 'AI 未配置';
      detail.textContent = '请到设置页填写 API Key 后才能评估';
      dot.classList.add('off');
    } else if (q.state === 'running') {
      stateText.textContent = '正在评估';
      detail.textContent = q.current
        ? `正在处理：${q.current.name}` : '准备中…';
      dot.classList.add('on');
    } else if (q.state === 'paused') {
      stateText.textContent = '已暂停';
      detail.textContent = `剩余 ${ev.pending || 0} 份待评估`;
      dot.classList.add('paused');
    } else {
      stateText.textContent = '未开始';
      detail.textContent = (ev.pending || 0) > 0
        ? `有 ${ev.pending} 份等待评估` : '全部已评估';
      dot.classList.add('off');
    }

    const total = ev.total || 0;
    const done = ev.done || 0;
    if (progressWrap && fill) {
      if (total > 0) {
        progressWrap.classList.remove('hidden');
        fill.style.width = Math.round((done / total) * 100) + '%';
      } else {
        progressWrap.classList.add('hidden');
      }
    }

    const startBtn = $('btnQueueStart');
    const pauseBtn = $('btnQueuePause');
    const retryBtn = $('btnQueueRetry');
    if (startBtn) startBtn.disabled = !aiReady || q.state === 'running';
    if (pauseBtn) pauseBtn.disabled = q.state !== 'running';
    if (retryBtn) retryBtn.disabled = !(ev.error > 0);
  }

  async function testAiConnection() {
    const key = $('aiApiKey').value.trim();
    if (!key) {
      toast('请先填写 API Key', 'warn');
      $('aiApiKey').focus();
      return;
    }
    if (!$('optAiEnabled').checked) {
      toast('请先勾选「启用 AI 评估」', 'warn');
      return;
    }
    const btn = $('btnAiTest');
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = '测试中…';
    try {
      await saveAiConfigOnly();
      const result = await call('ai_test_connection');
      toast(result.message, 'success');
      await refreshAiStatus();
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = original;
    }
  }

  async function saveAiConfigOnly() {
    const llm = {
      ...state.config.llm,
      enabled: $('optAiEnabled').checked,
      base_url: $('aiBaseUrl').value.trim() || 'https://api.openai.com/v1',
      api_key: $('aiApiKey').value.trim(),
      model: $('aiModel').value.trim() || 'gpt-4o-mini',
      timeout: Number($('aiTimeout').value) || 90,
      auto_start: $('optAiAutoStart').checked,
    };
    const result = await call('save_config', { ...state.config, llm });
    state.config = result.config;
    // 把服务端归一化后的值写回表单，避免用户看到"填了却没生效"
    const saved = result.config.llm || {};
    $('aiBaseUrl').value = saved.base_url || '';
    $('aiApiKey').value = saved.api_key || '';
    $('aiModel').value = saved.model || '';
    $('optAiEnabled').checked = !!saved.enabled;
    $('optAiAutoStart').checked = saved.auto_start !== false;
    return result;
  }

  async function refreshAiStatus() {
    try {
      const result = await call('ai_status');
      state.aiStatus = result;
      if (result.queue) state.queue = result.queue;
      if (result.eval) state.eval = result.eval;
      updateAiSidebar();
      renderQueueBar();
      if (state.view === 'screen') renderScreen();
    } catch (err) { /* 忽略 */ }
  }

  function updateAiSidebar() {
    const dot = $('aiSidebarDot');
    const text = $('aiSidebarText');
    const ready = !!(state.aiStatus && state.aiStatus.ready);
    dot.className = 'side-avatar ' + (ready ? 'green' : 'gray');
    dot.textContent = ready ? '●' : '○';
    text.textContent = ready ? '已启用' : '未启用';
  }

  async function resetReviews() {
    const ok = await confirmDialog('重新开始速筛', '将清除所有已记录的「喜欢 / 不合适 / 稍后」标记，简历本身不会删除。');
    if (!ok) return;
    try {
      const result = await call('reset_reviews');
      applyPayload(result);
      buildQueue();
      renderAll();
      toast('已重置速筛进度', 'success');
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  // ============ 简历库 ============
  function matchesSearch(item) {
    if (!state.search) return true;
    const haystack = [
      item.name, item.filename, item.school, item.degree, item.summary,
      item.detected_role_label, item.major,
      (((item.payload || {}).sections || {}).skills || '').slice(0, 800),
    ].join(' ').toLowerCase();
    return haystack.includes(state.search);
  }

  function filteredCandidates() {
    let list = state.candidates.filter((item) => {
      if (state.rarityFilter === '__reviewed') {
        if (!item.review_verdict) return false;
      } else if (state.rarityFilter) {
        if (item.rarity !== state.rarityFilter) return false;
      }
      if (state.filterYears && (item.years || 0) < Number(state.filterYears)) return false;
      if (state.filterDegree) {
        if ((DEGREE_RANK[item.degree] || 0) < (DEGREE_RANK[state.filterDegree] || 0)) return false;
      }
      return matchesSearch(item);
    });

    const dir = state.sortDesc ? -1 : 1;
    list.sort((a, b) => {
      if (state.sortBy === 'name') return a.name.localeCompare(b.name, 'zh') * dir;
      if (state.sortBy === 'years') return ((a.years || 0) - (b.years || 0)) * dir;
      if (state.sortBy === 'created') return String(a.created_at).localeCompare(String(b.created_at)) * dir;
      const aDone = a.eval_status === 'done' ? 1 : 0;
      const bDone = b.eval_status === 'done' ? 1 : 0;
      if (aDone !== bDone) return (aDone - bDone) * dir;
      return ((a.overall_score - b.overall_score) || (a.id - b.id)) * dir;
    });
    return list;
  }

  function syncRarityTabs() {
    $$('#rarityTabs button').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.rarity === state.rarityFilter);
    });
  }

  function renderLibrary() {
    const body = $('libraryBody');
    const list = filteredCandidates();
    state.filteredIds = list.map((item) => item.id);

    if (!list.length) {
      body.innerHTML = `
        <div class="empty-block">
          <div class="empty-icon">▤</div>
          <h3>${state.candidates.length ? '没有符合条件的简历' : '简历库是空的'}</h3>
          <p>${state.candidates.length
            ? '试试调整稀有度、经验或学历筛选，或清空搜索关键词。'
            : '导入 PDF / DOCX / TXT 简历后，这里会显示全部简历与评分结果。'}</p>
          <div class="empty-actions">
            <button class="btn btn-primary" id="libImport">导入简历</button>
            <button class="btn" id="libSamples">载入示例</button>
          </div>
        </div>`;
      $('libImport').addEventListener('click', importViaDialog);
      $('libSamples').addEventListener('click', loadSamples);
      renderBulkBar();
      return;
    }

    const cards = list.map((item) => personCardHtml(item)).join('');
    body.innerHTML = `<div class="cards ${state.layout}">${cards}</div>`;
    $$('#libraryBody .person-card').forEach((node) => {
      const id = Number(node.dataset.id);
      node.addEventListener('click', (event) => {
        if (event.target.closest('.person-check')) {
          if (state.selected.has(id)) state.selected.delete(id);
          else state.selected.add(id);
          renderLibrary();
          return;
        }
        openDrawer(id);
      });
      node.addEventListener('contextmenu', (event) => {
        event.preventDefault();
        if (state.selected.has(id)) state.selected.delete(id);
        else state.selected.add(id);
        renderLibrary();
      });
    });
    renderBulkBar();
  }

  function personCardHtml(item) {
    const rarity = (item.rarity || 'R').toLowerCase();
    const reviewed = item.review_verdict;
    const verdictChip = reviewed === 'liked' ? '<span class="status-badge pass">喜欢</span>'
      : reviewed === 'passed' ? '<span class="status-badge rejected">不合适</span>'
      : reviewed === 'skipped' ? '<span class="status-badge flagged">稍后</span>' : '';
    // 技能标签取本地抽取的技能章节（AI 只给判断，不负责抄字段）
    const skillText = ((item.payload || {}).sections || {}).skills || '';
    const skillTags = skillText
      .split(/[、,，\n\/|]+/)
      .map((t) => t.trim())
      .filter((t) => t && t.length <= 16)
      .slice(0, 4);
    const tags = skillTags.length
      ? `<div class="person-tags">${skillTags.map((t) => `<span class="chip">${escapeHtml(t)}</span>`).join('')}</div>`
      : '';
    const selected = state.selected.has(item.id);
    const evaluated = item.eval_status === 'done';
    const failed = item.eval_status === 'error';

    return `
      <div class="person-card ${selected ? 'selected' : ''} ${evaluated ? '' : 'pending'}" data-id="${item.id}">
        <div class="person-avatar ${rarity}">${escapeHtml(initial(item.name))}</div>
        <div class="person-main">
          <div class="person-top">
            <span class="person-name">${escapeHtml(item.name)}</span>
            ${evaluated ? `<span class="rarity-badge ${rarity}">${item.rarity}</span>` : ''}
            ${evaluated && item.payload && item.payload.score_source === 'ai' ? '<span class="ai-badge">AI</span>' : ''}
            ${!evaluated && !failed ? '<span class="status-badge flagged">待评估</span>' : ''}
            ${failed ? `<span class="eval-error-badge" title="${escapeHtml(item.eval_error || '')}">评估失败</span>` : ''}
            ${verdictChip}
          </div>
          <div class="person-role">
            ${escapeHtml(item.detected_role_label || item.role || '未识别岗位')}
            ${item.years ? ` · ${Number(item.years).toFixed(1)} 年` : ''}
            ${item.degree ? ` · ${escapeHtml(item.degree)}` : ''}
            ${item.school ? ` · ${escapeHtml(item.school)}` : ''}
          </div>
          <div class="person-flash">${escapeHtml(item.news_flash || item.summary || '')}</div>
          ${tags}
        </div>
        <div class="person-right">
          <span class="person-score ${rarity} ${evaluated ? '' : 'pending-score'}">${
            evaluated ? fmtScore(item.overall_score) : (failed ? '!' : '—')
          }</span>
          <span class="person-time">${escapeHtml((item.created_at || '').slice(5, 16).replace('T', ' '))}</span>
        </div>
      </div>`;
  }

  /** 全选当前筛选后的列表（不是全库，避免误删被筛选隐藏的简历） */
  function selectAllVisible() {
    const list = filteredCandidates();
    if (!list.length) {
      toast('当前列表为空，没有可选择的简历', 'warn');
      return;
    }
    list.forEach((item) => state.selected.add(item.id));
    renderLibrary();
    toast(`已选中当前列表全部 ${list.length} 份`, 'success');
  }

  function invertSelection() {
    const list = filteredCandidates();
    if (!list.length) return;
    list.forEach((item) => {
      if (state.selected.has(item.id)) state.selected.delete(item.id);
      else state.selected.add(item.id);
    });
    renderLibrary();
  }

  function renderBulkBar() {
    const bar = $('bulkBar');
    const visibleIds = state.filteredIds || [];
    const visibleSelected = visibleIds.filter((id) => state.selected.has(id)).length;

    // 计数与全选复选框始终同步
    const totalEl = $('selectTotal');
    const countEl = $('selectCount');
    if (totalEl) totalEl.textContent = visibleIds.length;
    if (countEl) countEl.textContent = state.selected.size;

    const chk = $('chkSelectAll');
    if (chk) {
      chk.checked = visibleIds.length > 0 && visibleSelected === visibleIds.length;
      chk.indeterminate = visibleSelected > 0 && visibleSelected < visibleIds.length;
    }

    if (!state.selected.size) {
      bar.classList.add('hidden');
      return;
    }
    bar.classList.remove('hidden');
    $('bulkCount').textContent = `已选 ${state.selected.size} 项`;
  }

  // ============ 详情抽屉 ============
  async function openDrawer(id) {
    try {
      const result = await call('get_candidate_detail', Number(id));
      const data = result.candidate;
      state.drawerId = data.id;
      const payload = data.payload || {};

      $('drawerRarity').className = 'rarity-badge ' + String(data.rarity).toLowerCase();
      $('drawerRarity').textContent = data.rarity;
      $('drawerName').textContent = data.name;
      $('drawerMeta').textContent = [
        data.filename,
        payload.detected_role_label ? `岗位：${payload.detected_role_label}` : '',
        `导入于 ${String(data.created_at || '').replace('T', ' ')}`,
      ].filter(Boolean).join(' · ');
      $('drawerBody').innerHTML = drawerHtml(data, payload);
      $('drawerFoot').innerHTML = `
        <button class="btn" id="btnOpenFile">打开原文件</button>
        <button class="btn btn-primary" id="btnDrawerLike">喜欢</button>
        <button class="btn btn-danger" id="btnDrawerPass">不合适</button>`;
      $('btnOpenFile').addEventListener('click', openOriginalFile);
      $('btnDrawerLike').addEventListener('click', () => drawerDecide('liked'));
      $('btnDrawerPass').addEventListener('click', () => drawerDecide('passed'));

      $('drawerMask').classList.remove('hidden');
      $('drawer').classList.remove('hidden');
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  function drawerEducationHtml(payload) {
    const entries = (payload.profile && payload.profile.education_entries) || [];
    if (!entries.length) {
      return '<div class="dim-note">简历中未识别到结构化的教育经历</div>';
    }
    return `<div class="edu-list">${entries.map((e) => `
      <div class="edu-item">
        <div class="edu-period">${escapeHtml(e.period || '—')}</div>
        <div class="edu-body">
          <div class="edu-school">${escapeHtml(e.school || '院校未识别')}${
            e.tier_label ? `<span class="edu-tier ${['c9','c985','c211','overseas_top','double_first','overseas_known'].includes(e.tier) ? '' : 'plain'}">${escapeHtml(e.tier_label)}</span>` : ''
          }</div>
          <div class="edu-major">${escapeHtml(e.degree || '')}${e.major ? ' · ' + escapeHtml(e.major) : ''}</div>
          ${e.raw ? `<div class="dim-note" style="margin-top:4px">${escapeHtml(e.raw)}</div>` : ''}
        </div>
      </div>`).join('')}</div>`;
  }

  function drawerHtml(data, payload) {
    const dims = payload.dimensions || [];
    const reasons = payload.screen_reasons || [];
    const strengths = payload.strengths || [];
    const weaknesses = payload.weaknesses || [];

    const dimRows = dims.map((dim) => {
      const na = dim.available === false;
      return `
        <div class="dim-row">
          <div class="dim-row-top">
            <span>${escapeHtml(dim.label)}${na ? '（未参与）' : ''}</span>
            <b>${na ? '—' : fmtScore(dim.score)}${na ? '' : ` <span style="font-weight:400;color:var(--text-3);font-size:11.5px">× ${(dim.weight * 100).toFixed(0)}%</span>`}</b>
          </div>
          <div class="dim-track"><div class="dim-fill" style="width:${na ? 0 : Math.min(100, dim.score)}%"></div></div>
          <div class="dim-note">${escapeHtml(dim.detail || '')}</div>
        </div>`;
    }).join('');

    // 技能由本地抽取的技能章节展示；不再有规则引擎的 skill_hits 命中统计
    const skillText = ((payload.sections || {}).skills || '').trim();

    const reasonBox = reasons.length
      ? `<div class="api-box" style="border-color:${payload.status === 'rejected' ? '#f5c2c0' : '#ffe08a'};background:${payload.status === 'rejected' ? 'var(--danger-soft)' : 'var(--warning-soft)'}">
           <b>${payload.status === 'rejected' ? '筛选未通过' : '需人工复核'}：</b>
           ${reasons.map(escapeHtml).join('；')}
         </div>`
      : '';

    const projects = (payload.projects || []).map((p) => `
      <div class="info-cell" style="grid-column:1/-1;margin-bottom:8px">
        <div class="k">${escapeHtml(p.title)}</div>
        <div style="font-size:12.5px;color:var(--text-2);line-height:1.6;margin-top:3px;white-space:pre-wrap">${escapeHtml((p.description || '').slice(0, 400))}</div>
      </div>`).join('');

    return `
      ${reasonBox}
      <div class="section-block">
        <div style="padding:12px 14px;border-radius:12px;background:var(--surface-2);font-size:14px;line-height:1.6">
          ${escapeHtml(payload.news_flash || payload.summary || '')}
        </div>
      </div>

      <div class="section-block">
        <h4>基本信息</h4>
        <div class="info-grid">
          <div class="info-cell"><div class="k">经验年限</div><div class="v">${(payload.years || 0).toFixed(1)} 年</div></div>
          <div class="info-cell"><div class="k">学历</div><div class="v">${escapeHtml(payload.degree || '未识别')}</div></div>
          <div class="info-cell"><div class="k">院校</div><div class="v">${escapeHtml(payload.school || '未识别')}${payload.school_tier_label ? ` · ${escapeHtml(payload.school_tier_label)}` : ''}</div></div>
          <div class="info-cell"><div class="k">专业</div><div class="v">${escapeHtml(payload.major || '未识别')}</div></div>
          <div class="info-cell"><div class="k">识别岗位</div><div class="v">${escapeHtml(payload.detected_role_label || '未识别')}</div></div>
          <div class="info-cell"><div class="k">综合分</div><div class="v">${fmtScore(payload.overall_score)} · ${escapeHtml(payload.rarity || '')}</div></div>
        </div>
      </div>

      <div class="section-block">
        <h4>联系方式</h4>
        <div class="contact-line" style="margin:0">
          ${(payload.contact && payload.contact.phone)
            ? `<span class="contact-item"><span class="icon">☎</span>${escapeHtml(payload.contact.phone)}</span>`
            : '<span class="contact-item empty">未识别电话</span>'}
          ${(payload.contact && payload.contact.email)
            ? `<span class="contact-item"><span class="icon">✉</span>${escapeHtml(payload.contact.email)}</span>`
            : '<span class="contact-item empty">未识别邮箱</span>'}
        </div>
      </div>

      <div class="section-block">
        <h4>教育经历</h4>
        ${drawerEducationHtml(payload)}
      </div>

      <div class="section-block">
        <h4>优点</h4>
        <div class="pros-col" style="background:var(--success-soft)">
          <ul>${strengths.length ? strengths.map((s) => `<li>${escapeHtml(s)}</li>`).join('') : '<li>暂无</li>'}</ul>
        </div>
      </div>

      <div class="section-block">
        <h4>不足</h4>
        <div class="cons-col" style="background:var(--danger-soft)">
          <ul>${weaknesses.length ? weaknesses.map((s) => `<li>${escapeHtml(s)}</li>`).join('') : '<li>暂无</li>'}</ul>
        </div>
      </div>

      <div class="section-block">
        <h4>评分明细</h4>
        ${dimRows || '<div class="dim-note">无评分数据</div>'}
      </div>

      ${skillText ? `<div class="section-block">
        <h4>技能</h4>
        <div class="raw-text" style="max-height:none">${escapeHtml(skillText.slice(0, 1200))}</div>
      </div>` : ''}

      ${projects ? `<div class="section-block"><h4>项目经历</h4>${projects}</div>` : ''}

      <details class="collapse">
        <summary>查看简历原文</summary>
        <div class="raw-text" style="margin-top:8px">${escapeHtml(data.raw_text || '（未保存原文，可在设置中开启）')}</div>
      </details>`;
  }

  function closeDrawer() {
    $('drawer').classList.add('hidden');
    $('drawerMask').classList.add('hidden');
    state.drawerId = null;
  }

  async function drawerDecide(verdict) {
    if (!state.drawerId) return;
    try {
      const result = await call('review_candidate', state.drawerId, verdict, '');
      applyPayload(result);
      closeDrawer();
      renderAll();
      if (verdict === 'liked') sfx.like(); else sfx.pass();
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  async function openOriginalFile() {
    if (!state.drawerId) return;
    try {
      const result = await call('get_candidate', state.drawerId);
      const path = result.candidate.stored_path;
      if (!path) {
        toast('该简历没有保存原始文件副本', 'warn');
        return;
      }
      await call('open_path', path);
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  // ============ 导出 ============
  function pickExportFormat() {
    return new Promise((resolve) => {
      const layer = document.createElement('div');
      layer.className = 'modal-layer';
      layer.innerHTML = `
        <div class="modal-card">
          <h3>导出筛选结果</h3>
          <p>包含综合分、稀有度、五维明细、优缺点、联系方式与人工评价。</p>
          <div class="modal-options">
            <button class="modal-option" data-fmt="csv"><b>CSV 表格</b><span>Excel 直接打开</span></button>
            <button class="modal-option" data-fmt="json"><b>JSON 数据</b><span>完整字段，便于程序处理</span></button>
            <button class="modal-option" data-fmt="md"><b>Markdown 报告</b><span>可直接阅读</span></button>
          </div>
          <button class="btn btn-block" data-fmt="">取消</button>
        </div>`;
      layer.addEventListener('click', (event) => {
        const picked = event.target.closest('[data-fmt]');
        if (!picked) return;
        layer.remove();
        resolve(picked.dataset.fmt || null);
      });
      document.body.appendChild(layer);
    });
  }

  function confirmDialog(title, message) {
    return new Promise((resolve) => {
      const layer = document.createElement('div');
      layer.className = 'modal-layer';
      layer.innerHTML = `
        <div class="modal-card">
          <h3>${escapeHtml(title)}</h3>
          <p>${escapeHtml(message)}</p>
          <div class="modal-row">
            <button class="btn" data-answer="no">取消</button>
            <button class="btn btn-danger" data-answer="yes">确认</button>
          </div>
        </div>`;
      layer.addEventListener('click', (event) => {
        const answer = event.target.closest('[data-answer]');
        if (!answer) return;
        layer.remove();
        resolve(answer.dataset.answer === 'yes');
      });
      document.body.appendChild(layer);
    });
  }

  async function runExport(fmt, ids) {
    try {
      const result = await call('export_results', fmt, ids);
      toast(`已导出 ${result.count} 份到 ${result.path}`, 'success');
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  async function exportAll() {
    if (!state.candidates.length) {
      toast('还没有可导出的简历', 'warn');
      return;
    }
    const fmt = await pickExportFormat();
    if (!fmt) return;
    const reviewedOnly = state.rarityFilter === '__reviewed';
    const ids = reviewedOnly
      ? state.candidates.filter((c) => c.review_verdict).map((c) => c.id)
      : null;
    await runExport(fmt, ids);
  }

  async function exportSelected() {
    const ids = Array.from(state.selected);
    if (!ids.length) {
      toast('请先选择要导出的简历', 'warn');
      return;
    }
    const fmt = await pickExportFormat();
    if (!fmt) return;
    await runExport(fmt, ids);
  }

  async function deleteSelected() {
    if (!state.selected.size) return;
    const ok = await confirmDialog('删除简历', `确认删除选中的 ${state.selected.size} 份简历？此操作不可撤销。`);
    if (!ok) return;
    try {
      const result = await call('delete_candidates', Array.from(state.selected));
      state.selected.clear();
      applyPayload(result);
      buildQueue();
      renderAll();
      toast(`已删除 ${result.removed} 份`, 'success');
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  // ============ 统计 ============
  function renderStats() {
    const stats = state.stats || {};
    const counts = stats.rarity_counts || { R: 0, SR: 0, SSR: 0 };
    const review = state.review || {};

    const tiles = [
      { k: '简历总数', v: stats.total || 0, s: '本地解析' },
      { k: 'SSR', v: counts.SSR || 0, s: `占 ${stats.ssr_rate || 0}%` },
      { k: 'SR', v: counts.SR || 0, s: '推荐' },
      { k: 'R', v: counts.R || 0, s: '备选' },
      { k: '平均分', v: fmtScore(stats.avg_score), s: `最高 ${fmtScore(stats.max_score)}` },
      { k: '速筛完成', v: (review.progress || 0) + '%', s: `${review.pending || 0} 份待看` },
      { k: '喜欢', v: review.liked || 0, s: '人工标记' },
      { k: '不合适', v: review.passed || 0, s: '人工标记' },
    ];
    $('statGrid').innerHTML = tiles.map((t) => `
      <div class="stat-tile">
        <div class="k">${escapeHtml(t.k)}</div>
        <div class="v">${t.v}</div>
        <div class="s">${escapeHtml(t.s)}</div>
      </div>`).join('');

    renderCharts();
  }

  function renderCharts() {
    if (state.view !== 'stats' || !window.MiniChart) return;
    const counts = (state.stats && state.stats.rarity_counts) || { R: 0, SR: 0, SSR: 0 };

    MiniChart.donut($('rarityChart'), [
      { label: 'SSR', value: counts.SSR || 0, color: '#d9a400' },
      { label: 'SR', value: counts.SR || 0, color: '#7c4dd6' },
      { label: 'R', value: counts.R || 0, color: '#a5abb3' },
    ], '份简历');

    const buckets = new Array(10).fill(0);
    state.candidates.forEach((item) => {
      const index = Math.min(9, Math.max(0, Math.floor(item.overall_score / 10)));
      buckets[index] += 1;
    });
    MiniChart.bars(
      $('scoreChart'),
      buckets.map((_, i) => `${i * 10}`),
      buckets,
      ['#1877f2'],
      { max: Math.max(1, ...buckets) }
    );

    const totals = {};
    const dimCounts = {};
    state.candidates.forEach((item) => {
      (item.dimensions || []).forEach((dim) => {
        if (dim.available === false) return;
        totals[dim.key] = (totals[dim.key] || 0) + dim.score;
        dimCounts[dim.key] = (dimCounts[dim.key] || 0) + 1;
      });
    });
    const order = ['skill', 'experience', 'education', 'project', 'keyword', 'job_match'];
    const labels = { skill: '技能', experience: '经验', education: '学历', project: '项目', keyword: '关键词', job_match: '岗位' };
    const names = [];
    const values = [];
    order.forEach((key) => {
      if (dimCounts[key]) {
        names.push(labels[key]);
        values.push(totals[key] / dimCounts[key]);
      }
    });
    MiniChart.hbars($('dimChart'), names.length ? names : ['暂无'], names.length ? values : [0], '#1877f2');
  }

  // ============ 设置 ============
  function syncSettingsForm() {
    if (!state.config) return;
    const cfg = state.config;
    $('ssrRange').value = cfg.rarity.ssr;
    $('srRange').value = cfg.rarity.sr;
    $('ssrVal').textContent = cfg.rarity.ssr;
    $('srVal').textContent = cfg.rarity.sr;
    updateThresholdPreview();

    const roleSelect = $('roleSelect');
    roleSelect.innerHTML = '<option value="">未选择（无法开始速筛）</option>' +
      (state.roles || []).filter((r) => r.key !== 'general')
        .map((r) => `<option value="${r.key}">${escapeHtml(r.label)}</option>`).join('') +
      '<option value="auto">自动识别</option>' +
      '<option value="general">通用岗位</option>';
    roleSelect.value = cfg.role || '';
    roleSelect.onchange = () => {
      const value = roleSelect.value;
      if (value) chooseJob(value);
    };

    $('keywordsInput').value = (cfg.keywords || []).join(', ');
    $('jdInput').value = cfg.jd_text || '';
    $('minScore').value = cfg.screening.min_score || 0;
    $('minYears').value = cfg.screening.min_years || 0;
    $('minDegree').value = cfg.screening.required_degree || '';
    $('mustKeywords').value = (cfg.screening.must_keywords || []).join(', ');
    $('rejectKeywords').value = (cfg.screening.reject_keywords || []).join(', ');

    $('optSound').checked = cfg.ui.sound;
    $('optAnimation').checked = cfg.ui.animation;
    $('optRawText').checked = cfg.privacy.store_raw_text;
    $('optApiAuto').checked = (cfg.api || {}).enabled !== false;
    $('apiPort').value = (cfg.api || {}).port || 8765;

    $('dataDir').textContent = state.meta.data_dir || '';
    $('configFile').textContent = state.meta.config_path || '';
    $('logFile').textContent = state.meta.log_path || '';

    // AI 评估配置
    const llm = cfg.llm || {};
    $('optAiEnabled').checked = !!llm.enabled;
    $('aiBaseUrl').value = llm.base_url || 'https://api.openai.com/v1';
    $('aiApiKey').value = llm.api_key || '';
    $('aiModel').value = llm.model || 'gpt-4o-mini';
    $('aiTimeout').value = llm.timeout || 90;
    $('optAiAutoStart').checked = llm.auto_start !== false;

    updateSoundButton();
    renderApiBox();
    paintAllRanges();
    updateAiSidebar();
    renderQueueBar();
  }

  function updateThresholdPreview() {
    const ssr = Number($('ssrRange').value);
    const sr = Number($('srRange').value);
    $('ssrVal').textContent = ssr;
    $('srVal').textContent = sr;
    const preview = $('thresholdPreview');
    if (sr >= ssr) {
      preview.innerHTML = '<div style="width:100%;background:#e41e3f">SR 阈值必须小于 SSR 阈值</div>';
      return;
    }
    paintAllRanges();
    preview.innerHTML = `
      <div style="width:${Math.max(2, sr)}%;background:#8a8d91">R</div>
      <div style="width:${Math.max(2, ssr - sr)}%;background:#7c4dd6">SR</div>
      <div style="width:${Math.max(2, 100 - ssr)}%;background:#d9a400">SSR</div>`;
  }

  function collectSettings() {
    const splitList = (value) => value.split(/[,，、]/).map((s) => s.trim()).filter(Boolean);

    return {
      rarity: {
        ssr: Number($('ssrRange').value),
        sr: Number($('srRange').value),
        labels: state.config.rarity.labels,
      },
      role: $('roleSelect').value,
      keywords: splitList($('keywordsInput').value),
      jd_text: $('jdInput').value,
      screening: {
        min_score: Number($('minScore').value) || 0,
        min_years: Number($('minYears').value) || 0,
        required_degree: $('minDegree').value,
        must_keywords: splitList($('mustKeywords').value),
        reject_keywords: splitList($('rejectKeywords').value),
      },
      ui: {
        theme: 'light',
        view: state.layout,
        sound: $('optSound').checked,
        animation: $('optAnimation').checked,
      },
      privacy: { store_raw_text: $('optRawText').checked },
      llm: {
        enabled: $('optAiEnabled').checked,
        base_url: $('aiBaseUrl').value.trim(),
        api_key: $('aiApiKey').value.trim(),
        model: $('aiModel').value.trim() || 'gpt-4o-mini',
        temperature: (state.config.llm && state.config.llm.temperature) || 0.2,
        timeout: Number($('aiTimeout').value) || 90,
        auto_start: $('optAiAutoStart').checked,
      },
      api: {
        enabled: $('optApiAuto').checked,
        port: Number($('apiPort').value) || 8765,
      },
    };
  }

  async function saveSettings() {
    if (Number($('srRange').value) >= Number($('ssrRange').value)) {
      toast('SR 阈值必须小于 SSR 阈值', 'error');
      return;
    }
    const btn = $('btnSaveConfig');
    btn.disabled = true;
    btn.textContent = '保存中…';
    try {
      const result = await call('save_config', collectSettings());
      state.config = result.config;
      applyPayload(result);
      await syncApiWithConfig();
      await refreshAiStatus();
      syncJobPicker();
      buildQueue();
      syncSettingsForm();
      renderAll();
      toast('设置已保存。改动岗位或规则后，需点「全部重新排队」再开始评估', 'success');
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = '保存设置';
    }
  }

  async function syncApiWithConfig() {
    const cfg = state.config.api || {};
    const running = state.apiInfo && state.apiInfo.running;
    try {
      if (cfg.enabled && !running) {
        const result = await call('api_start', cfg.port || 8765);
        state.apiInfo = result.api;
      } else if (!cfg.enabled && running) {
        const result = await call('api_stop');
        state.apiInfo = result.api;
      }
    } catch (err) {
      toast('API 状态同步失败：' + err.message, 'warn');
    }
  }

  async function rescoreAll() {
    const ok = await confirmDialog(
      '全部重新排队',
      '将把所有简历标记为待评估。之后到速筛页点「开始评估」，AI 会按当前岗位重新评估。'
    );
    if (!ok) return;
    try {
      const result = await call('queue_requeue_all');
      applyPayload(result);
      buildQueue();
      renderAll();
      toast(`已排队 ${result.queued || 0} 份，点「开始评估」执行`, 'success');
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  async function resetSettings() {
    const ok = await confirmDialog('恢复默认配置', '当前阈值、岗位与筛选规则将被重置为默认值。已有评分不会自动清除，需要重评请点「全部重新排队」。');
    if (!ok) return;
    try {
      const result = await call('reset_config');
      state.config = result.config;
      applyPayload(result);
      buildQueue();
      syncSettingsForm();
      renderAll();
      toast('已恢复默认配置（已有评分保留，重评请点「全部重新排队」）', 'success');
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  async function clearAll() {
    const ok = await confirmDialog('清空全部简历', '将删除所有简历记录与文件副本，此操作不可撤销。建议先导出结果。');
    if (!ok) return;
    try {
      const result = await call('clear_all');
      applyPayload(result);
      state.selected.clear();
      buildQueue();
      renderAll();
      toast('已清空', 'success');
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  // ============ 本地 API ============
  function renderApiBox() {
    const box = $('apiBox');
    const info = state.apiInfo || {};
    const running = info.running;
    $('btnApiToggle').textContent = running ? '停止 API' : '启动 API';
    $('btnApiToggle').className = 'btn btn-sm ' + (running ? 'btn-danger' : 'btn-primary');
    box.innerHTML = `
      <div><span class="status-dot ${running ? 'on' : ''}"></span>
        ${running ? '运行中' : '未启动'}${running ? ` · 端口 ${info.port}` : ''}</div>
      ${running ? `<code>${escapeHtml(info.base_url || '')}</code>` : ''}
      <div style="margin-top:8px;font-size:12px;color:var(--text-2)">
        请求头需携带令牌：<br>
        <code style="margin-top:4px">X-API-Token: ${escapeHtml(info.token || '')}</code>
      </div>
      <div style="margin-top:8px;font-size:11.5px;color:var(--text-3)">
        仅监听 127.0.0.1，不返回简历原文与联系方式。
      </div>`;
  }

  async function toggleApi() {
    try {
      const running = state.apiInfo && state.apiInfo.running;
      const port = Number($('apiPort').value) || 8765;
      const result = running ? await call('api_stop') : await call('api_start', port);
      state.apiInfo = result.api;
      // 手动启停视为修改"自动启动"偏好
      state.config.api = { enabled: !running, port };
      try {
        const saved = await call('save_config', state.config);
        state.config = saved.config;
      } catch (err) { /* 忽略持久化失败 */ }
      renderApiBox();
      toast(running ? 'API 已停止' : 'API 已启动', 'success');
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  async function regenerateToken() {
    try {
      const result = await call('api_regenerate_token');
      state.apiInfo = result.api;
      renderApiBox();
      toast('令牌已重置，请更新调用方配置', 'success');
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  function showApiDocs() {
    const info = state.apiInfo || {};
    const token = info.token || '<token>';
    const base = info.base_url || 'http://127.0.0.1:8765';
    const layer = document.createElement('div');
    layer.className = 'modal-layer';
    layer.innerHTML = `
      <div class="modal-card" style="width:min(620px,94vw);max-height:84vh;overflow-y:auto">
        <h3>本地 AI 审查 API</h3>
        <p>供本机 AI / Agent 读取结构化评分、给出审查意见并回写结论。</p>
        <div class="api-box" style="text-align:left">
          <b>1. 健康检查（无需令牌）</b>
          <code>curl ${base}/health</code>

          <b style="display:block;margin-top:12px">2. 读取待审简历</b>
          <code>curl -H "X-API-Token: ${token}" "${base}/candidates?pending=1"</code>

          <b style="display:block;margin-top:12px">3. 读取单份详情（含评分理由与优缺点）</b>
          <code>curl -H "X-API-Token: ${token}" "${base}/candidates/1"</code>

          <b style="display:block;margin-top:12px">4. 回写审查结论</b>
          <code>curl -X POST -H "X-API-Token: ${token}" -H "Content-Type: application/json" \\
  -d '{"id":1,"verdict":"liked","note":"AI 复核：技能匹配度高"}' \\
  ${base}/review</code>

          <b style="display:block;margin-top:12px">5. 批量回写</b>
          <code>POST ${base}/review/batch  {"items":[{"id":1,"verdict":"liked"}]}</code>

          <b style="display:block;margin-top:12px">可用端点</b>
          <div style="font-size:12px;line-height:1.8;margin-top:4px">
            GET /health · GET / · GET /candidates · GET /candidates/{id}<br>
            GET /stats · GET /config · GET /export?format=json|csv|md<br>
            POST /review · POST /review/batch
          </div>

          <b style="display:block;margin-top:12px">筛选参数</b>
          <div style="font-size:12px;line-height:1.8;margin-top:4px">
            rarity=SSR|SR|R · min_score=70 · pending=1 · sections=1 · limit=100
          </div>
        </div>
        <div class="modal-row" style="margin-top:14px">
          <button class="btn" data-close>关闭</button>
        </div>
      </div>`;
    layer.addEventListener('click', (event) => {
      if (event.target === layer || event.target.closest('[data-close]')) layer.remove();
    });
    document.body.appendChild(layer);
  }

  // ============ 渲染入口 ============
  function renderChrome() {
    syncJobPicker();
    updateAiSidebar();
    const counts = { R: 0, SR: 0, SSR: 0 };
    let reviewed = 0;
    state.candidates.forEach((item) => {
      counts[item.rarity] = (counts[item.rarity] || 0) + 1;
      if (item.review_verdict) reviewed += 1;
    });
    $('countSSR').textContent = counts.SSR;
    $('countSR').textContent = counts.SR;
    $('countR').textContent = counts.R;
    $('countReviewed').textContent = reviewed;

    const pending = (state.review && state.review.pending) || 0;
    const badge = $('pendingCount');
    if (pending > 0) {
      badge.textContent = pending;
      badge.classList.remove('hidden');
    } else {
      badge.classList.add('hidden');
    }
    updateSoundButton();
  }

  function updateSoundButton() {
    const btn = $('btnSound');
    if (!btn || !state.config) return;
    const on = state.config.ui.sound;
    btn.classList.toggle('off', !on);
    btn.title = on ? '音效已开启' : '音效已关闭';
  }

  function renderAll() {
    renderChrome();
    if (state.view === 'screen') renderScreen();
    if (state.view === 'library') renderLibrary();
    if (state.view === 'stats') renderStats();
    if (state.view === 'settings') syncSettingsForm();
  }

  // ============ 自检钩子 ============
  window.__appInternals = {
    applyData(data) {
      applyPayload(data);
      buildQueue();
      renderAll();
    },
    state,
  };
  window.__probeRefresh = function () {
    if (!state.api) return;
    call('list_candidates').then((d) => {
      applyPayload(d);
      state.aiStatus = null;   // 强制重新查询 AI 可用性
      buildQueue();
      renderAll();
      refreshAiStatus();
    }).catch(() => {});
  };
  // 后端队列状态推送
  window.__queueUpdate = function (snapshot) {
    state.queue = snapshot;
    if (snapshot && snapshot.eval) state.eval = snapshot.eval;
    renderQueueBar();
    // 队列空闲下来时刷新一次列表（拿到最新分数）
    if (snapshot && snapshot.state === 'idle') {
      call('queue_status').then((d) => {
        applyPayload(d);
        buildQueue();
        renderAll();
      }).catch(() => {});
    } else if (snapshot && snapshot.finished > 0) {
      // 过程中轻量刷新计数
      renderChrome();
    }
  };

  window.__errors = [];
  window.addEventListener('error', (event) => {
    window.__errors.push(String(event.message || event.error || 'unknown'));
  });
  window.addEventListener('unhandledrejection', (event) => {
    window.__errors.push('promise: ' + String(event.reason));
  });

  document.addEventListener('DOMContentLoaded', boot);
})(window);
