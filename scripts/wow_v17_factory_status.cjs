'use strict';
/**
 * Read-only WOW factory status probe. GitHub evidence can establish CI and
 * repository-governance progress but NEVER production/model/trading readiness.
 * This script is not a release controller, CI gate, lease holder or publisher.
 */
const https = require('node:https');
const REPO = 'gregoryharper84-ship-it/WOW-Dashboard';
const EXPECTED = Object.freeze({
  QA: {name:'WOW Independent QA exact-head', appId:5245832},
  RELEASE: {name:'WOW Release Authority exact-head', appId:5245853},
});
const SHA = /^[a-f0-9]{40}$/;

function selectedCheck(checkRuns, head, spec) {
  const names = checkRuns.filter(run => run && run.name === spec.name);
  const valid = names.filter(run => run.head_sha === head && Number(run.app && run.app.id) === spec.appId)
    .sort((a,b) => (Date.parse(b.completed_at || b.started_at || b.created_at || 0) || 0) -
      (Date.parse(a.completed_at || a.started_at || a.created_at || 0) || 0) || (b.id || 0) - (a.id || 0));
  if (!valid.length) return {
    state:'UNVERIFIED', reason:names.length ? 'WRONG_APP_OR_HEAD' : 'CHECK_MISSING',
    expected_app_id:spec.appId, check_name:spec.name
  };
  const r = valid[0];
  const state = r.status !== 'completed' ? 'PENDING' : r.conclusion === 'success' ? 'PASS' : 'FAIL';
  return {state, reason:state === 'FAIL' ? (r.conclusion || 'NO_CONCLUSION') : null,
    check_name:spec.name, app_id:spec.appId, head_sha:r.head_sha,
    check_run_id:r.id, url:r.html_url || null, conclusion:r.conclusion || null};
}
function assess(pr, checkRuns, queriedAt = new Date().toISOString()) {
  if (!pr || typeof pr !== 'object' || !Array.isArray(checkRuns)) throw new Error('Malformed GitHub evidence');
  const head = pr.head && pr.head.sha;
  if (!SHA.test(head || '')) throw new Error('Missing or invalid exact PR head');
  const qa = selectedCheck(checkRuns, head, EXPECTED.QA);
  const release = selectedCheck(checkRuns, head, EXPECTED.RELEASE);
  const githubState = pr.merged_at && SHA.test(pr.merge_commit_sha || '') ? 'MERGED'
    : pr.state === 'closed' ? 'CLOSED_UNMERGED'
    : pr.state === 'open' && pr.draft ? 'DRAFT'
    : pr.state === 'open' ? 'OPEN' : 'UNKNOWN';
  const needs = [];
  if (qa.state !== 'PASS') needs.push('QA_EXACT_HEAD_' + qa.state);
  if (release.state !== 'PASS') needs.push('RELEASE_EXACT_HEAD_' + release.state);
  if (githubState !== 'MERGED') needs.push('AUTHORIZED_PROTECTED_MERGE_UNVERIFIED');
  // GitHub cannot establish production SHA, writer-fence negative canary,
  // specialist health, or a real scoring/reducer receipt. Never infer those.
  needs.push('RENDER_WEB_AND_WORKER_SHA_UNVERIFIED');
  needs.push('WRITER_FENCE_AND_LEASE_RELEASE_UNVERIFIED');
  needs.push('PRODUCTION_ACCEPTANCE_RECEIPTS_UNVERIFIED');
  return {
    schema:'WOW_V17_FACTORY_STATUS_READ_ONLY_V1',
    source:'LIVE_GITHUB_READ_ONLY',
    queried_at:queriedAt,
    repository:REPO,
    pr_number:pr.number,
    head_sha:head,
    pr_state:githubState,
    merged_sha:githubState === 'MERGED' ? pr.merge_commit_sha : null,
    exact_head_checks:{qa,release},
    repository_governance:qa.state === 'PASS' && release.state === 'PASS' ? 'QA_RELEASE_CHECKS_PASS_ONLY' : 'UNVERIFIED_OR_BLOCKED',
    backend_runtime:'NOT_EVALUATED',
    model_capability:'NOT_EVALUATED',
    live_gpt_editor_sync:'NOT_EVALUATED',
    production_status:'UNVERIFIED',
    fixed_and_verified:false,
    missing_proofs:needs,
    can_execute:false,
    dry_run_only:true,
  };
}
function api(path) {
  return new Promise((resolve,reject)=>{
    const headers = {
      'Accept':'application/vnd.github+json',
      'X-GitHub-Api-Version':'2022-11-28',
      'User-Agent':'wow-v17-factory-status-readonly'
    };
    if (process.env.GITHUB_TOKEN) headers.Authorization = 'Bearer ' + process.env.GITHUB_TOKEN;
    const req = https.get({hostname:'api.github.com',path,headers,timeout:15000},res=>{
      let data='';
      res.on('data',chunk=>{data+=chunk; if(data.length>2*1024*1024) req.destroy(new Error('Response too large'));});
      res.on('end',()=>{
        if(res.statusCode!==200) return reject(new Error('GitHub GET status '+res.statusCode));
        try { resolve(JSON.parse(data)); } catch(e) {reject(new Error('GitHub JSON invalid'));}
      });
    });
    req.on('error',reject);
    req.on('timeout',()=>req.destroy(new Error('GitHub GET timeout')));
  });
}
async function main() {
  const number = Number(process.argv[2]);
  if (!Number.isSafeInteger(number) || number <= 0) throw new Error('Usage: node wow_v17_factory_status.cjs <PR_NUMBER>');
  const pr = await api('/repos/'+REPO+'/pulls/'+number);
  if (pr.number !== number || !SHA.test((pr.head || {}).sha || '')) throw new Error('PR identity invalid');
  let runs=[];
  for(let page=1;page<=3;page++) {
    const response = await api('/repos/'+REPO+'/commits/'+pr.head.sha+'/check-runs?per_page=100&page='+page);
    if(!Array.isArray(response.check_runs)) throw new Error('Check run response malformed');
    runs = runs.concat(response.check_runs);
    if(response.check_runs.length<100) break;
    if(page===3 && response.check_runs.length===100) throw new Error('Check run inventory truncated');
  }
  process.stdout.write(JSON.stringify(assess(pr,runs),null,2)+'\n');
}
if(require.main===module) main().catch(err=>{console.error('Read-only status unavailable:',err.message);process.exitCode=1;});
module.exports={assess,selectedCheck,EXPECTED};
