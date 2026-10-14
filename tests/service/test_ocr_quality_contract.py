"""OCR output contracts reject invalid evidence and configure quality gates."""
import pytest


def test_invalid_ocr_blocks_rejected():
    from knowledge_service.integrations.image_ocr import OcrBlock
    from pydantic import ValidationError
    valid=dict(text='rule',normalized_text='rule',confidence=.9,x=.1,y=.1,width=.4,height=.2)
    for patch in ({'confidence':float('nan')},{'text':''},{'normalized_text':' '},{'x':-.1},{'width':0},{'x':.9,'width':.4}):
        with pytest.raises(ValidationError): OcrBlock.model_validate(dict(valid,**patch))


def test_ocr_quality_policy_is_environment_configured(monkeypatch):
    from knowledge_service.services.image_ocr import ImageOCRService
    monkeypatch.setenv('KG_IMAGE_OCR_MIN_CONFIDENCE','.7')
    monkeypatch.setenv('KG_IMAGE_OCR_MIN_AVERAGE_CONFIDENCE','.8')
    monkeypatch.setenv('KG_IMAGE_OCR_MAX_LOW_CONFIDENCE_RATIO','.1')
    monkeypatch.setenv('KG_IMAGE_OCR_MIN_TEXT_CHARS','8')
    service=ImageOCRService(None,None,adapter=object())
    assert service.quality_policy==dict(min_confidence=.7,min_average_confidence=.8,max_low_confidence_ratio=.1,min_text_chars=8)


def test_quality_rejects_many_low_blocks_and_short_text():
    from knowledge_service.services.image_ocr import ImageOCRService
    from knowledge_service.integrations.image_ocr import OCRError
    service=ImageOCRService(None,None,adapter=object())
    def block(text,confidence): return dict(text=text,normalized_text=text,confidence=confidence,x=0,y=0,width=.5,height=.5,page_or_frame=1)
    with pytest.raises(OCRError,match='quality'):
        service.quality_check({'blocks':[block('long rule text',.99),block('noise',.59)]})
    with pytest.raises(OCRError,match='quality'): service.quality_check({'blocks':[block('a',.99)]})
    accepted,average=service.quality_check({'blocks':[block('安全规则',.95)]})
    assert average==.95 and len(accepted)==1


def test_preprocess_fingerprint_captures_limits():
    from knowledge_service.integrations.image_ocr import RapidOCRAdapter
    assert RapidOCRAdapter(max_side=1000).preprocess_fingerprint != RapidOCRAdapter(max_side=2000).preprocess_fingerprint
    assert RapidOCRAdapter(max_pixels=1000).preprocess_fingerprint != RapidOCRAdapter(max_pixels=2000).preprocess_fingerprint


def test_quality_fingerprint_changes_with_each_threshold():
    from knowledge_service.services.image_ocr import ImageOCRService
    baseline=ImageOCRService(None,None,adapter=object()).fingerprint('adapter')
    for options in ({'min_confidence':.7},{'min_average_confidence':.8},{'max_low_confidence_ratio':.1},{'min_text_chars':8}):
        assert ImageOCRService(None,None,adapter=object(),**options).fingerprint('adapter')!=baseline


def test_adapter_rejects_invalid_engine_evidence():
    import io
    from types import SimpleNamespace
    from PIL import Image
    from knowledge_service.integrations.image_ocr import RapidOCRAdapter,OCRError
    image=Image.new('RGB',(100,100),'white'); stream=io.BytesIO();image.save(stream,format='PNG')
    for box,text,score in (([[0,0],[100,0],[100,50],[0,50]],'rule',float('nan')),([[-1,0],[50,0],[50,50],[0,50]],'rule',.9),([[0,0],[50,0],[50,50],[0,50]],'',.9)):
        adapter=RapidOCRAdapter();adapter._engine=lambda pixels:SimpleNamespace(boxes=[box],txts=[text],scores=[score])
        with pytest.raises(OCRError): adapter.recognize(stream.getvalue())
    unsupported=io.BytesIO();image.save(unsupported,format='PPM')
    with pytest.raises(OCRError,match='Unsupported source image format'): adapter.recognize(unsupported.getvalue())
