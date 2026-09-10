-- Jalankan setelah 001_initial.sql dan 002_admin_panel.sql.
-- Menutup temuan Supabase Database Linter untuk SECURITY DEFINER RPC.
-- Aplikasi Flask memakai SUPABASE_SERVICE_ROLE_KEY di backend; klien tidak
-- perlu, dan tidak boleh, mengeksekusi helper otorisasi ini lewat REST RPC.

revoke all on function public.current_admin_role() from public;
revoke execute on function public.current_admin_role() from anon, authenticated;

-- Verifikasi setelah menjalankan migration ini:
-- select has_function_privilege('anon', 'public.current_admin_role()', 'execute');
-- select has_function_privilege('authenticated', 'public.current_admin_role()', 'execute');
-- Keduanya harus false.
