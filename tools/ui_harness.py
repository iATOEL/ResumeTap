"""界面自检台：用 mock 桥接驱动真实 ui/ 代码，验证速筛空态与队列交互。

仅用于开发验收，不打包进发布物。启动后访问 http://127.0.0.1:<port>/
"""
import json
import shutil
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tools" / "_harness"

MOCK_JS = """
(function () {
  const now = '2026-10-22T10:00:00';
  const cfg = {
    rarity: { ssr: 82, sr: 66, labels: { SSR: '特级', SR: '高级', R: '普通' } },
    role: 'backend',
    custom_role: { label: '', keywords: [] },
    keywords: [], jd_text: '',
    screening: { min_score: 0, min_years: 0, required_degree: '',
                 must_keywords: [], reject_keywords: [] },
    ui: { theme: 'light', view: 'list', sound: true, animation: true },
    api: { enabled: false, port: 8765 },
    privacy: { store_raw_text: true },
    llm: { enabled: true, base_url: 'http://127.0.0.1:9/v1', api_key: 'k',
           model: 'mock', temperature: 0.2, timeout: 30, auto_start: true },
    config_version: 6,
  };

  function cand(id, name, score, rarity, evalStatus, review) {
    return {
      id, name, filename: name + '.docx', overall_score: score, rarity,
      rarity_label: rarity, status: 'pass', role: 'backend',
      years: 5, degree: '本科', school: '浙江大学', school_tier: 'c9',
      school_tier_label: 'C9', major: '计算机',
      summary: '整体匹配度高', news_flash: '整体匹配度高',
      created_at: now, review_verdict: review || '', review_note: '', reviewed_at: '',
      eval_status: evalStatus, eval_error: '', has_raw_text: true,
      detected_role_label: '后端开发',
      dimensions: [
        { key: 'skill', label: '技能匹配', score: 88, weight: 0, detail: '技能栈完整', available: true },
        { key: 'experience', label: '经验年限', score: 80, weight: 0, detail: '5 年经验', available: true },
        { key: 'education', label: '教育背景', score: 82, weight: 0, detail: 'C9 本科', available: true },
        { key: 'project', label: '项目经历', score: 70, weight: 0, detail: '项目有量化', available: true },
        { key: 'job_match', label: '岗位匹配', score: 85, weight: 0, detail: '契合', available: true },
      ],
      strengths: ['技术栈完整', '项目有量化成果'],
      weaknesses: ['缺少团队管理经验'],
      screen_reasons: [],
      // 与 v3.2.0 后端的轻量行契约一致：列表不带 payload，只带展示小字段
      score_source: 'ai', ai_model: 'mock',
      contact: { phone: '13812345678', email: 'lisi@example.com' },
      education_entries: [
        { period: '2013 - 2017', school: '浙江大学', tier: 'c9', tier_label: 'C9',
          degree: '本科', major: '计算机', raw: '浙江大学 计算机 本科' },
      ],
      skills_text: 'Java、Spring Boot、MySQL、Redis、Docker',
      payload: {
        score_source: 'ai', ai_model: 'mock', contact: { phone: '13812345678', email: 'lisi@example.com' },
        sections: { skills: 'Java、Spring Boot、MySQL、Redis、Docker', education: '教育背景',
                    experience: '工作经历', projects: '项目经历' },
        strengths: ['技术栈完整'], weaknesses: ['缺少团队管理经验'],
        dimensions: [], screen_reasons: [], news_flash: '整体匹配度高',
        projects: [], skill_hits: [],
        profile: { education_entries: [
          { period: '2013 - 2017', school: '浙江大学', tier: 'c9', tier_label: 'C9',
            degree: '本科', major: '计算机', raw: '浙江大学 计算机 本科' },
        ] },
      },
    };
  }

  const state = {
    candidates: [],
    review: { total: 0, pending: 0, liked: 0, passed: 0, skipped: 0, progress: 0 },
    eval: { total: 0, pending: 0, running: 0, done: 0, error: 0, progress: 0 },
  };

  function summarize() {
    const total = state.candidates.length;
    const done = state.candidates.filter((c) => c.eval_status === 'done').length;
    const reviewed = state.candidates.filter((c) => c.review_verdict).length;
    state.eval = {
      total, done,
      pending: state.candidates.filter((c) => c.eval_status === 'pending').length,
      running: 0,
      error: state.candidates.filter((c) => c.eval_status === 'error').length,
      progress: total ? Math.round(done / total * 1000) / 10 : 0,
    };
    state.review = {
      total, pending: total - reviewed,
      liked: state.candidates.filter((c) => c.review_verdict === 'liked').length,
      passed: state.candidates.filter((c) => c.review_verdict === 'passed').length,
      skipped: state.candidates.filter((c) => c.review_verdict === 'skipped').length,
      progress: total ? Math.round(reviewed / total * 1000) / 10 : 0,
    };
  }

  function stats() {
    const done = state.candidates.filter((c) => c.eval_status === 'done');
    const scores = done.map((c) => c.overall_score);
    return {
      total: state.candidates.length, evaluated: done.length, eval: state.eval,
      rarity_counts: { R: 0, SR: 0, SSR: done.length },
      status_counts: { pass: done.length, flagged: 0, rejected: 0 },
      review_counts: { '': state.review.pending, liked: state.review.liked,
                       passed: state.review.passed, skipped: state.review.skipped },
      reviewed: state.candidates.length - state.review.pending,
      review_progress: state.review.progress,
      avg_score: scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : 0,
      max_score: scores.length ? Math.max(...scores) : 0,
      avg_years: 5, pass_rate: 100, ssr_rate: 100,
      best: done.length ? { name: done[0].name, score: done[0].overall_score, rarity: done[0].rarity } : null,
    };
  }

  const roles = [
    { key: 'backend', label: '后端开发' }, { key: 'frontend', label: '前端开发' },
    { key: 'data', label: '数据分析' }, { key: 'general', label: '通用岗位' },
  ];
  const groups = [{ label: '技术研发', keys: ['backend', 'frontend', 'data'] }];

  function ok(extra) { return Object.assign({ ok: true }, extra || {}); }

  const api = {
    bootstrap() {
      summarize();
      return ok({
        config: cfg, stats: stats(), achievements: {},
        roles, role_groups: groups,
        meta: { version: '3.1.1', data_dir: 'D:/data', app_dir: 'D:/app',
                portable: true, config_path: 'D:/data/config.yaml',
                log_path: 'D:/data/logs/app.log', api: { running: false, port: 8765, token: 'x' },
                has_candidates: state.candidates.length > 0 },
        review: state.review, eval: state.eval,
        queue: { state: 'idle', current: null, finished: 0, failed: 0, last_error: '',
                 pending: state.eval.pending, errored: state.eval.error, running: false,
                 eval: state.eval },
        candidates: state.candidates,
      });
    },
    list_candidates() { summarize(); return ok({ candidates: state.candidates, stats: stats(), review: state.review }); },
    queue_status() {
      summarize();
      return ok({ queue: { state: 'idle', current: null, finished: 0, failed: 0, last_error: '',
                           pending: state.eval.pending, errored: state.eval.error, running: false,
                           eval: state.eval },
                  eval: state.eval, candidates: state.candidates, stats: stats() });
    },
    ai_status() {
      return ok({ ready: true, enabled: true, has_key: true, model: 'mock',
                  base_url: 'http://127.0.0.1:9/v1', reason: '',
                  queue: { state: 'idle', current: null, finished: 0, failed: 0,
                           last_error: '', pending: state.eval.pending,
                           errored: state.eval.error, running: false, eval: state.eval },
                  eval: state.eval });
    },
    get_candidate_detail(id) {
      const c = state.candidates.find((x) => x.id === Number(id));
      if (!c) return { ok: false, error: '不存在' };
      return ok({ candidate: Object.assign({}, c, { raw_text: '简历原文示例 第二行' }) });
    },
    get_candidate(id) {
      const c = state.candidates.find((x) => x.id === Number(id));
      if (!c) return { ok: false, error: '不存在' };
      return ok({ candidate: Object.assign({}, c, { raw_text: '原文', stored_path: 'D:/a.docx' }) });
    },
    review_candidate(id, verdict) {
      const c = state.candidates.find((x) => x.id === Number(id));
      if (c) c.review_verdict = verdict;
      summarize();
      return ok({ review: state.review, stats: stats(), candidates: state.candidates });
    },
    reset_reviews() {
      state.candidates.forEach((c) => { c.review_verdict = ''; });
      summarize();
      return ok({ review: state.review, candidates: state.candidates });
    },
    save_config(next) {
      Object.assign(cfg, next);
      return ok({ config: cfg, touched: 0, candidates: state.candidates,
                  stats: stats(), eval: state.eval,
                  queue: { state: 'idle', current: null, finished: 0, failed: 0,
                           last_error: '', pending: state.eval.pending,
                           errored: state.eval.error, running: false, eval: state.eval } });
    },
    reset_config() { return ok({ config: cfg, touched: 0, candidates: state.candidates,
                                 stats: stats(), eval: state.eval, queue: { state: 'idle' } }); },
    queue_start() { return ok({ queue: { state: 'running', current: null, finished: 0,
                                         failed: 0, last_error: '', pending: state.eval.pending,
                                         errored: state.eval.error, running: true, eval: state.eval },
                                eval: state.eval }); },
    queue_pause() { return ok({ queue: { state: 'paused', current: null, finished: 0,
                                         failed: 0, last_error: '', pending: state.eval.pending,
                                         errored: state.eval.error, running: false, eval: state.eval },
                                eval: state.eval }); },
    queue_retry_failed() { return ok({ queued: 0, queue: { state: 'idle' }, eval: state.eval,
                                       candidates: state.candidates }); },
    queue_requeue_all() { return ok({ queued: state.candidates.length, queue: { state: 'idle' },
                                      eval: state.eval, candidates: state.candidates }); },
    ai_evaluate_one() { return ok({ candidate: null, candidates: state.candidates,
                                    stats: stats(), eval: state.eval }); },
    ai_test_connection() { return ok({ message: '连接正常' }); },
    pick_files() { return ok({ paths: [] }); },
    pick_folder() { return ok({ path: null }); },
    scan_folder() { return ok({ paths: [], total: 0 }); },
    import_files() { return ok({ imported: [], duplicates: [], failures: [], total: 0,
                                 stats: stats(), candidates: state.candidates,
                                 review: state.review, eval: state.eval, queue: { state: 'idle' } }); },
    import_bytes() { return ok({ imported: [], duplicates: [], failures: [], total: 0 }); },
    load_samples() { return ok({ paths: [], total: 0 }); },
    delete_candidates() { return ok({ removed: 0, candidates: state.candidates, stats: stats() }); },
    clear_all() { state.candidates = []; summarize(); return ok({ candidates: [], stats: stats() }); },
    export_results() { return ok({ path: 'D:/exports/x.csv', count: 1, format: 'csv' }); },
    export_to_folder() { return ok({ path: 'D:/exports/x.csv', count: 1, format: 'csv' }); },
    open_data_dir() { return ok({ path: 'D:/data' }); },
    open_path() { return ok(); },
    show_in_folder() { return ok(); },
    api_status() { return ok({ api: { running: false, port: 8765, token: 'x', base_url: '' } }); },
    api_start() { return ok({ api: { running: true, port: 8765, token: 'x', base_url: 'http://127.0.0.1:8765' } }); },
    api_stop() { return ok({ api: { running: false, port: 8765, token: 'x', base_url: '' } }); },
    api_regenerate_token() { return ok({ api: { running: false, port: 8765, token: 'new', base_url: '' } }); },
    rescore_all() { return ok({ queued: state.candidates.length, candidates: state.candidates, stats: stats() }); },
  };

  // 测试场景切换
  window.__harness = {
    scenario(name) {
      if (name === 'empty') {
        state.candidates = [];
      } else if (name === 'unevaluated') {
        state.candidates = [cand(1, '待评甲', 0, 'R', 'pending'), cand(2, '待评乙', 0, 'R', 'pending')];
      } else if (name === 'erroronly') {
        const a = cand(1, '失败甲', 0, 'R', 'error'); a.eval_error = 'API Key 无效';
        state.candidates = [a];
      } else if (name === 'reviewed') {
        state.candidates = [cand(1, '已评甲', 90, 'SSR', 'done', 'liked'),
                            cand(2, '已评乙', 70, 'SR', 'done', 'passed')];
      } else if (name === 'mixed') {
        state.candidates = [cand(1, '张三', 91.2, 'SSR', 'done'),
                            cand(2, '李四', 72.5, 'SR', 'done'),
                            cand(3, '王五', 0, 'R', 'pending')];
      }
      summarize();
      return state.candidates.length;
    },
  };

  window.pywebview = { api: api };
  window.dispatchEvent(new Event('pywebviewready'));
})();
"""


def build_harness() -> None:
    if HARNESS.exists():
        shutil.rmtree(HARNESS)
    (HARNESS / "js" / "mock").mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "ui" / "css", HARNESS / "css")
    shutil.copytree(ROOT / "ui" / "js", HARNESS / "js", dirs_exist_ok=True)
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    mock_tag = '<script src="js/mock/bridge.js"></script>\n<script src="js/charts.js"></script>'
    html = html.replace('<script src="js/charts.js"></script>', mock_tag, 1)
    (HARNESS / "index.html").write_text(html, encoding="utf-8")
    (HARNESS / "js" / "mock" / "bridge.js").write_text(MOCK_JS, encoding="utf-8")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HARNESS), **kwargs)

    def log_message(self, *args):
        pass


def main() -> int:
    build_harness()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_port
    print(json.dumps({"url": f"http://127.0.0.1:{port}/", "port": port}), flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
