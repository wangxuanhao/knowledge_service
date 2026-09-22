"""单进程后台任务，带持久化收据，而非分布式队列。"""
from concurrent.futures import ThreadPoolExecutor
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
        for job in self.repo.list_artifacts('job'):
            if job['status'] in ('queued','running'):
                job.update(status='interrupted',updated_at=utc_now(),error='服务在完成前重启；重试前请检查收据')
                self.repo.save_artifact('job',job)

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
        def append(message):
            line=f'[{utc_now()}] +{time.monotonic()-started:.1f}s {redact(message)}'
            job['logs'].append(line)
            job['logs']=job['logs'][-2000:]
            print(f"[task:{job['id']}] {line}",flush=True)
        def progress(stage,percent=None):
            with self.lock:
                append(stage)
                job.update(updated_at=utc_now(),stage=redact(stage),stage_started_at=utc_now(),elapsed_seconds=round(time.monotonic()-started,1))
                if percent is not None: job['progress']=percent
                self.repo.save_artifact('job',job)
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
        except Exception as exc:
            with self.lock:
                job.update(status='failed',error=concise(exc),failed_stage=job.get('stage'),error_type=type(exc).__name__,
                           failure_stack=[f'{Path(f.filename).name}:{f.lineno}:{f.name}' for f in traceback.extract_tb(exc.__traceback__)])
            progress('处理失败 · '+type(exc).__name__+' · '+concise(exc))
        finally:
            stopped.set()

    def close(self):
        self.executor.shutdown(wait=True)