"""
app.py - Flask 后端服务
启动方式：p·ython app.py
浏览器访问：http://localhost:5000
"""

import os
import uuid
import json
import threading
import logging
from datetime import datetime

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler

from scraper import scrape
from exporter import export_csv, export_excel, export_html

# ──────────────────────────────────────────────
# 初始化
# ──────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
os.makedirs(DATA_DIR, exist_ok=True)

# 任务状态存储（内存）
tasks: dict = {}

# 定时调度器
scheduler = BackgroundScheduler(daemon=True)
scheduler.start()


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────

def make_task(params: dict) -> tuple[str, dict]:
    """创建任务对象，返回 (task_id, task)"""
    task_id = str(uuid.uuid4())[:8]
    task = {
        'id':         task_id,
        'status':     'pending',      # pending / running / done / error / stopped
        'message':    '任务已创建，等待执行...',
        'logs':       [],
        'results':    [],
        'error':      None,
        'params':     params,
        'stop_event': threading.Event(),
        'created_at': datetime.now().isoformat(),
        'finished_at': None,
    }
    tasks[task_id] = task
    return task_id, task


def run_task(task_id: str, params: dict):
    """在后台线程中执行爬取任务"""
    task = tasks[task_id]
    task['status'] = 'running'

    def log(msg: str):
        task['message'] = msg
        task['logs'].append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        # 只保留最近 200 条日志
        if len(task['logs']) > 200:
            task['logs'] = task['logs'][-200:]

    try:
        start = datetime.strptime(params['start_date'], '%Y-%m-%d')
        end   = datetime.strptime(params['end_date'],   '%Y-%m-%d')

        results = scrape(
            keywords         = params.get('keywords', []),
            sites            = params.get('sites', []),
            start_date       = start,
            end_date         = end,
            max_per_site     = int(params.get('max_per_site', 20)),
            progress_callback= log,
            stop_event       = task['stop_event'],
            seed_urls        = params.get('seed_urls', []) or [],
            task_id          = task_id,
        )

        task['results']     = results
        task['status']      = 'done'
        task['finished_at'] = datetime.now().isoformat()
        task['message']     = f'完成，共抓取 {len(results)} 条数据'

        # 持久化到文件
        save_path = os.path.join(DATA_DIR, f'{task_id}.json')
        with open(save_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.exception(f'Task {task_id} failed')
        task['status']  = 'error'
        task['error']   = str(e)
        task['message'] = f'错误: {e}'


def load_results(task_id: str) -> list:
    """优先从内存取结果，兜底从文件加载"""
    task = tasks.get(task_id)
    if task and task.get('results'):
        return task['results']
    path = os.path.join(DATA_DIR, f'{task_id}.json')
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    return []


# ──────────────────────────────────────────────
# 路由
# ──────────────────────────────────────────────

@app.route('/')
def index():
    return app.send_static_file('index.html')


@app.route('/api/search', methods=['POST'])
def api_search():
    """启动一个新的爬取任务"""
    params = request.get_json(force=True)
    if not params.get('keywords'):
        return jsonify({'error': '请至少填写一个关键词'}), 400

    # 兼容前端传入字符串/数组
    if isinstance(params.get('seed_urls'), str):
        params['seed_urls'] = [u.strip() for u in params['seed_urls'].splitlines() if u.strip()]

    task_id, _ = make_task(params)
    thread = threading.Thread(target=run_task, args=(task_id, params), daemon=True)
    thread.start()
    return jsonify({'task_id': task_id})


@app.route('/api/status/<task_id>')
def api_status(task_id: str):
    """轮询任务状态"""
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404
    return jsonify({
        'status':   task['status'],
        'message':  task['message'],
        'logs':     task['logs'][-30:],
        'count':    len(task.get('results', [])),
    })


@app.route('/api/results/<task_id>')
def api_results(task_id: str):
    """获取任务结果（完整列表）"""
    results = load_results(task_id)
    if results is None:
        return jsonify({'error': '任务不存在或无结果'}), 404
    return jsonify(results)


@app.route('/api/stop/<task_id>', methods=['POST'])
def api_stop(task_id: str):
    """中止运行中的任务"""
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404
    task['stop_event'].set()
    task['status']  = 'stopped'
    task['message'] = '任务已手动停止'
    return jsonify({'ok': True})


@app.route('/api/export/<task_id>/<fmt>')
def api_export(task_id: str, fmt: str):
    """下载导出文件（csv / excel / html）"""
    results = load_results(task_id)
    if not results:
        return jsonify({'error': '无可导出的数据'}), 404

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    fname_map = {
        'csv':   f'舆情数据_{ts}.csv',
        'excel': f'舆情数据_{ts}.xlsx',
        'html':  f'舆情报告_{ts}.html',
    }
    if fmt not in fname_map:
        return jsonify({'error': '不支持的格式'}), 400

    out_path = os.path.join(DATA_DIR, fname_map[fmt])

    if fmt == 'csv':
        export_csv(results, out_path)
    elif fmt == 'excel':
        export_excel(results, out_path)
    elif fmt == 'html':
        export_html(results, out_path)

    return send_file(out_path, as_attachment=True, download_name=fname_map[fmt])


# ──────────────────────────────────────────────
# 定时任务接口
# ──────────────────────────────────────────────

@app.route('/api/schedule', methods=['GET'])
def api_list_schedules():
    """列出所有定时任务"""
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            'id':       job.id,
            'name':     job.name,
            'next_run': str(job.next_run_time) if job.next_run_time else None,
            'trigger':  str(job.trigger),
        })
    return jsonify(jobs)


@app.route('/api/schedule', methods=['POST'])
def api_add_schedule():
    """添加定时爬取任务"""
    data = request.get_json(force=True)
    params         = data.get('params', {})
    interval_hours = float(data.get('interval_hours', 24))
    job_name       = data.get('name') or f"定时监控_{datetime.now().strftime('%m%d_%H%M')}"

    def scheduled_run():
        tid, _ = make_task(params)
        run_task(tid, params)
        logger.info(f'定时任务 [{job_name}] 执行完成，task_id={tid}')

    job = scheduler.add_job(
        scheduled_run,
        trigger='interval',
        hours=interval_hours,
        id=str(uuid.uuid4())[:8],
        name=job_name,
        replace_existing=False,
        max_instances=1,
    )
    return jsonify({'job_id': job.id, 'name': job.name, 'interval_hours': interval_hours})


@app.route('/api/schedule/<job_id>', methods=['DELETE'])
def api_delete_schedule(job_id: str):
    """删除定时任务"""
    try:
        scheduler.remove_job(job_id)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 404


# ──────────────────────────────────────────────
# 启动
# ──────────────────────────────────────────────

if __name__ == '__main__':
    print('=' * 50)
    print('  舆情监控工具 启动中...')
    print('  请在浏览器访问: http://localhost:5000')
    print('=' * 50)
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
