from copy import deepcopy
import threading

from knowledge_service.diagnostics import reporting, stage, event
from knowledge_service.jobs import Jobs


class Repo:
    def __init__(self):
        self.saved={}
        self.heartbeat=threading.Event()
    def list_artifacts(self, kind):return []
    def save_artifact(self,kind,job):
        self.saved=deepcopy(job)
        if job.get('heartbeat_at'):self.heartbeat.set()


def run_task(fn, interval=30):
    repo=Repo();jobs=Jobs(repo,heartbeat_seconds=interval)
    job=dict(id='test',status='queued',progress=0,logs=[])
    jobs._run(job,lambda report:fn(report,repo))
    jobs.close()
    return repo.saved


def test_stages_redacted_and_persisted(monkeypatch):
    monkeypatch.setenv('KG_LLM_API_KEY','test-private-key')
    def task(report,repo):
        with reporting(report),stage('片段 1/2',20):
            event('LLM 实体抽取 test-private-key')
        return {'count':2}
    job=run_task(task)
    assert job['status']=='completed' and job['progress']==100
    text='\n'.join(job['logs'])
    assert '片段 1/2 · 开始' in text and '片段 1/2 · 完成' in text
    assert 'test-private-key' not in text and '<REDACTED>' in text
    assert job['elapsed_seconds']>=0


def test_failure_keeps_stage_and_actual_progress():
    def task(report,repo):
        with reporting(report),stage('LLM 关系抽取',35):
            raise TimeoutError('request timed out')
    job=run_task(task)
    assert job['status']=='failed' and job['progress']==35
    assert 'LLM 关系抽取' in job['failed_stage']
    assert job['error_type']=='TimeoutError' and job['failure_stack']


def test_wait_heartbeat_reports_stack_without_claiming_progress():
    def task(report,repo):
        report('等待模型',25)
        assert repo.heartbeat.wait(3)
        assert repo.saved['progress']==25
        assert repo.saved['stage']=='等待模型'
        assert repo.saved['worker_stack']
    job=run_task(task,.02)
    assert any('仍在等待当前步骤' in line for line in job['logs'])
