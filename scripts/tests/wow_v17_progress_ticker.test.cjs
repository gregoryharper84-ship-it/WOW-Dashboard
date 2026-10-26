'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {buildReport,replaceBlock,pullState,START,END,LANES} = require('../wow_v17_progress_ticker.cjs');

const now = new Date('2026-10-23T23:00:00Z');
function fixture() {
  const issues = new Map(), prs = new Map();
  for (const lane of LANES) {
    for (const n of lane.issues) issues.set(n,{number:n,state:'open',closed_at:null});
    for (const n of lane.prs) prs.set(n,{number:n,state:'open',merged_at:null,draft:false});
  }
  return {issues,prs};
}
test('open issue and PR are never silently counted as complete', () => {
  const {issues,prs}=fixture(), result=buildReport(issues,prs,now);
  assert.match(result,/0\/2 linked tickets closed/);
  assert.match(result,/0\/2 linked PRs merged/);
  assert.match(result,/UNVERIFIED/);
  assert.match(result,/No new closed issues or merged PRs/);
});
test('real closed issue and merged PR count as engineering-only completions', () => {
  const {issues,prs}=fixture();
  issues.set(1841,{number:1841,state:'closed',closed_at:'2026-10-23T20:00:00Z'});
  prs.set(1974,{number:1974,state:'closed',merged_at:'2026-10-22T20:00:00Z',draft:false});
  const result=buildReport(issues,prs,now);
  assert.match(result,/1\/2 linked tickets closed/);
  assert.match(result,/1\/2 linked PRs merged/);
  assert.match(result,/issue \[#1841\]/);
  assert.match(result,/PR \[#1974\]/);
  assert.match(result,/Production stays UNVERIFIED/);
});
test('closed unmerged PR is not a completion; draft stays draft',()=>{
  assert.equal(pullState({state:'closed',merged_at:null}), 'CLOSED_UNMERGED');
  assert.equal(pullState({state:'open',draft:true,merged_at:null}), 'DRAFT');
  assert.equal(pullState({state:'closed',merged_at:'2026-10-23T19:00:00Z'}), 'MERGED');
});
test('never overwrite outer issue content',()=>{
  const body='owner start\n'+START+'\nold\n'+END+'\nowner end';
  assert.equal(replaceBlock(body,'new'), 'owner start\n'+START+'\nnew\n'+END+'\nowner end');
});
test('fail closed on missing or duplicate markers',()=>{
  for (const body of ['no markers',END+START,START+END+START+END]) {
    assert.throws(()=>replaceBlock(body,'new'),/markers/);
  }
});
test('fail closed on any missing issue/PR; never substitute false green',()=>{
  const {issues,prs}=fixture();
  issues.delete(1841);
  assert.throws(()=>buildReport(issues,prs,now),/Missing issue state/);
  const ok=fixture();
  ok.prs.delete(1974);
  assert.throws(()=>buildReport(ok.issues,ok.prs,now),/Missing PR state/);
});
test('old closures do not appear as fresh ticker events',()=>{
  const {issues,prs}=fixture();
  issues.set(1841,{number:1841,state:'closed',closed_at:'2026-09-01T00:00:00Z'});
  const result=buildReport(issues,prs,now);
  assert.match(result,/1\/2 linked tickets closed/);
  assert.doesNotMatch(result,/UTC — NFL: issue/);
});
