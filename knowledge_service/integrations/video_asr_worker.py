"""Private offline worker. Importing this module never imports model runtimes."""
import json
import os
from pathlib import Path
import socket
import sys


def _deny_network(*args, **kwargs):
    raise RuntimeError('Network is disabled in local ASR workers')


def main(request_path):
    os.environ.update(HF_HUB_OFFLINE='1', MODELSCOPE_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
    socket.socket.connect = _deny_network
    socket.socket.connect_ex = _deny_network
    socket.create_connection = _deny_network
    socket.getaddrinfo = _deny_network
    request = json.loads(Path(request_path).read_text(encoding='utf-8'))
    result_path = Path(request['result'])
    try:
        from importlib.metadata import version
        import numpy as np
        import soundfile as sf
        from funasr import AutoModel
        from funasr.utils.postprocess_utils import rich_transcription_postprocess
        common = dict(device='cpu', ncpu=2, disable_update=True, disable_pbar=True, trust_remote_code=False, log_level='ERROR')
        audio, rate = sf.read(request['audio'], dtype='float32')
        vad = AutoModel(model=request['vad_root'], max_single_segment_time=30000, **common)
        intervals = vad.generate(input=audio, fs=rate, is_final=True)
        boundaries = intervals[0].get('value', []) if intervals else []
        if not boundaries:
            raise ValueError('asr_no_speech')
        model = AutoModel(model=request['model_root'], **common)
        segments = []
        duration_ms = int(len(audio)/rate*1000)
        for start, end in boundaries:
            start, end = max(0, int(start)), min(duration_ms, int(end))
            if end <= start:
                continue
            sample = np.ascontiguousarray(audio[int(start*rate/1000):int(end*rate/1000)])
            raw = model.generate(input=sample, fs=rate, language=request['language'], use_itn=True,
                                 ban_emo_unk=True, batch_size_s=30)
            text = rich_transcription_postprocess(raw[0].get('text', '')) if raw else ''
            normalized = ' '.join(text.split())
            if normalized:
                segments.append(dict(start_ms=start, end_ms=end, text=text, normalized_text=normalized))
        output = dict(engine_version=version('funasr'), segments=segments) if segments else dict(error_code='asr_no_speech')
    except Exception as exc:
        output = dict(error_code='asr_no_speech' if str(exc) == 'asr_no_speech' else 'asr_runtime_error')
    result_path.write_text(json.dumps(output, ensure_ascii=False), encoding='utf-8')
    return 1 if output.get('error_code') else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
