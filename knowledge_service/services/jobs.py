"""Persisted tasks with PostgreSQL leases and bounded local consumers.

Application jobs use registered handlers and durable inputs. The callable
submit/_run seam remains for old in-process diagnostic consumers only.
SSE is local to an instance; persisted receipts remain the shared truth.
"""
from concurrent.futures import ThreadPoolExecutor
import queue as queue_lib
import threading
import time
import sys
import traceback
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from ..core.time import utc_now
from ..utils.diagnostics import redact


def concise(value,limit=1200):
    text=redact(value)
    return text if len(text)<=limit else text[:limit]+'…（详细信息已保存在文档收据）'


class Jobs:
    def __init__(self,repository,heartbeat_seconds=30, *, handlers=None, workers=2):
        self.repo=repository
        self.lock=threading.RLock()
        self.heartbeat_seconds=heartbeat_seconds
        self.executor=ThreadPoolExecutor(max_workers=workers,thread_name_prefix='knowledge-job')
        self.handlers = dict(handlers or {})
        self.owner = str(uuid4())
        self.stopped = threading.Event()
        self.workers = []
        self.worker_count = workers
        # 发布-订阅：job_id -> [Queue,...]；跨线程传递进度事件
        self._subscribers={}
        for job in self.repo.list_artifacts('job'):
            if job['status'] in ('queued','running') and 'payload' not in job:
                job.update(status='interrupted',updated_at=utc_now(),error='服务在完成前重启；重试前请检查收据')
                self.repo.save_artifact('job',job)
    def start(self):
        with self.lock:
            if self.workers or not self.handlers or self.stopped.is_set():
                return
            for index in range(self.worker_count):
                worker = threading.Thread(target=self._consume,
                    name=f'knowledge-durable-{index}', daemon=True)
                worker.start()
                self.workers.append(worker)

    def enqueue(self, kind, payload, project_id=None):
        if kind not in self.handlers:
            raise ValueError('任务类型没有注册持久处理器')
        job = dict(id=str(uuid4()), kind=kind, project_id=project_id,
                   payload=payload, status='queued', progress=0, logs=['已排队'],
                   created_at=utc_now(), attempts=0)
        self.repo.save_artifact('job', job)
        self.start()
        return job

    def control(self, job_id, action):
        job = self.repo.get_artifact('job', job_id)
        if action != 'cancel' and job.get('kind') not in self.handlers:
            raise ValueError('旧任务缺少持久参数，需重新提交')
        return self.repo.control_task('job', job_id, action)

    def _consume(self):
        while not self.stopped.is_set():
            if getattr(self.repo, 'closed', False):
                return
            try:
                task = self.repo.claim_task('job', self.owner, lease_seconds=180)
                if task is not None:
                    self._run_durable(task)
                    continue
            except Exception:
                if getattr(self.repo, 'closed', False):
                    return
                # A transient DB failure must not terminate a queue consumer.
                traceback.print_exc()
            self.stopped.wait(1)

    def _run_durable(self, task):
        started = time.monotonic()
        heartbeat_stop = threading.Event()
        lost = threading.Event()

        def persist(updates):
            saved = self.repo.update_task('job', task, updates)
            if saved is None:
                lost.set()
                raise RuntimeError('任务已取消或租约已失效')
            task.update(saved)

        def progress(stage, percent=None):
            if lost.is_set() or self.stopped.is_set():
                raise RuntimeError('任务已取消或执行器已关闭')
            task['logs'].append(f'[{utc_now()}] {redact(stage)}')
            persist({'stage': redact(stage), 'stage_started_at': utc_now(),
                     'logs': task['logs'][-2000:],
                     'progress': percent if percent is not None else task['progress'],
                     'elapsed_seconds': round(time.monotonic() - started, 1)})
            self._publish(task['id'], 'progress', {
                'stage': redact(stage), 'percent': task['progress']})

        def heartbeat():
            while not heartbeat_stop.wait(min(self.heartbeat_seconds, 30)):
                if time.monotonic() - started > 3600:
                    self.repo.update_task('job', task,
                        {'status': 'failed', 'error': '任务执行超过 3600 秒，需重试',
                         'error_type': 'TaskTimeout'})
                    lost.set()
                    return
                lease = (datetime.now(timezone.utc) + timedelta(seconds=180)).isoformat(
                    timespec='microseconds').replace('+00:00', 'Z')
                if self.repo.update_task('job', task,
                        {'lease_until': lease, 'heartbeat_at': utc_now()}) is None:
                    lost.set()
                    return

        monitor = threading.Thread(target=heartbeat, daemon=True)
        monitor.start()
        try:
            progress('开始执行', 5)
            fn = self.handlers[task['kind']]
            completion = queue_lib.Queue(maxsize=1)

            def execute():
                try:
                    value = fn(task['payload'], task.get('project_id'), progress, task['id'])
                    completion.put((True, value))
                except BaseException as exc:
                    completion.put((False, exc))

            # Blocking integrations cannot monopolize a consumer slot forever.
            # Cancellation is cooperative at their next progress checkpoint.
            threading.Thread(target=execute, daemon=True,
                             name='knowledge-task-' + task['id']).start()
            while True:
                if lost.is_set() or self.stopped.is_set():
                    return
                try:
                    success, result = completion.get(timeout=1)
                    break
                except queue_lib.Empty:
                    current = self.repo.get_artifact('job', task['id'])
                    if (current['status'] != 'running'
                            or current.get('lease_token') != task['lease_token']):
                        lost.set()
                        return
            if not success:
                raise result
            if lost.is_set():
                return
            persist({'status': 'completed', 'result': result, 'progress': 100,
                     'completed_at': utc_now()})
            self._publish(task['id'], 'completed', {'status': 'completed', 'progress': 100})
        except Exception as exc:
            if not lost.is_set():
                self.repo.update_task('job', task, {
                    'status': 'failed', 'error': concise(exc),
                    'error_type': type(exc).__name__, 'failed_stage': task.get('stage')})
                self._publish(task['id'], 'failed', {'status': 'failed', 'error': concise(exc)})
        finally:
            heartbeat_stop.set()

    # ------------------------------------------------------------- 发布-订阅
    def subscribe(self,job_id):
        """订阅某个任务的实时事件，返回一个线程安全队列。"""
        q=queue_lib.Queue(maxsize=2000)
        with self.lock:
            self._subscribers.setdefault(job_id,[]).append(q)
        return q

    def unsubscribe(self,job_id,q):
        """取消订阅（客户端断开时务必调用，避免泄漏）。"""
        with self.lock:
            subs=self._subscribers.get(job_id,[])
            if q in subs:subs.remove(q)
            if not subs:self._subscribers.pop(job_id,None)

    def _publish(self,job_id,event_type,payload):
        """把一个事件广播给该任务的所有订阅者；队列满则跳过（不拖慢任务）。"""
        with self.lock:
            subs=list(self._subscribers.get(job_id,[]))
        message={'event':event_type,**payload}
        for q in subs:
            try:
                q.put_nowait(message)
            except queue_lib.Full:
                pass

    def submit(self,kind,fn,project_id=None):
        with self.lock:
            pending=[j for j in self.repo.list_artifacts('job') if j['status'] in ('queued','running')]
            if len(pending)>=100: raise RuntimeError('任务队列已满')
            job=dict(id=str(uuid4()),kind=kind,project_id=project_id,status='queued',progress=0,logs=['已排队'],created_at=utc_now())
            self.repo.save_artifact('job',job)
            self.executor.submit(self._run,job,fn)
            return dict(job)

    def _run(self,job,fn):
        started=time.monotonic()
        worker_id=threading.get_ident()
        stopped=threading.Event()
        job_id=job['id']
        def append(message):
            line=f'[{utc_now()}] +{time.monotonic()-started:.1f}s {redact(message)}'
            job['logs'].append(line)
            job['logs']=job['logs'][-2000:]
            print(f"[task:{job_id}] {line}",flush=True)
        def progress(stage,percent=None):
            with self.lock:
                append(stage)
                job.update(updated_at=utc_now(),stage=redact(stage),stage_started_at=utc_now(),elapsed_seconds=round(time.monotonic()-started,1))
                if percent is not None: job['progress']=percent
                self.repo.save_artifact('job',job)
            # 实时推给 SSE 订阅者（在锁外，避免阻塞任务线程）
            self._publish(job_id,'progress',{
                'stage':redact(stage),'percent':percent if percent is not None else job['progress'],
                'elapsed_seconds':round(time.monotonic()-started,1)})
        def heartbeat():
            while not stopped.wait(self.heartbeat_seconds):
                frame=sys._current_frames().get(worker_id)
                locations=[] if frame is None else [f'{Path(f.filename).name}:{f.lineno}:{f.name}' for f in traceback.extract_stack(frame)[-8:]]
                del frame
                with self.lock:
                    if job['status']!='running':return
                    job.update(heartbeat_at=utc_now(),elapsed_seconds=round(time.monotonic()-started,1),worker_stack=locations)
                    append('仍在等待当前步骤（非完成进度） · '+job.get('stage','')+' · 执行位置 '+ ' → '.join(locations))
                    self.repo.save_artifact('job',job)
        monitor=threading.Thread(target=heartbeat,name='knowledge-job-heartbeat',daemon=True)
        try:
            job['status']='running';progress('开始执行',5)
            monitor.start()
            result=fn(progress)
            with self.lock:job.update(status='completed',result=result)
            progress('处理完成',100)
            # 通知订阅者任务已完成（轻量信号；完整结果前端自行查询，避免大对象/序列化问题）
            self._publish(job_id,'completed',{
                'status':'completed','progress':100,'updated_at':utc_now()})
        except Exception as exc:
            with self.lock:
                job.update(status='failed',error=concise(exc),failed_stage=job.get('stage'),error_type=type(exc).__name__,
                           failure_stack=[f'{Path(f.filename).name}:{f.lineno}:{f.name}' for f in traceback.extract_tb(exc.__traceback__)])
            progress('处理失败 · '+type(exc).__name__+' · '+concise(exc))
            self._publish(job_id,'failed',{
                'status':'failed','error':concise(exc),'error_type':type(exc).__name__,
                'updated_at':utc_now()})
        finally:
            stopped.set()

    def close(self):
        self.stopped.set()
        self.executor.shutdown(wait=False, cancel_futures=True)
        for worker in self.workers:
            worker.join(timeout=1)
