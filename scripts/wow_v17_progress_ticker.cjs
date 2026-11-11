'use strict';
// Observability only: reads existing GitHub evidence and updates issue #2020.
// No model scoring, wagers, releases, PR merges, approvals, or other writes.
const https = require('node:https');

const REPO = 'gregoryharper84-ship-it/WOW-Dashboard';
const DASHBOARD_ISSUE = 2020;
const START = '<!-- WOW_PROGRESS_AUTO_START -->';
const END = '<!-- WOW_PROGRESS_AUTO_END -->';
const LANES = [
  { name: 'NFL', priority: 'P0 — highest', issues: [1841, 1942], prs: [1974, 1842] },
  { name: 'NBA', priority: 'P0 — highest', issues: [1950, 1988], prs: [1953, 1960] },
  { name: 'NHL', priority: 'P1 — next', issues: [1952, 1349], prs: [1957, 1959] },
  { name: 'NCAAF + NCAAB', priority: 'P1 — concurrent', issues: [1951, 1644], prs: [1645, 1648] },
  { name: 'Other sports + MLB care', priority: 'P2 — bounded', issues: [664, 1649, 1650, 1802], prs: [] },
];

function replaceBlock(body, report) {
  const start = body.indexOf(START), end = body.indexOf(END);
  if (start < 0 || end <= start || body.indexOf(START, start + START.length) >= 0 || body.indexOf(END, end + END.length) >= 0) {
    throw new Error('Dashboard markers absent, duplicated, or out of order; refusing to replace issue body');
  }
  return body.slice(0, start + START.length) + '\n' + report + '\n' + body.slice(end);
}

function pullState(pr) {
  if (pr.merged_at) return 'MERGED';
  if (pr.state === 'open') return pr.draft ? 'DRAFT' : 'OPEN';
  return 'CLOSED_UNMERGED';
}

function buildReport(issues, prs, now = new Date()) {
  const recent = [], rows = [], ageMs = 7 * 24 * 60 * 60 * 1000;
  for (const lane of LANES) {
    const i = lane.issues.map(n => {
      const v = issues.get(n);
      if (!v || !['open', 'closed'].includes(v.state)) throw new Error('Missing issue state #' + n);
      return v;
    });
    const p = lane.prs.map(n => {
      const v = prs.get(n);
      if (!v || !['open', 'closed'].includes(v.state)) throw new Error('Missing PR state #' + n);
      return v;
    });
    const closed = i.filter(x => x.state === 'closed').length;
    const merged = p.filter(x => pullState(x) === 'MERGED').length;
    const issueRefs = lane.issues.map(n => '[#' + n + '](https://github.com/' + REPO + '/issues/' + n + ')').join(', ');
    const prRefs = lane.prs.length ? lane.prs.map(n => {
      const state = pullState(prs.get(n));
      return '[#' + n + '](https://github.com/' + REPO + '/pull/' + n + ') ' + state;
    }).join('; ') : 'No linked PRs yet';
    rows.push('| **' + lane.name + '** (' + lane.priority + ') | ' + closed + '/' + i.length + ' linked tickets closed | ' + merged + '/' + p.length + ' linked PRs merged | **UNVERIFIED** |');
    for (const x of i) {
      if (x.state === 'closed' && x.closed_at && now - new Date(x.closed_at) <= ageMs && now >= new Date(x.closed_at))
        recent.push({at:x.closed_at, label:lane.name + ': issue [#' + x.number + '](https://github.com/' + REPO + '/issues/' + x.number + ') closed'});
    }
    for (const x of p) {
      if (x.merged_at && now - new Date(x.merged_at) <= ageMs && now >= new Date(x.merged_at))
        recent.push({at:x.merged_at, label:lane.name + ': PR [#' + x.number + '](https://github.com/' + REPO + '/pull/' + x.number + ') merged'});
    }
    rows.push('| ↳ **Source links** | ' + issueRefs + ' | ' + prRefs + ' | Independent production evidence required |');
  }
  recent.sort((a,b)=>b.at.localeCompare(a.at));
  const ticker = recent.slice(0,12).map(x => '- ' + x.at.replace('T',' ').slice(0,16) + ' UTC — ' + x.label).join('\n') || '- No new closed issues or merged PRs among the tracked links in the past seven days.';
  return '**Last successful refresh:** ' + now.toISOString().replace('T',' ').slice(0,19) + ' UTC. GitHub issue/PR signals only; not model approval.\n\n'
    + '| Workstream | Ticket progress | Code progress | Production capability |\n|---|---|---|---|\n'
    + rows.join('\n') + '\n\n'
    + '**Completion ticker — newest first (past 7 days)**\n\n' + ticker + '\n\n'
    + '**Important:** A merged PR or closed ticket is *not* evidence of a certified fitted model, Action delivery, or final terminal publication. Production stays UNVERIFIED until separate SHA-pinned, independent SIRT acceptance is recorded. This script never changes that classification.';
}

function api(method, path, data) {
  const token = process.env.GITHUB_TOKEN;
  if (!token) return Promise.reject(new Error('GITHUB_TOKEN is missing'));
  return new Promise((resolve, reject) => {
    const payload = data === undefined ? null : JSON.stringify(data);
    const req = https.request({
      hostname:'api.github.com', path, method,
      headers:{
        'Accept':'application/vnd.github+json',
        'Authorization':'Bearer ' + token,
        'X-GitHub-Api-Version':'2022-11-28',
        'User-Agent':'wow-v17-restoration-ticker',
        ...(payload ? {'Content-Type':'application/json','Content-Length':Buffer.byteLength(payload)} : {})
      },
      timeout: 15000,
    }, res => {
      let text = '';
      res.on('data', d => { text += d; if (text.length > 1024 * 1024) req.destroy(new Error('Oversized response')); });
      res.on('end', () => {
        if (res.statusCode < 200 || res.statusCode >= 300) return reject(new Error(method + ' ' + path + ' HTTP ' + res.statusCode));
        try { resolve(JSON.parse(text)); } catch(e) { reject(e); }
      });
    });
    req.on('error', reject);
    req.on('timeout', ()=>req.destroy(new Error('GitHub request timed out')));
    req.end(payload || undefined);
  });
}

async function main() {
  if (process.env.GITHUB_REPOSITORY !== REPO) throw new Error('Wrong repository; refusing to write');
  const uniqueIssues = [...new Set(LANES.flatMap(x=>x.issues))];
  const uniquePRs = [...new Set(LANES.flatMap(x=>x.prs))];
  const [issueEntries, prEntries] = await Promise.all([
    Promise.all(uniqueIssues.map(async n=>[n,await api('GET','/repos/'+REPO+'/issues/'+n)])),
    Promise.all(uniquePRs.map(async n=>[n,await api('GET','/repos/'+REPO+'/pulls/'+n)])),
  ]);
  const dashboard = await api('GET','/repos/'+REPO+'/issues/'+DASHBOARD_ISSUE);
  const report = buildReport(new Map(issueEntries), new Map(prEntries));
  const body = replaceBlock(dashboard.body, report);
  if (body !== dashboard.body) {
    await api('PATCH','/repos/'+REPO+'/issues/'+DASHBOARD_ISSUE,{body});
    console.log('Refreshed progress board #' + DASHBOARD_ISSUE);
  }
}
if (require.main === module) {
  main().catch(err => {
    console.error('Refresh failed; previous board snapshot remains untouched:', err.message);
    process.exitCode = 1;
  });
}
module.exports = {buildReport,replaceBlock,pullState,START,END,LANES};
