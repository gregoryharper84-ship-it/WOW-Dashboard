'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {assess,EXPECTED} = require('../wow_v17_factory_status.cjs');
const head = 'a'.repeat(40);
const merged = 'b'.repeat(40);
function check(role, overrides={}) {
  const spec=EXPECTED[role];
  return {id:role==='QA'?10:20,name:spec.name,app:{id:spec.appId},head_sha:head,status:'completed',conclusion:'success',completed_at:'2026-10-28T12:00:00Z',...overrides};
}
function pr(overrides={}) {return {number:2217,head:{sha:head},state:'open',draft:false,merged_at:null,merge_commit_sha:null,...overrides};}
test('live exact-head QA and Release passes do NOT become deployment evidence',()=>{
  const out=assess(pr(),[check('QA'),check('RELEASE')]);
  assert.equal(out.repository_governance,'QA_RELEASE_CHECKS_PASS_ONLY');
  assert.equal(out.fixed_and_verified,false);
  assert.equal(out.production_status,'UNVERIFIED');
  assert.equal(out.can_execute,false);
  assert.ok(out.missing_proofs.includes('AUTHORIZED_PROTECTED_MERGE_UNVERIFIED'));
});
test('a merged PR still cannot claim Render, lease or scoring acceptance',()=>{
  const out=assess(pr({state:'closed',merged_at:'2026-10-28T13:00:00Z',merge_commit_sha:merged}),[check('QA'),check('RELEASE')]);
  assert.equal(out.pr_state,'MERGED');
  assert.equal(out.merged_sha,merged);
  assert.equal(out.fixed_and_verified,false);
  assert.ok(out.missing_proofs.includes('WRITER_FENCE_AND_LEASE_RELEASE_UNVERIFIED'));
});
test('checks with a spoofed App ID or wrong source head never pass',()=>{
  const out=assess(pr(),[check('QA',{app:{id:999}}),check('RELEASE',{head_sha:merged})]);
  assert.equal(out.exact_head_checks.qa.state,'UNVERIFIED');
  assert.equal(out.exact_head_checks.qa.reason,'WRONG_APP_OR_HEAD');
  assert.equal(out.exact_head_checks.release.state,'UNVERIFIED');
});
test('newest check status wins and pending or failed do not count as green',()=>{
  const out=assess(pr(),[check('QA'),check('QA',{id:30,status:'in_progress',conclusion:null,completed_at:null,started_at:'2026-10-28T13:00:00Z'}),check('RELEASE',{conclusion:'failure'})]);
  assert.equal(out.exact_head_checks.qa.state,'PENDING');
  assert.equal(out.exact_head_checks.release.state,'FAIL');
  assert.equal(out.repository_governance,'UNVERIFIED_OR_BLOCKED');
});
test('no missing field can silently become green',()=>{
  const out=assess(pr({state:'closed'}),[]);
  assert.equal(out.pr_state,'CLOSED_UNMERGED');
  assert.equal(out.exact_head_checks.qa.state,'UNVERIFIED');
  assert.equal(out.exact_head_checks.release.state,'UNVERIFIED');
  assert.throws(()=>assess(pr({head:{sha:'bad'}}),[]),/exact PR head/);
});
test('a stale successful check cannot override a fresh failure',()=>{
  const out=assess(pr(),[check('QA',{id:10,completed_at:'2026-10-28T12:00:00Z'}),check('QA',{id:11,conclusion:'failure',completed_at:'2026-10-28T13:00:00Z'}),check('RELEASE')]);
  assert.equal(out.exact_head_checks.qa.state,'FAIL');
});
