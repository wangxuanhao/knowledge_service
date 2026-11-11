"""Bounded local video recognition in one short-lived, offline model process."""
from contextlib import contextmanager
from importlib.metadata import version, PackageNotFoundError
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from pydantic import BaseModel, Field, model_validator


MAX_VIDEO_BYTES = 25_000_000
MAX_VIDEO_DURATION_SECONDS = 600
_ASR_SEMAPHORE = threading.BoundedSemaphore(1)


class ASRError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class ASRSegment(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1)
    normalized_text: str = Field(min_length=1)

    @model_validator(mode='after')
    def valid_interval(self):
        if self.end_ms <= self.start_ms or not self.normalized_text.strip() or not self.text.strip():
            raise ValueError('Invalid speech interval or empty text')
        return self


def _media_command(args, timeout=30):
    try:
        return subprocess.run(args, check=True, capture_output=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise ASRError('asr_engine_unavailable', '本地 FFmpeg / ffprobe 不可用') from exc
    except subprocess.TimeoutExpired as exc:
        raise ASRError('asr_timeout', '视频处理超时') from exc
    except subprocess.CalledProcessError as exc:
        raise ASRError('asr_invalid_media', '视频文件无法解析') from exc


def inspect_video(data, mime_type=None, *, max_bytes=MAX_VIDEO_BYTES, max_duration=MAX_VIDEO_DURATION_SECONDS):
    """Probe actual bytes without trusting a filename or allowing network protocols."""
    if not data or len(data) > max_bytes:
        raise ASRError('asr_file_too_large', '视频为空或超过 25 MB 上限')
    with tempfile.TemporaryDirectory(prefix='knowledge-video-probe-') as directory:
        path = Path(directory)/'source.media'
        path.write_bytes(data)
        output = _media_command(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                                 '-show_format', '-show_streams', '-of', 'json', str(path)])
    try:
        probe = json.loads(output.stdout)
        streams = probe.get('streams', [])
        video = next(stream for stream in streams if stream.get('codec_type') == 'video'
                     and not stream.get('disposition', {}).get('attached_pic'))
        formats = set(probe['format']['format_name'].split(','))
        if 'mp4' in formats or 'mov' in formats:
            detected = 'video/mp4'
            video_codecs, audio_codecs = {'h264'}, {'aac'}
        elif 'webm' in formats and video.get('codec_name') in {'vp8', 'vp9', 'av1'}:
            detected = 'video/webm'
            video_codecs, audio_codecs = {'vp8', 'vp9', 'av1'}, {'opus', 'vorbis'}
        else:
            raise ValueError('Unsupported video container')
        audio = [stream for stream in streams if stream.get('codec_type') == 'audio']
        if (video.get('codec_name') not in video_codecs or any(stream.get('codec_name') not in audio_codecs for stream in audio)
                or probe['format'].get('tags',{}).get('major_brand','').strip()=='qt'):
            raise ASRError('asr_unsupported_codec', '视频编码不受支持，请使用 H.264/AAC MP4 或 VP8/VP9/AV1 + Opus/Vorbis WebM')
        duration = float(probe['format'].get('duration', video.get('duration', 0)))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('Unknown video duration')
        if duration > max_duration:
            raise ASRError('asr_duration_exceeded', f'视频超过 {max_duration:g} 秒时长上限')
        if mime_type and mime_type not in {detected, 'application/octet-stream'}:
            raise ValueError('Video MIME mismatch')
        return dict(mime_type=detected, width=int(video['width']), height=int(video['height']),
                    duration_ms=math.ceil(duration*1000), has_audio=any(s.get('codec_type') == 'audio' for s in streams))
    except ASRError:
        raise
    except (ValueError, KeyError, StopIteration, TypeError) as exc:
        raise ASRError('asr_invalid_media', '仅支持可读取的 MP4 / WebM 视频') from exc


@contextmanager
def _machine_lock(timeout):
    """OS releases the byte lock if an application process exits unexpectedly."""
    path = Path(tempfile.gettempdir())/'knowledge-service-video-asr.lock'
    with path.open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0'); handle.flush()
        deadline = time.monotonic()+timeout
        while True:
            try:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise ASRError('asr_busy', '视频识别任务繁忙，请稍后重试') from exc
                time.sleep(.2)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class FunASRAdapter:
    engine = 'sensevoice-fsmn'

    def __init__(self, model_root=None, vad_root=None, timeout=None):
        self.model_root = Path(model_root or os.getenv('KG_VIDEO_ASR_MODEL_ROOT', 'data/model/asr/SenseVoiceSmall')).expanduser().resolve()
        self.vad_root = Path(vad_root or os.getenv('KG_VIDEO_ASR_VAD_ROOT', 'data/model/asr/speech_fsmn_vad_zh-cn-16k-common-pytorch')).expanduser().resolve()
        self.timeout = float(timeout if timeout is not None else os.getenv('KG_VIDEO_ASR_TIMEOUT_SECONDS', '600'))
        self.max_duration = int(os.getenv('KG_VIDEO_ASR_MAX_DURATION_SECONDS', '600'))
        self.language = os.getenv('KG_VIDEO_ASR_LANGUAGE', 'zh')

    @property
    def configuration_fingerprint(self):
        identities = []
        for root in (self.model_root, self.vad_root):
            for name in ('model.pt', 'config.yaml', 'configuration.json', 'am.mvn'):
                if not (root/name).is_file() or (root/name).stat().st_size == 0:
                    raise ASRError('asr_engine_unavailable', '视频识别所需的本地 SenseVoice / FSMN-VAD 模型缺失')
            files = []
            for path in sorted(root.iterdir()):
                if path.is_file() and not path.name.startswith('.'):
                    stat = path.stat()
                    files.append((path.name, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
            identities.append((str(root), files))
        try:
            runtime = version('funasr')
        except PackageNotFoundError as exc:
            raise ASRError('asr_engine_unavailable', '本地 FunASR 运行时不可用') from exc
        if self.timeout <= 0 or not 0 < self.max_duration <= 600 or self.language not in {'zh', 'en', 'auto', 'yue', 'ja', 'ko'}:
            raise ASRError('asr_engine_unavailable', '视频识别运行参数无效')
        config = dict(models=identities, runtime=runtime, device='cpu', ncpu=2, sample_rate=16000,
                      channels=1, vad_max_segment_ms=30000, language=self.language, use_itn=True,
                      ban_emo_unk=True, batch_size_s=30, disable_update=True, disable_pbar=True,
                      trust_remote_code=False, preprocess_version='ffmpeg-pcm16-mono-v1', max_duration=self.max_duration)
        return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()

    def available(self):
        try:
            self.configuration_fingerprint
            return bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))
        except (ASRError, OSError, ValueError):
            return False

    def recognize(self, data):
        fingerprint = self.configuration_fingerprint
        details = inspect_video(data, max_duration=self.max_duration)
        if not details['has_audio']:
            raise ASRError('asr_no_audio', '视频没有音轨，无法转写')
        if not _ASR_SEMAPHORE.acquire(timeout=self.timeout):
            raise ASRError('asr_busy', '视频识别任务繁忙，请稍后重试')
        try:
            with _machine_lock(self.timeout), tempfile.TemporaryDirectory(prefix='knowledge-video-asr-') as directory:
                root = Path(directory)
                source, audio, request, result = (root/name for name in ('source.media', 'audio.wav', 'request.json', 'result.json'))
                source.write_bytes(data)
                _media_command(['ffmpeg', '-v', 'error', '-nostdin', '-protocol_whitelist', 'file,pipe',
                                '-i', str(source), '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '16000',
                                '-t', str(self.max_duration), '-c:a', 'pcm_s16le', '-y', str(audio)], timeout=60)
                request.write_text(json.dumps(dict(audio=str(audio), result=str(result), model_root=str(self.model_root),
                    vad_root=str(self.vad_root), language=self.language, configuration_fingerprint=fingerprint)), encoding='utf-8')
                env = dict(os.environ, HF_HUB_OFFLINE='1', MODELSCOPE_OFFLINE='1', TOKENIZERS_PARALLELISM='false',
                           OMP_NUM_THREADS='2', MKL_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2')
                try:
                    # FunASR emits arbitrary progress text. Only the private JSON file is a protocol.
                    with (root/'worker.log').open('wb') as log:
                        completed = subprocess.run([sys.executable, '-m', 'knowledge_service.integrations.video_asr_worker', str(request)],
                            env=env, stdout=log, stderr=subprocess.STDOUT, timeout=self.timeout, check=False,
                            cwd=str(Path(__file__).resolve().parents[2]))
                except subprocess.TimeoutExpired as exc:
                    raise ASRError('asr_timeout', '视频语音转写超时，请稍后重试') from exc
                if not result.is_file():
                    raise ASRError('asr_runtime_error', '本地视频识别进程未返回结果')
                try:
                    output = json.loads(result.read_text(encoding='utf-8'))
                    if output.get('error_code'):
                        code = output['error_code'] if output['error_code'] in {'asr_no_speech', 'asr_engine_unavailable'} else 'asr_runtime_error'
                        raise ASRError(code, '视频中没有可识别语音' if code == 'asr_no_speech' else '本地视频识别失败')
                    segments = [ASRSegment.model_validate(item).model_dump() for item in output['segments']]
                    if completed.returncode or not segments or any(s['end_ms'] > details['duration_ms'] for s in segments):
                        raise ValueError('Invalid worker result')
                    return dict(engine_version=output['engine_version'], duration_ms=details['duration_ms'], segments=segments,
                                normalized_text='\n'.join(s['normalized_text'] for s in segments), configuration_fingerprint=fingerprint)
                except ASRError:
                    raise
                except (ValueError, KeyError, TypeError) as exc:
                    raise ASRError('asr_runtime_error', '视频识别返回无效结果') from exc
        finally:
            _ASR_SEMAPHORE.release()
