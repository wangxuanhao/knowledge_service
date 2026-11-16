"""Project mapped source spans onto attachment-specific recognition locations."""


def focus_source_location(location, *, spans=None, boxes=None, segments=None):
    spans=location.get('spans',[]) if spans is None else spans
    previous=location.get('media_locations',[])
    if boxes is None:boxes=[b for group in previous for b in group.get('boxes',[])] if previous else location.get('boxes',[])
    if segments is None:segments=[s for group in previous for s in group.get('segments',[])] if previous else location.get('segments',[])
    ocr={box['id']:box for box in boxes};asr={segment['id']:segment for segment in segments}
    groups={}
    for span in spans:
        speech=bool(span.get('asr_segment_id'))
        row=(asr if speech else ocr).get(span.get('asr_segment_id' if speech else 'ocr_block_id'))
        if row is None:continue
        kind='video_asr' if speech else 'image_ocr'
        run_key='asr_run_id' if speech else 'ocr_run_id'
        key=(kind,row['asset_id'],row[run_key])
        group=groups.setdefault(key,dict(source_kind=kind,asset_id=row['asset_id'],boxes=[],segments=[],
            ocr_run_id=None,asr_run_id=None,asr_segment_id=None,start_ms=None,end_ms=None))
        group[run_key]=row[run_key]
        rows=group['segments' if speech else 'boxes']
        if not any(existing['id']==row['id'] for existing in rows):rows.append(row)
    for group in groups.values():
        if group['segments']:
            group.update(start_ms=min(s['start_ms'] for s in group['segments']),
                end_ms=max(s['end_ms'] for s in group['segments']),asr_segment_id=group['segments'][0]['id'])
    result={**location,'spans':spans,'media_locations':list(groups.values()),
        'block_id':next((s['block_id'] for s in spans if s.get('block_id')),None),
        'asset_id':None,'boxes':[],'segments':[],'ocr_run_id':None,'asr_run_id':None,
        'asr_segment_id':None,'start_ms':None,'end_ms':None}
    if len(groups)==1:
        # Backward-compatible single-media fields must share one asset/run.
        result.update(next(iter(groups.values())))
    return result
