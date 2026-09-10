-- Panel admin Supabase: jalankan setelah 001_initial.sql.
-- Login dikelola Supabase Auth; tabel ini tidak menyimpan password.

create table if not exists public.admin_profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  username text not null unique,
  role text not null default 'expert' check (role in ('admin', 'expert')),
  is_active boolean not null default true,
  last_login_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.diagnosis_history
  add column if not exists case_number text,
  add column if not exists customer_name text,
  add column if not exists complaint text,
  add column if not exists features_confirmed boolean not null default false,
  add column if not exists all_diagnoses jsonb not null default '[]'::jsonb,
  add column if not exists ds_metrics jsonb not null default '{}'::jsonb;

create unique index if not exists diagnosis_history_case_number_key
  on public.diagnosis_history(case_number) where case_number is not null;
create index if not exists diagnosis_history_created_idx on public.diagnosis_history(created_at desc);
create index if not exists diagnosis_history_class_idx on public.diagnosis_history(top_diagnosis);

create table if not exists public.case_verifications (
  id uuid primary key default gen_random_uuid(),
  diagnosis_id uuid not null unique references public.diagnosis_history(id) on delete cascade,
  actual_diagnosis text,
  verification_status text not null default 'pending'
    check (verification_status in ('pending', 'verified', 'rejected', 'needs_revision')),
  expert_notes text,
  verified_by uuid references public.admin_profiles(id) on delete set null,
  verified_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists case_verifications_status_idx
  on public.case_verifications(verification_status);

create table if not exists public.repair_prices (
  id uuid primary key default gen_random_uuid(),
  damage_class text not null,
  phone_model_pattern text not null default '*',
  service_name text not null,
  minimum_price numeric(14,2) not null check (minimum_price >= 0),
  maximum_price numeric(14,2) not null check (maximum_price >= minimum_price),
  currency text not null default 'IDR',
  notes text,
  is_active boolean not null default true,
  effective_from timestamptz not null default now(),
  effective_until timestamptz,
  updated_by uuid references public.admin_profiles(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists repair_prices_lookup_idx
  on public.repair_prices(damage_class, is_active, effective_from desc);

create table if not exists public.training_datasets (
  id uuid primary key default gen_random_uuid(),
  version text not null unique,
  source text not null,
  row_count integer not null check (row_count >= 0),
  file_path text not null,
  sha256 text not null,
  parent_version text,
  status text not null default 'draft' check (status in ('draft', 'validated', 'archived')),
  created_by uuid references public.admin_profiles(id) on delete set null,
  created_at timestamptz not null default now()
);

create table if not exists public.training_jobs (
  id uuid primary key default gen_random_uuid(),
  job_type text not null check (job_type in ('preprocessing', 'pso', 'evaluation')),
  dataset_version text,
  status text not null default 'queued'
    check (status in ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
  progress integer not null default 0 check (progress between 0 and 100),
  parameters jsonb not null default '{}'::jsonb,
  log_path text,
  artifact_path text,
  metrics jsonb not null default '{}'::jsonb,
  started_by uuid references public.admin_profiles(id) on delete set null,
  started_at timestamptz,
  finished_at timestamptz,
  error_message text,
  created_at timestamptz not null default now()
);

alter table public.model_versions
  add column if not exists knowledge_base_path text,
  add column if not exists approved_by uuid references public.admin_profiles(id) on delete set null,
  add column if not exists approved_at timestamptz;
create unique index if not exists model_versions_one_active_idx
  on public.model_versions(is_active) where is_active;

create table if not exists public.audit_logs (
  id uuid primary key default gen_random_uuid(),
  admin_id uuid references public.admin_profiles(id) on delete set null,
  action text not null,
  entity_type text not null,
  entity_id text,
  before_data jsonb not null default '{}'::jsonb,
  after_data jsonb not null default '{}'::jsonb,
  ip_hash text,
  created_at timestamptz not null default now()
);
create index if not exists audit_logs_created_idx on public.audit_logs(created_at desc);

-- Setiap diagnosis baru otomatis masuk antrean verifikasi pakar.
create or replace function public.create_pending_verification()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.case_verifications(diagnosis_id) values (new.id)
  on conflict (diagnosis_id) do nothing;
  return new;
end;
$$;
drop trigger if exists diagnosis_pending_verification on public.diagnosis_history;
create trigger diagnosis_pending_verification
after insert on public.diagnosis_history for each row execute function public.create_pending_verification();
revoke all on function public.create_pending_verification() from public, anon, authenticated;

-- Aktivasi satu versi model dilakukan atomik dan hanya oleh admin aktif.
create or replace function public.activate_model_version(p_model_id uuid, p_admin_id uuid)
returns void language plpgsql security definer set search_path = public as $$
begin
  if not exists (
    select 1 from public.admin_profiles
    where id = p_admin_id and role = 'admin' and is_active
  ) then raise exception 'admin_required'; end if;
  update public.model_versions set is_active = false where is_active;
  update public.model_versions
     set is_active = true, approved_by = p_admin_id, approved_at = now()
   where id = p_model_id;
  if not found then raise exception 'model_not_found'; end if;
end;
$$;

-- Helper policy. SECURITY DEFINER mencegah rekursi policy pada admin_profiles.
create or replace function public.current_admin_role()
returns text language sql stable security definer set search_path = public as $$
  select role from public.admin_profiles where id = auth.uid() and is_active limit 1
$$;
revoke all on function public.current_admin_role() from public;
-- Panel admin hanya mengakses database melalui backend service-role.
-- Fungsi ini dipanggil dari kebijakan RLS, bukan endpoint RPC publik.
revoke execute on function public.current_admin_role() from anon, authenticated;

alter table public.admin_profiles enable row level security;
alter table public.case_verifications enable row level security;
alter table public.repair_prices enable row level security;
alter table public.training_datasets enable row level security;
alter table public.training_jobs enable row level security;
alter table public.audit_logs enable row level security;

-- Hapus dahulu agar migration aman dijalankan ulang.
drop policy if exists profiles_read_self_or_admin on public.admin_profiles;
create policy profiles_read_self_or_admin on public.admin_profiles for select to authenticated
using (id = auth.uid() or public.current_admin_role() = 'admin');

drop policy if exists diagnoses_staff_read on public.diagnosis_history;
create policy diagnoses_staff_read on public.diagnosis_history for select to authenticated
using (public.current_admin_role() in ('admin', 'expert'));

drop policy if exists verifications_staff_read on public.case_verifications;
create policy verifications_staff_read on public.case_verifications for select to authenticated
using (public.current_admin_role() in ('admin', 'expert'));
drop policy if exists verifications_staff_update on public.case_verifications;
create policy verifications_staff_update on public.case_verifications for update to authenticated
using (public.current_admin_role() in ('admin', 'expert'))
with check (
  public.current_admin_role() in ('admin', 'expert')
  and verified_by = auth.uid()
);

drop policy if exists prices_staff_read on public.repair_prices;
create policy prices_staff_read on public.repair_prices for select to authenticated
using (public.current_admin_role() in ('admin', 'expert'));
drop policy if exists prices_admin_write on public.repair_prices;
create policy prices_admin_write on public.repair_prices for all to authenticated
using (public.current_admin_role() = 'admin') with check (public.current_admin_role() = 'admin');

drop policy if exists datasets_staff_read on public.training_datasets;
create policy datasets_staff_read on public.training_datasets for select to authenticated
using (public.current_admin_role() in ('admin', 'expert'));
drop policy if exists datasets_admin_write on public.training_datasets;
create policy datasets_admin_write on public.training_datasets for all to authenticated
using (public.current_admin_role() = 'admin') with check (public.current_admin_role() = 'admin');

drop policy if exists jobs_staff_read on public.training_jobs;
create policy jobs_staff_read on public.training_jobs for select to authenticated
using (public.current_admin_role() in ('admin', 'expert'));
drop policy if exists jobs_admin_write on public.training_jobs;
create policy jobs_admin_write on public.training_jobs for all to authenticated
using (public.current_admin_role() = 'admin') with check (public.current_admin_role() = 'admin');

drop policy if exists models_staff_read on public.model_versions;
create policy models_staff_read on public.model_versions for select to authenticated
using (public.current_admin_role() in ('admin', 'expert'));
drop policy if exists models_admin_write on public.model_versions;
create policy models_admin_write on public.model_versions for all to authenticated
using (public.current_admin_role() = 'admin') with check (public.current_admin_role() = 'admin');

drop policy if exists audits_admin_read on public.audit_logs;
create policy audits_admin_read on public.audit_logs for select to authenticated
using (public.current_admin_role() = 'admin');

revoke all on function public.activate_model_version(uuid, uuid) from public, anon, authenticated;
grant execute on function public.activate_model_version(uuid, uuid) to service_role;

-- SQL privilege dan RLS harus sama-sama mengizinkan operasi.
grant select on public.admin_profiles, public.diagnosis_history, public.case_verifications,
  public.repair_prices, public.training_datasets, public.training_jobs, public.model_versions
  to authenticated;
grant update on public.case_verifications to authenticated;
grant insert, update, delete on public.repair_prices, public.training_datasets,
  public.training_jobs, public.model_versions to authenticated;

-- Buat akun melalui Authentication > Users, lalu tautkan UUID-nya, misalnya:
-- insert into public.admin_profiles(id, username, role)
-- values ('UUID_DARI_AUTH_USERS', 'admin@example.com', 'admin');
