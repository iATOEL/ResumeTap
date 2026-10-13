/* 简历智能筛选与评估系统 - 前端逻辑 */

let barChart = null;
let radarChart = null;
let candidatesCache = [];
let selectedRadarIds = new Set();

// ========== 工具函数 ==========

/** HTML 转义，防止 XSS 注入 */
function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/** 轻提示 */
function showToast(message, type = 'info') {
    let container = document.getElementById('toastContainer');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toastContainer';
        container.className = 'fixed top-4 right-4 z-[60] flex flex-col gap-2';
        document.body.appendChild(container);
    }
    const colors = {
        info: 'bg-blue-600',
        success: 'bg-green-600',
        error: 'bg-red-600',
    };
    const toast = document.createElement('div');
    toast.className = `${colors[type] || colors.info} text-white px-4 py-2 rounded-lg shadow-lg text-sm transition-opacity duration-300`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
        toast.classList.add('opacity-0');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ========== 初始化 ==========
document.addEventListener('DOMContentLoaded', () => {
    initUpload();
    initFilters();
    loadCandidates();
});

// ========== 上传功能 ==========
function initUpload() {
    const dropZone = document.getElementById('dropZone');
    const fileInput = document.getElementById('fileInput');

    dropZone.addEventListener('click', () => fileInput.click());

    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('border-blue-500', 'bg-blue-50');
    });

    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('border-blue-500', 'bg-blue-50');
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('border-blue-500', 'bg-blue-50');
        const files = Array.from(e.dataTransfer.files).filter(f => f.name.toLowerCase().endsWith('.docx'));
        if (files.length > 0) {
            uploadFiles(files);
        } else {
            showToast('请拖入 .docx 格式的简历文件', 'error');
        }
    });

    fileInput.addEventListener('change', (e) => {
        const files = Array.from(e.target.files);
        if (files.length > 0) uploadFiles(files);
        fileInput.value = '';
    });
}

async function uploadFiles(files) {
    const progress = document.getElementById('uploadProgress');
    const progressBar = document.getElementById('progressBar');
    const progressText = document.getElementById('progressText');

    progress.classList.remove('hidden');
    let done = 0;
    let successCount = 0;

    for (const file of files) {
        progressText.textContent = `正在处理: ${file.name} (${done + 1}/${files.length})`;
        progressBar.style.width = `${(done / files.length) * 100}%`;

        const formData = new FormData();
        formData.append('file', file);

        try {
            const resp = await fetch('/api/upload', { method: 'POST', body: formData });
            const data = await resp.json();
            if (!resp.ok) {
                showToast(`文件 ${file.name} 处理失败: ${data.detail || '未知错误'}`, 'error');
            } else {
                successCount++;
            }
        } catch (err) {
            showToast(`文件 ${file.name} 上传失败: ${err.message}`, 'error');
        }
        done++;
    }

    progressBar.style.width = '100%';
    progressText.textContent = successCount > 0
        ? `完成！成功处理 ${successCount}/${files.length} 个文件`
        : '处理失败，请重试';
    setTimeout(() => progress.classList.add('hidden'), 2000);

    loadCandidates();
}

// ========== 筛选与排序 ==========
function initFilters() {
    document.getElementById('sortBy').addEventListener('change', loadCandidates);
    document.getElementById('sortOrder').addEventListener('change', loadCandidates);
    document.getElementById('filterCategory').addEventListener('change', loadCandidates);
}

// ========== 数据加载 ==========
async function loadCandidates() {
    const sortBy = document.getElementById('sortBy').value;
    const order = document.getElementById('sortOrder').value;
    const category = document.getElementById('filterCategory').value;

    const params = new URLSearchParams({ sort_by: sortBy, order });
    if (category) params.append('category', category);

    try {
        const resp = await fetch(`/api/candidates?${params}`);
        if (!resp.ok) throw new Error('服务返回异常');
        const data = await resp.json();
        candidatesCache = data;
        renderTable(data);
        renderStats(data);
        renderBarChart(data);
        renderRadarSelector(data);
        renderRadarChart(data);
    } catch (err) {
        console.error('加载数据失败:', err);
        showToast('加载数据失败', 'error');
    }
}

// ========== 统计概览 ==========
function renderStats(data) {
    document.getElementById('statTotal').textContent = data.length;
    document.getElementById('statStrong').textContent = data.filter(c => c.category === '强烈推荐').length;
    document.getElementById('statRecommend').textContent = data.filter(c => c.category === '推荐').length;
    document.getElementById('statPending').textContent = data.filter(c => c.category === '待定').length;
    document.getElementById('statReject').textContent = data.filter(c => c.category === '不推荐').length;
}

// ========== 表格渲染 ==========
function renderTable(data) {
    const tbody = document.getElementById('candidateTable');
    if (data.length === 0) {
        tbody.innerHTML = '<tr><td colspan="10" class="px-4 py-8 text-center text-gray-400">暂无数据，请上传简历</td></tr>';
        return;
    }

    tbody.innerHTML = data.map((c, i) => `
        <tr class="hover:bg-gray-50 transition-colors">
            <td class="px-4 py-3 font-medium text-gray-600">${i + 1}</td>
            <td class="px-4 py-3 font-medium text-gray-800">${escapeHtml(c.name)}</td>
            <td class="px-4 py-3 text-sm text-gray-600">${escapeHtml(c.education_level)}</td>
            <td class="px-4 py-3 text-center">${scoreBadge(c.education_score)}</td>
            <td class="px-4 py-3 text-center">${scoreBadge(c.experience_score)}</td>
            <td class="px-4 py-3 text-center">${scoreBadge(c.project_score)}</td>
            <td class="px-4 py-3 text-center">${scoreBadge(c.skill_score)}</td>
            <td class="px-4 py-3 text-center font-bold">${scoreBadge(c.overall_score)}</td>
            <td class="px-4 py-3 text-center">${categoryBadge(c.category)}</td>
            <td class="px-4 py-3 text-center">
                <button onclick="showDetail(${c.id})" class="text-blue-600 hover:text-blue-800 text-sm mr-2">详情</button>
                <button onclick="deleteCandidate(${c.id})" class="text-red-500 hover:text-red-700 text-sm">删除</button>
            </td>
        </tr>
    `).join('');
}

function scoreBadge(score) {
    let color = 'bg-gray-100 text-gray-600';
    if (score >= 85) color = 'bg-green-100 text-green-700';
    else if (score >= 70) color = 'bg-blue-100 text-blue-700';
    else if (score >= 55) color = 'bg-yellow-100 text-yellow-700';
    else color = 'bg-red-100 text-red-700';
    return `<span class="inline-block px-2 py-0.5 rounded text-sm font-medium ${color}">${Number(score).toFixed(1)}</span>`;
}

function categoryBadge(category) {
    const colors = {
        '强烈推荐': 'bg-green-100 text-green-800',
        '推荐': 'bg-blue-100 text-blue-800',
        '待定': 'bg-yellow-100 text-yellow-800',
        '不推荐': 'bg-red-100 text-red-800',
    };
    const color = colors[category] || 'bg-gray-100 text-gray-600';
    return `<span class="inline-block px-2.5 py-0.5 rounded-full text-xs font-medium ${color}">${escapeHtml(category)}</span>`;
}

// ========== 柱状图 ==========
function renderBarChart(data) {
    const ctx = document.getElementById('barChart').getContext('2d');
    if (barChart) barChart.destroy();

    const sorted = [...data].sort((a, b) => b.overall_score - a.overall_score).slice(0, 15);

    barChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: sorted.map(c => c.name),
            datasets: [
                { label: '学历', data: sorted.map(c => c.education_score), backgroundColor: '#60a5fa' },
                { label: '经验', data: sorted.map(c => c.experience_score), backgroundColor: '#34d399' },
                { label: '项目', data: sorted.map(c => c.project_score), backgroundColor: '#fbbf24' },
                { label: '技能', data: sorted.map(c => c.skill_score), backgroundColor: '#a78bfa' },
                { label: '综合', data: sorted.map(c => c.overall_score), backgroundColor: '#f87171' },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { position: 'bottom' } },
            scales: { y: { beginAtZero: true, max: 100 } },
        },
    });
}

// ========== 雷达图选择器 ==========
function renderRadarSelector(data) {
    const container = document.getElementById('radarSelector');
    container.innerHTML = data.slice(0, 10).map(c => `
        <button onclick="toggleRadar(${c.id})"
            class="px-2 py-1 rounded text-xs border ${selectedRadarIds.has(c.id) ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-600 border-gray-300'}">
            ${escapeHtml(c.name)}
        </button>
    `).join('');

    // 默认选前3个
    if (selectedRadarIds.size === 0 && data.length > 0) {
        data.slice(0, Math.min(3, data.length)).forEach(c => selectedRadarIds.add(c.id));
        renderRadarSelector(data);
    }
}

function toggleRadar(id) {
    if (selectedRadarIds.has(id)) {
        selectedRadarIds.delete(id);
    } else if (selectedRadarIds.size < 5) {
        selectedRadarIds.add(id);
    } else {
        showToast('最多同时对比 5 位候选人', 'info');
        return;
    }
    renderRadarSelector(candidatesCache);
    renderRadarChart(candidatesCache);
}

// ========== 雷达图 ==========
function renderRadarChart(data) {
    const ctx = document.getElementById('radarChart').getContext('2d');
    if (radarChart) radarChart.destroy();

    const selected = data.filter(c => selectedRadarIds.has(c.id));
    const colors = ['#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6'];

    radarChart = new Chart(ctx, {
        type: 'radar',
        data: {
            labels: ['学历', '经验', '项目', '技能', '综合'],
            datasets: selected.map((c, i) => ({
                label: c.name,
                data: [c.education_score, c.experience_score, c.project_score, c.skill_score, c.overall_score],
                borderColor: colors[i % colors.length],
                backgroundColor: colors[i % colors.length] + '20',
                pointRadius: 4,
            })),
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: { r: { beginAtZero: true, max: 100 } },
            plugins: { legend: { position: 'bottom' } },
        },
    });
}

// ========== 详情弹窗 ==========
async function showDetail(id) {
    try {
        const resp = await fetch(`/api/candidates/${id}`);
        if (!resp.ok) throw new Error('候选人不存在或加载失败');
        const c = await resp.json();

        document.getElementById('modalTitle').textContent = `${c.name} - 详细评估报告`;

        const detail = c.evaluation_detail || {};
        const details = detail.details || {};

        document.getElementById('modalContent').innerHTML = `
            <div class="space-y-4">
                <div class="grid grid-cols-2 gap-3">
                    <div class="bg-gray-50 rounded p-3">
                        <p class="text-sm text-gray-500">学历层次</p>
                        <p class="font-bold text-lg">${escapeHtml(c.education_level)}</p>
                    </div>
                    <div class="bg-gray-50 rounded p-3">
                        <p class="text-sm text-gray-500">综合分类</p>
                        <p class="font-bold text-lg">${categoryBadge(c.category)}</p>
                    </div>
                </div>
                <div class="grid grid-cols-5 gap-2 text-center">
                    <div class="bg-blue-50 rounded p-2"><p class="text-xs text-gray-500">学历</p><p class="font-bold">${c.education_score}</p></div>
                    <div class="bg-green-50 rounded p-2"><p class="text-xs text-gray-500">经验</p><p class="font-bold">${c.experience_score}</p></div>
                    <div class="bg-yellow-50 rounded p-2"><p class="text-xs text-gray-500">项目</p><p class="font-bold">${c.project_score}</p></div>
                    <div class="bg-purple-50 rounded p-2"><p class="text-xs text-gray-500">技能</p><p class="font-bold">${c.skill_score}</p></div>
                    <div class="bg-red-50 rounded p-2"><p class="text-xs text-gray-500">综合</p><p class="font-bold">${c.overall_score}</p></div>
                </div>
                ${detail.summary ? `<div class="bg-gray-50 rounded p-3"><p class="text-sm text-gray-500">综合评语</p><p class="text-gray-700">${escapeHtml(detail.summary)}</p></div>` : ''}
                ${details.education_analysis ? `<div class="bg-gray-50 rounded p-3"><p class="text-sm text-gray-500">学历分析</p><p class="text-gray-700 text-sm">${escapeHtml(details.education_analysis)}</p></div>` : ''}
                ${details.experience_analysis ? `<div class="bg-gray-50 rounded p-3"><p class="text-sm text-gray-500">工作经验分析</p><p class="text-gray-700 text-sm">${escapeHtml(details.experience_analysis)}</p></div>` : ''}
                ${details.project_analysis ? `<div class="bg-gray-50 rounded p-3"><p class="text-sm text-gray-500">项目经历分析</p><p class="text-gray-700 text-sm">${escapeHtml(details.project_analysis)}</p></div>` : ''}
                ${details.skill_analysis ? `<div class="bg-gray-50 rounded p-3"><p class="text-sm text-gray-500">专业技能分析</p><p class="text-gray-700 text-sm">${escapeHtml(details.skill_analysis)}</p></div>` : ''}
                <details class="bg-gray-50 rounded p-3">
                    <summary class="text-sm text-gray-500 cursor-pointer">查看简历原文</summary>
                    <pre class="mt-2 text-xs text-gray-600 whitespace-pre-wrap max-h-60 overflow-y-auto">${escapeHtml(c.raw_text)}</pre>
                </details>
            </div>
        `;

        document.getElementById('detailModal').classList.remove('hidden');
        document.getElementById('detailModal').classList.add('flex');
    } catch (err) {
        showToast(err.message, 'error');
    }
}

function closeModal() {
    document.getElementById('detailModal').classList.add('hidden');
    document.getElementById('detailModal').classList.remove('flex');
}

// ========== 删除候选人 ==========
async function deleteCandidate(id) {
    if (!confirm('确认删除该候选人？')) return;
    try {
        const resp = await fetch(`/api/candidates/${id}`, { method: 'DELETE' });
        if (!resp.ok) throw new Error('删除失败');
        showToast('删除成功', 'success');
        loadCandidates();
    } catch (err) {
        showToast('删除失败: ' + err.message, 'error');
    }
}

// 点击弹窗外关闭
document.addEventListener('click', (e) => {
    const modal = document.getElementById('detailModal');
    if (e.target === modal) closeModal();
});
