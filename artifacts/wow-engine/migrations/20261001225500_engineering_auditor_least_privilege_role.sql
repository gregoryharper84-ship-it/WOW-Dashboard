-- Class B reliability repair: purpose-scoped DB identity for the resident
-- Engineering Auditor. No sporting probability or execution authority.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'wow_engineering_auditor') THEN
    CREATE ROLE wow_engineering_auditor
      LOGIN
      NOSUPERUSER
      NOCREATEDB
      NOCREATEROLE
      NOINHERIT
      NOBYPASSRLS
      CONNECTION LIMIT 4;
  END IF;
END
$$;

ALTER ROLE wow_engineering_auditor SET statement_timeout = '15s';
ALTER ROLE wow_engineering_auditor SET lock_timeout = '5s';
ALTER ROLE wow_engineering_auditor SET idle_in_transaction_session_timeout = '15s';

GRANT CONNECT ON DATABASE postgres TO wow_engineering_auditor;
GRANT USAGE ON SCHEMA public TO wow_engineering_auditor;

GRANT SELECT, INSERT ON TABLE public.wow_agent_audit_events TO wow_engineering_auditor;
GRANT SELECT, INSERT, UPDATE ON TABLE public.wow_engineering_audit_work_items TO wow_engineering_auditor;
GRANT SELECT, INSERT, UPDATE ON TABLE public.wow_engineering_audit_findings TO wow_engineering_auditor;
GRANT SELECT, INSERT, UPDATE ON TABLE public.wow_engineering_auditor_runtime TO wow_engineering_auditor;
GRANT SELECT, INSERT, UPDATE ON TABLE public.wow_engineering_backlog TO wow_engineering_auditor;

GRANT USAGE, SELECT ON SEQUENCE public.wow_agent_audit_events_audit_event_id_seq TO wow_engineering_auditor;
GRANT USAGE, SELECT ON SEQUENCE public.wow_engineering_backlog_id_seq TO wow_engineering_auditor;

CREATE POLICY wow_engineering_auditor_read_agent_events
  ON public.wow_agent_audit_events
  FOR SELECT TO wow_engineering_auditor
  USING (true);
CREATE POLICY wow_engineering_auditor_insert_agent_events
  ON public.wow_agent_audit_events
  FOR INSERT TO wow_engineering_auditor
  WITH CHECK (can_execute IS FALSE);

CREATE POLICY wow_engineering_auditor_read_work_items
  ON public.wow_engineering_audit_work_items
  FOR SELECT TO wow_engineering_auditor
  USING (true);
CREATE POLICY wow_engineering_auditor_insert_work_items
  ON public.wow_engineering_audit_work_items
  FOR INSERT TO wow_engineering_auditor
  WITH CHECK (
    can_execute IS FALSE
    AND terminal_authority = 'V17_TERMINAL_REDUCER'
  );
CREATE POLICY wow_engineering_auditor_update_work_items
  ON public.wow_engineering_audit_work_items
  FOR UPDATE TO wow_engineering_auditor
  USING (true)
  WITH CHECK (
    can_execute IS FALSE
    AND terminal_authority = 'V17_TERMINAL_REDUCER'
  );

CREATE POLICY wow_engineering_auditor_read_findings
  ON public.wow_engineering_audit_findings
  FOR SELECT TO wow_engineering_auditor
  USING (true);
CREATE POLICY wow_engineering_auditor_insert_findings
  ON public.wow_engineering_audit_findings
  FOR INSERT TO wow_engineering_auditor
  WITH CHECK (
    can_execute IS FALSE
    AND terminal_authority = 'V17_TERMINAL_REDUCER'
  );
CREATE POLICY wow_engineering_auditor_update_findings
  ON public.wow_engineering_audit_findings
  FOR UPDATE TO wow_engineering_auditor
  USING (true)
  WITH CHECK (
    can_execute IS FALSE
    AND terminal_authority = 'V17_TERMINAL_REDUCER'
  );

CREATE POLICY wow_engineering_auditor_read_runtime
  ON public.wow_engineering_auditor_runtime
  FOR SELECT TO wow_engineering_auditor
  USING (true);
CREATE POLICY wow_engineering_auditor_insert_runtime
  ON public.wow_engineering_auditor_runtime
  FOR INSERT TO wow_engineering_auditor
  WITH CHECK (
    can_execute IS FALSE
    AND terminal_authority = 'V17_TERMINAL_REDUCER'
  );
CREATE POLICY wow_engineering_auditor_update_runtime
  ON public.wow_engineering_auditor_runtime
  FOR UPDATE TO wow_engineering_auditor
  USING (true)
  WITH CHECK (
    can_execute IS FALSE
    AND terminal_authority = 'V17_TERMINAL_REDUCER'
  );

CREATE POLICY wow_engineering_auditor_read_backlog
  ON public.wow_engineering_backlog
  FOR SELECT TO wow_engineering_auditor
  USING (true);
CREATE POLICY wow_engineering_auditor_insert_backlog
  ON public.wow_engineering_backlog
  FOR INSERT TO wow_engineering_auditor
  WITH CHECK (source = 'ENGINEERING_AUDITOR');
CREATE POLICY wow_engineering_auditor_update_backlog
  ON public.wow_engineering_backlog
  FOR UPDATE TO wow_engineering_auditor
  USING (source = 'ENGINEERING_AUDITOR')
  WITH CHECK (source = 'ENGINEERING_AUDITOR');
