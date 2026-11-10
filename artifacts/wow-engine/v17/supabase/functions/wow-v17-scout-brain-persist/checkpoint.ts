/* P0 #1775: optional protocol V2 for exact-once persistence.
 * Only the credentialed Edge handler calls this; all writes and acknowledgement
 * insertion occur in ONE PostgreSQL transaction per source-run chunk.
 */
type Row = Record<string, unknown>;
type Counts = {changed:number; quarantined:number; candidatesFinalized:number; observations:number; sourceSnapshots:number; candidateSourceLinks:number};
type Ops = {
  openRun:(tx:any,id:string,body:Row)=>Promise<void>;
  persistCandidates:(tx:any,id:string,entries:Row[])=>Promise<Counts>;
  accumulate:(tx:any,id:string,counts:Counts)=>Promise<void>;
  closeRun:(tx:any,id:string,body:Row)=>Promise<Row>;
  candidateRows:(body:Row)=>Row[];
};
const invalid = (code:string):never => { throw new Error(code); };
function count(value:unknown):number {
  return typeof value==="number" && Number.isSafeInteger(value) && value>=0 ? value : -1;
}
async function digest(entries:Row[]):Promise<string> {
  const buf = new TextEncoder().encode(JSON.stringify(entries));
  const hash = new Uint8Array(await crypto.subtle.digest("SHA-256",buf));
  return Array.from(hash).map(n=>n.toString(16).padStart(2,"0")).join("");
}
async function lock(tx:any,id:string):Promise<void> {
  await tx`select pg_advisory_xact_lock(hashtextextended(${id},0))`;
}
export async function checkpointedPersistence(
  sql:any, phase:string,id:string,body:Row,ops:Ops
):Promise<Row> {
  const candidatesExpected=count(body.expected_candidates);
  const evidenceExpected=count(body.expected_evidence);
  const headerHash=await digest([Object.fromEntries(Object.entries(body).filter(([key]) =>
    !["persist_phase","chunk_index","candidates"].includes(key)))]);
  if(candidatesExpected<0||evidenceExpected<0) invalid("SCOUT_PERSIST_EXPECTED_TOTALS_INVALID");
  const totalsMatch=(s:Row)=> Number(s.expected_candidates)===candidatesExpected &&
    Number(s.expected_evidence)===evidenceExpected && s.header_sha256===headerHash;
  if(phase==="BEGIN"){
    let next=0;
    await sql.begin(async(tx:any)=>{
      await lock(tx,id);
      const sessions=await tx`select * from wow_scout.persist_upload_sessions where research_run_id=${id} for update`;
      if(sessions.length){
        if(!totalsMatch(sessions[0])||!["PERSISTING","COMPLETE"].includes(String(sessions[0].upload_state)))
          invalid("SCOUT_PERSIST_REPLAY_CONFLICT");
        next=Number(sessions[0].next_chunk_index);
      }else{
        const prev=await tx`select status from wow_scout.research_runs where research_run_id=${id}`;
        if(prev.length) invalid("SCOUT_PERSIST_LEGACY_PARTIAL_REQUIRES_RECONCILIATION");
        await ops.openRun(tx,id,body);
        await tx`insert into wow_scout.persist_upload_sessions
          (research_run_id,expected_candidates,expected_evidence,header_sha256,can_execute)
          values (${id},${candidatesExpected},${evidenceExpected},${headerHash},false)`;
      }
    });
    return {ok:true,protocol_version:2,persist_phase:"BEGIN",next_chunk_index:next,can_execute:false};
  }
  if(phase==="APPEND"){
    const index=count(body.chunk_index), entries=ops.candidateRows(body);
    if(index<0||!entries.length) invalid("SCOUT_PERSIST_CHUNK_INVALID");
    const sha=await digest(entries);
    let ack:Row={};
    await sql.begin(async(tx:any)=>{
      await lock(tx,id);
      const sessions=await tx`select * from wow_scout.persist_upload_sessions where research_run_id=${id} for update`;
      if(!sessions.length) invalid("SCOUT_PERSIST_BEGIN_REQUIRED");
      const session=sessions[0];
      if(!totalsMatch(session)||!["PERSISTING","COMPLETE"].includes(String(session.upload_state))) invalid("SCOUT_PERSIST_REPLAY_CONFLICT");
      const next=Number(session.next_chunk_index);
      if(index>next) invalid("SCOUT_PERSIST_CHUNK_OUT_OF_ORDER");
      if(index===next && session.upload_state==="COMPLETE") invalid("SCOUT_PERSIST_FINALIZED_APPEND_DENIED");
      if(index<next){
        const prev=await tx`select * from wow_scout.persist_chunk_receipts
          where research_run_id=${id} and chunk_index=${index}`;
        if(prev.length!==1||prev[0].content_sha256!==sha||Number(prev[0].entries)!==entries.length)
          invalid("SCOUT_PERSIST_CHUNK_DIGEST_MISMATCH");
        ack={batch_entry_count:Number(prev[0].entries),
          batch_finalized_candidate_count:Number(prev[0].finalized_candidates),
          batch_evidence_row_count:Number(prev[0].evidence_rows),duplicate:true};
        return;
      }
      const counts=await ops.persistCandidates(tx,id,entries);
      await ops.accumulate(tx,id,counts);
      await tx`insert into wow_scout.persist_chunk_receipts
        (research_run_id,chunk_index,content_sha256,entries,finalized_candidates,evidence_rows,can_execute)
        values (${id},${index},${sha},${entries.length},${counts.candidatesFinalized},${counts.observations},false)`;
      await tx`update wow_scout.persist_upload_sessions set
        next_chunk_index=next_chunk_index+1,
        uploaded_candidates=uploaded_candidates+${counts.candidatesFinalized},
        uploaded_evidence=uploaded_evidence+${counts.observations},
        updated_at=now() where research_run_id=${id}`;
      ack={batch_entry_count:entries.length,
        batch_finalized_candidate_count:counts.candidatesFinalized,
        batch_evidence_row_count:counts.observations,duplicate:false};
    });
    return {ok:true,protocol_version:2,persist_phase:"APPEND",chunk_index:index,...ack,can_execute:false};
  }
  if(phase==="FINALIZE"){
    let result:Row={};
    await sql.begin(async(tx:any)=>{
      await lock(tx,id);
      const sessions=await tx`select * from wow_scout.persist_upload_sessions where research_run_id=${id} for update`;
      if(!sessions.length) invalid("SCOUT_PERSIST_BEGIN_REQUIRED");
      const session=sessions[0];
      if(!totalsMatch(session)) invalid("SCOUT_PERSIST_REPLAY_CONFLICT");
      if(Number(session.uploaded_candidates)!==candidatesExpected||
         Number(session.uploaded_evidence)!==evidenceExpected) invalid("SCOUT_PERSIST_INCOMPLETE");
      if(session.upload_state==="COMPLETE"){
        const prev=await tx`select candidate_count,changed_candidate_count,quarantined_count,source_snapshot from wow_scout.research_runs
          where research_run_id=${id} and status='COMPLETE'`;
        if(prev.length!==1) invalid("SCOUT_PERSIST_RECEIPT_MISSING");
        const snapshot=prev[0].source_snapshot;
        const stats:Row = snapshot&&typeof snapshot==="object" ? snapshot as Row : {};
        result={candidate_count:Number(prev[0].candidate_count),
          changed_candidate_count:Number(prev[0].changed_candidate_count),
          quarantined_count:Number(prev[0].quarantined_count),
          observation_count:Number(stats.observations_seen||0),
          source_snapshot_count:Number(stats.source_snapshots_seen||0),
          candidate_source_link_count:Number(stats.candidate_source_links_seen||0),duplicate:true};
        return;
      }
      result=await ops.closeRun(tx,id,body);
      if(Number(result.candidate_count)!==candidatesExpected||
         Number(result.observation_count)!==evidenceExpected)
        invalid("SCOUT_PERSIST_RECEIPT_RECONCILIATION_FAILED");
      await tx`update wow_scout.persist_upload_sessions
        set upload_state='COMPLETE',updated_at=now() where research_run_id=${id}`;
    });
    return {ok:true,protocol_version:2,persist_phase:"FINALIZE",...result,can_execute:false};
  }
  return invalid("SCOUT_PERSIST_PHASE_UNSUPPORTED");
}
