-- Class A: register a *standby* evidence-only Gemini provider in engineering receipts.
-- This migration does NOT enable Gemini, create any credential or grant authority.
-- Apply only after authorized review; existing append-only audit rules remain.
ALTER TABLE public.wow_engineering_attempt_receipts
  DROP CONSTRAINT IF EXISTS wow_engineering_attempt_receipts_provider_check;
ALTER TABLE public.wow_engineering_attempt_receipts
  ADD CONSTRAINT wow_engineering_attempt_receipts_provider_check
  CHECK (provider IN ('openai', 'claude', 'gemini', 'human_owner', 'automation'));
ALTER TABLE public.wow_engineering_attempt_receipts
  DROP CONSTRAINT IF EXISTS wow_eng_receipt_gemini_limited;
ALTER TABLE public.wow_engineering_attempt_receipts
  ADD CONSTRAINT wow_eng_receipt_gemini_limited
  CHECK (
    provider <> 'gemini' OR (
      role IN ('implementer', 'adversarial_reviewer', 'research_challenger', 'sirt_challenger')
      AND qa_decision IS NULL
      AND release_decision IS NULL
      AND deployed_sha IS NULL
      AND disposition <> 'FIXED_AND_VERIFIED'
    )
  );
