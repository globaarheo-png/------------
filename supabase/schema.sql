create table if not exists public.users (
  id bigint primary key,
  name text,
  first_started_at timestamptz not null default now()
);

create table if not exists public.family_settings (
  user_id bigint primary key references public.users(id) on delete cascade,
  people_count integer,
  adults_count integer,
  children_count integer,
  child_age text,
  allergies jsonb not null default '[]'::jsonb,
  excluded_products jsonb not null default '[]'::jsonb,
  updated_at timestamptz not null default now()
);

alter table public.family_settings
  add column if not exists adults_count integer;

alter table public.family_settings
  add column if not exists children_count integer;

alter table public.family_settings
  add column if not exists child_ages jsonb not null default '[]'::jsonb;

create table if not exists public.food_requests (
  id bigserial primary key,
  user_id bigint not null references public.users(id) on delete cascade,
  raw_text text not null,
  parsed_products_json jsonb,
  options_json jsonb,
  selected_option_number integer,
  selected_recipe_text text,
  child_note text,
  status text not null default 'options_shown',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.favorites (
  id bigserial primary key,
  user_id bigint not null references public.users(id) on delete cascade,
  food_request_id bigint references public.food_requests(id) on delete set null,
  title text not null,
  recipe text not null,
  child_note text,
  created_at timestamptz not null default now()
);

create index if not exists food_requests_user_created_idx
  on public.food_requests(user_id, created_at desc);


create index if not exists favorites_user_created_idx
  on public.favorites(user_id, created_at desc);
