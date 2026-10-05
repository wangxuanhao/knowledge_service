"""单进程后台任务，带持久化收据，而非分布式队列。

2026-10-05：新增**发布-订阅**能力 —— 进度除了写入持久化 logs，还会实时推给
订阅者（供 SSE 端点 /jobs/{id}/stream 使用）。替代此前前端每 2 秒轮询的笨办法。
"""
from concurrent.futures import ThreadPoolExecutor
import queue as queue_lib
import threading
import time
import sys
import traceback
from pathlib import Path
from uuid import uuid4
from ..core.time import utc_now
from ..utils.diagnostics import redact


def concise(value,limit=1200):
    text=redact(value)
    return text if len(text)<=limit else text[:limit]+'…（详细信息已保存在文档收据）'


class Jobs:
    def __init__(self,repository,heartbeat_seconds=30):
        self.repo=repository
        self.lock=threading.RLock()
        self.heartbeat_seconds=heartbeat_seconds
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='knowledge-job')
        # 发布-订阅：job_id -> [Queue,...]；跨线程传递进度事件
        self._subscribers={}
        for job in self.repo.list_artifacts('job'):
            if job['status'] in ('queued','running'):
                job.update(status='interrupted',updated_at=utc_now(),error='服务在完成前重启；重试前请检查收据')
                self.repo.save_artifact('job',job)

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
        self.executor.shutdown(wait=True)
