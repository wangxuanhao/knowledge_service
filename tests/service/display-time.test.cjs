const test=require('node:test');
const assert=require('node:assert/strict');
const {format}=require('../../knowledge_service/web/display-time.js');

test('timestamps use Beijing calendar date and 24-hour time with seconds',()=>{
  assert.equal(format('2026-11-02T06:30:24.524765Z'),'2026年11月02日 14:30:24');
  assert.equal(format('2026-11-01T20:00:05+04:00'),'2026年11月02日 00:00:05');
  assert.equal(format('2026-11-02 09:45:06+0800'),'2026年11月02日 09:45:06');
  assert.equal(format(0),'1970年01月01日 08:00:00');
});

test('calendar dates keep their precision and invalid values use the supplied fallback',()=>{
  assert.equal(format('2024-02-29'),'2024年02月29日');
  for(const value of [null,undefined,'',{},'2026-02-29','2026-04-31T09:00:00Z','2026-11-02T06:30:24','not a date',NaN]){
    assert.equal(format(value,'未知'),'未知');
  }
});
