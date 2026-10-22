const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

test('question editor accepts plain questions and preserves explicit gold evidence', () => {
  const { parseQuestions } = require('../../knowledge_service/web/retrieval-experiments.js');
  assert.deepEqual(parseQuestions('退款流程\n申请材料'), [{query:'退款流程'}, {query:'申请材料'}]);
  assert.deepEqual(parseQuestions('[{"query":"退款","expected_record_ids":["fact1"]}]'),
    [{query:'退款', expected_record_ids:['fact1']}]);
  assert.throws(() => parseQuestions('[]'), /问题/);
  assert.throws(() => parseQuestions('[{"expected_answer":"x"}]'), /query/);
});

test('experiment UI uses authenticated API and keeps extraction evaluation available', () => {
  const source = fs.readFileSync(path.join(__dirname, '../../knowledge_service/web/retrieval-experiments.js'), 'utf8');
  assert.match(source, /api\(url/);
  assert.doesNotMatch(source, /fetch\(/);
  assert.match(source, /extraction-evaluation/);
  assert.match(source, /取消实验/);
  assert.match(source, /重试未完成项/);
  assert.match(source, /导出 JSON/);
  assert.match(source, /generation_skip_reason/);
  assert.match(source, /requestSerial/);
});
