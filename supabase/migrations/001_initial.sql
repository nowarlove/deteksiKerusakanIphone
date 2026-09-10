-- Jalankan melalui Supabase SQL Editor atau migration CLI.
create extension if not exists pgcrypto;

create table if not exists public.diagnosis_history (
  id uuid primary key default gen_random_uuid(),
  phone_type text not null,
  features jsonb not null default '{}'::jsonb,
  top_diagnosis text not null,
  top_probability double precision,
  is_software boolean not null default false,
  extractor_provider text,
  extractor_model text,
  duration_ms integer,
  knowledge_base_version text not null default 'legacy',
  created_at timestamptz not null default now()
);

create table if not exists public.feedback_cases (
  id uuid primary key default gen_random_uuid(),
  nama text not null,
  tipe_hp text not null,
  keluhan text not null,
  kerusakan_sebenarnya text not null,
  status text not null default 'pending_expert_verification'
    check (status in ('pending_expert_verification', 'verified', 'rejected')),
  verified_by text,
  verified_at timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists public.model_versions (
  id uuid primary key default gen_random_uuid(),
  version text unique not null,
  dataset_hash text,
  metrics jsonb not null default '{}'::jsonb,
  metadata jsonb not null default '{}'::jsonb,
  is_active boolean not null default false,
  created_at timestamptz not null default now()
);

create table if not exists public.api_rate_limits (
  identifier text not null,
  window_start timestamptz not null,
  request_count integer not null default 1,
  primary key (identifier, window_start)
);

alter table public.diagnosis_history enable row level security;
alter table public.feedback_cases enable row level security;
alter table public.model_versions enable row level security;
alter table public.api_rate_limits enable row level security;

-- Tidak ada policy publik. Akses aplikasi hanya melalui service-role backend.
create or replace function public.check_rate_limit(
  p_identifier text,
  p_limit integer,
  p_window_seconds integer
) returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_window timestamptz;
  v_count integer;
begin
  v_window := to_timestamp(
    floor(extract(epoch from now()) / p_window_seconds) * p_window_seconds
  );
  insert into public.api_rate_limits(identifier, window_start, request_count)
  values (p_identifier, v_window, 1)
  on conflict (identifier, window_start)
  do update set request_count = public.api_rate_limits.request_count + 1
  returning request_count into v_count;
  return v_count <= p_limit;
end;
$$;

revoke all on function public.check_rate_limit(text, integer, integer) from public, anon, authenticated;
grant execute on function public.check_rate_limit(text, integer, integer) to service_role;

