-- LifeKanban Supabase bootstrap schema.
-- Run this in the Supabase SQL editor before enabling KANBAN_STORAGE=supabase.

create table if not exists public.lifekanban_documents (
  key text primary key,
  value jsonb not null,
  updated_at timestamptz not null default now()
);

create or replace function public.set_lifekanban_documents_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists lifekanban_documents_updated_at on public.lifekanban_documents;
create trigger lifekanban_documents_updated_at
before update on public.lifekanban_documents
for each row
execute function public.set_lifekanban_documents_updated_at();

alter table public.lifekanban_documents enable row level security;

-- No anon/authenticated policies are created. The LifeKanban server uses the
-- service-role key from its private environment; browsers never see it.
