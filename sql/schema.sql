-- Schema for market-data-pipeline on Supabase (PostgreSQL)

create table if not exists public.market_bars (
    symbol text not null,
    asset_class text not null,
    timeframe text not null check (timeframe in ('4H', '1D', '1W')),
    ts timestamptz not null,
    open double precision not null,
    high double precision not null,
    low double precision not null,
    close double precision not null,
    volume double precision,
    sma_20 double precision,
    sma_50 double precision,
    ema_20 double precision,
    rsi_14 double precision,
    atr_14 double precision,
    source text not null,
    inserted_at timestamptz not null default now(),
    primary key (symbol, timeframe, ts)
);

create index if not exists idx_market_bars_asset_class_ts
    on public.market_bars (asset_class, timeframe, ts desc);

-- Enable Row Level Security.
-- Note: The GitHub Actions writer authenticates with SUPABASE_SERVICE_ROLE_KEY.
-- In Supabase, the `service_role` Postgres role has BYPASSRLS enabled, so server-side
-- upserts bypass RLS without needing write policies exposed to client roles.
alter table public.market_bars enable row level security;

-- Deny all access to unauthenticated (`anon`) callers and allow read-only access
-- exclusively to signed-in (`authenticated`) users.
revoke all on table public.market_bars from anon;
revoke all on table public.market_bars from authenticated;
grant select on table public.market_bars to authenticated;

drop policy if exists "authenticated_select_market_bars" on public.market_bars;
create policy "authenticated_select_market_bars"
    on public.market_bars
    for select
    to authenticated
    using (true);
