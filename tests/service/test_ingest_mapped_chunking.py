from knowledge_service.integrations.markdown_parser import parse_markdown
from knowledge_service.services.chunking import split_source_blocks, split_mapped_chunks


def test_fixed_windows_cross_blocks_with_overlap_and_exact_mapping():
    parsed = parse_markdown('a' * 70 + '\n\n' + 'b' * 70, 'source')
    rows, canonical = split_source_blocks(parsed['blocks'], dict(chunk_size=100, chunk_overlap=20, chunk_strategy='fixed'), return_text=True)
    assert canonical == 'a' * 70 + '\n\n' + 'b' * 70
    assert len(canonical) == 142
    assert [row['start_char'] for row in rows] == [0, 80]
    assert all(canonical[row['start_char']:row['end_char']] == row['text'] for row in rows)
    assert [len(row['text']) for row in rows] == [100, 62]
    assert rows[0]['text'][-20:] == rows[1]['text'][:20]
    assert len(rows[0]['source_spans']) == 2
    for row in rows:
        for span in row['source_spans']:
            block = next(b for b in parsed['blocks'] if b['block_id'] == span['block_id'])
            assert row['text'][span['chunk_start_char']:span['chunk_end_char']] == block['normalized_text'][span['block_start_char']:span['block_end_char']]


def test_paragraph_and_structural_settings_change_boundaries():
    parsed = parse_markdown('a' * 70 + '\n\n' + 'b' * 70 + '\n\n# Next\n\n' + 'c' * 70, 'source')
    fixed = split_source_blocks(parsed['blocks'], dict(chunk_size=100, chunk_overlap=0, chunk_strategy='fixed'))
    paragraph = split_source_blocks(parsed['blocks'], dict(chunk_size=100, chunk_overlap=0, chunk_strategy='paragraph'))
    structural = split_source_blocks(parsed['blocks'], dict(chunk_size=100, chunk_overlap=0, chunk_strategy='structural'))
    assert fixed[0]['text'].endswith('b' * 28)
    assert paragraph[0]['text'] == 'a' * 70 + '\n\n'
    assert any(row['text'].startswith('# Next') for row in structural)


def test_table_chunks_keep_repeated_header_and_original_rows():
    parsed = parse_markdown('| Name | Value |\n| --- | --- |\n' + '\n'.join('| item%d | %s |' % (i, 'v' * 30) for i in range(5)), 'source')
    rows = split_source_blocks(parsed['blocks'], dict(chunk_size=100, chunk_overlap=20, chunk_strategy='fixed'))
    assert len(rows) > 1
    assert all(row['text'].startswith('Name | Value\n') for row in rows)
    assert [span['payload']['table_start_row'] for row in rows for span in row['source_spans'] if not span['payload'].get('table_header')] == [0, 2, 4]
    for row in rows:
        for span in row['source_spans']:
            assert row['text'][span['chunk_start_char']:span['chunk_end_char']] == parsed['blocks'][0]['normalized_text'][span['block_start_char']:span['block_end_char']]


def test_ocr_overlap_retains_box_identity_and_clips_character_ranges():
    original = dict(text='x' * 160, metadata=dict(source_kind='image_ocr', ocr_block_ids=['box'], ocr_run_id='run'),
                    source_spans=[dict(ocr_block_id='box', chunk_start_char=0, chunk_end_char=160, block_start_char=0, block_end_char=160,
                                       payload=dict(asset_id='asset', ocr_run_id='run', bbox=[1, 2, 3, 4]))])
    rows = split_mapped_chunks([original], dict(chunk_size=100, chunk_overlap=20, chunk_strategy='fixed'))
    assert [row['source_spans'][0]['block_start_char'] for row in rows] == [0, 80]
    assert [row['source_spans'][0]['block_end_char'] for row in rows] == [100, 160]
    assert all(row['metadata']['ocr_block_ids'] == ['box'] for row in rows)
    assert all(row['source_spans'][0]['payload']['bbox'] == [1, 2, 3, 4] for row in rows)


def test_structural_heading_metadata_tracks_each_window():
    parsed = parse_markdown('# A\n\n' + 'a' * 70 + '\n\n# B\n\n' + 'b' * 70, 'source')
    rows = split_source_blocks(parsed['blocks'], dict(chunk_size=100, chunk_overlap=0, chunk_strategy='structural'))
    assert [row['metadata']['heading_path'] for row in rows] == [['A'], ['B']]


def test_canonical_table_header_and_ocr_text_are_not_repeated():
    parsed = parse_markdown('| Name | Value |\n| --- | --- |\n' + '\n'.join('| item%d | %s |' % (i, 'v' * 30) for i in range(5)), 'source')
    rows, text = split_source_blocks(parsed['blocks'], dict(chunk_size=100, chunk_overlap=20), return_text=True)
    assert text == parsed['blocks'][0]['normalized_text']
    assert text.count('Name | Value') == 1
    assert rows[1]['start_char'] > rows[0]['start_char']
    ocr_rows, ocr_text = split_mapped_chunks([dict(text='x' * 160)], dict(chunk_size=100, chunk_overlap=20), return_text=True)
    assert len(ocr_text) == 160
    assert [row['start_char'] for row in ocr_rows] == [0, 80]
