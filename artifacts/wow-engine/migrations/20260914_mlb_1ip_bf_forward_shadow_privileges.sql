-- Restrict the BF shadow calibration-health RPC to the governed backend.
-- Tables already have RLS enabled with no anon/authenticated policies.

revoke all on function public.wow_mlb_1ip_bf_shadow_health(text) from public;
revoke all on function public.wow_mlb_1ip_bf_shadow_health(text) from anon;
revoke all on function public.wow_mlb_1ip_bf_shadow_health(text) from authenticated;
grant execute on function public.wow_mlb_1ip_bf_shadow_health(text) to service_role;

revoke all on function public.wow_mlb_1ip_bf_shadow_immutable_guard() from public;
revoke all on function public.wow_mlb_1ip_bf_shadow_immutable_guard() from anon;
revoke all on function public.wow_mlb_1ip_bf_shadow_immutable_guard() from authenticated;
