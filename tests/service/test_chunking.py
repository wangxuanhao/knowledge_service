import pytest
from knowledge_service.chunking import split_document
from knowledge_service.models import Ingest


@pytest.mark.parametrize('strategy', ['fixed','paragraph','structural'])
def test_offsets_coverage_and_limits(strategy):
    text=('## 标题\n第一条 规则正文。\n\n'+'中文内容'*40+'\n')*12
    rows=split_document(text,dict(chunk_strategy=strategy,chunk_size=180,chunk_overlap=20))
    assert rows[0]['start_char']==0
    assert rows[-1]['end_char']==len(text)
    for i,row in enumerate(rows):
        assert row['text']==text[row['start_char']:row['end_char']]
        assert 0<len(row['text'])<=180
        if i:
            assert row['start_char'] in ([rows[i-1]['end_char'],rows[i-1]['end_char']-20] if strategy=='structural' else [rows[i-1]['end_char']-20])


def test_boundary_selection_and_validation():
    text='a'*110+'。'+'b'*110
    assert split_document(text,dict(chunk_strategy='paragraph',chunk_size=180,chunk_overlap=20))[0]['end_char']==111
    text='a'*110+'\n## 新标题\n'+'b'*110
    assert split_document(text,dict(chunk_strategy='structural',chunk_size=180,chunk_overlap=20))[0]['end_char']==111
    with pytest.raises(ValueError): Ingest(title='x',text='a',chunk_size=100,chunk_overlap=100)
    assert len(split_document('a'*3500,{}))==3

def test_short_chinese_clauses_split_without_breaking_nested_list():
    text='# 美团侵权投诉须知\n\n一、本须知适用范围。\n二、提供材料：\n1、身份证明；\n2、权属证明；\n三、处理流程。'
    rows=split_document(text,dict(chunk_strategy='structural'))
    assert len(rows)==4
    assert rows[1]['text'].startswith('一、')
    assert '1、身份证明' in rows[2]['text'] and '2、权属证明' in rows[2]['text']
    assert ''.join(r['text'] for r in rows)==text

@pytest.mark.parametrize('heading',['第一条','**第二条**','（一）','一、','1、','## 标题'])
def test_short_structure_recognition(heading):
    text='前言\n'+heading+' 内容\n'
    rows=split_document(text,dict(chunk_strategy='structural'))
    assert len(rows)==2
    assert rows[1]['text'].startswith(heading)
