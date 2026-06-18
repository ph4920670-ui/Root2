-- SalasFF — Supabase Schema
-- Execute este arquivo no SQL Editor do Supabase.
-- Habilita UUID
create extension if not exists "pgcrypto";

create table if not exists keys (
  id text primary key default gen_random_uuid()::text,
  code text unique not null,
  quantia integer not null default 0,
  salas_usadas integer not null default 0,
  modo integer not null default 0,
  -- user_id / user_nome: aliases de dono_id / dono_nome (legado)
  user_id text,
  user_nome text,
  -- dono_id / dono_nome: campos primários usados pelo bot
  dono_id text,
  dono_nome text,
  origem text default 'venda',
  criado_por text,
  criado_em timestamptz default now(),
  resgatado_em timestamptz,
  usado_em timestamptz
);

create index if not exists keys_dono_id_idx on keys(dono_id);
create index if not exists keys_code_idx on keys(code);

create table if not exists salas (
  id text primary key default gen_random_uuid()::text,
  user_id text,
  user_nome text,
  modo integer,
  guild_id text,
  saldo_origem text default 'pessoal',
  pedidoid text,
  sala_id text,
  sala_senha text,
  sala_nome text,
  sala_link text,
  criado_em timestamptz default now(),
  atualizado_em timestamptz
);

create index if not exists salas_user_id_idx on salas(user_id);
create index if not exists salas_guild_id_idx on salas(guild_id);
create index if not exists salas_criado_em_idx on salas(criado_em);

create table if not exists pedidos_pix (
  id text primary key default gen_random_uuid()::text,
  txid text unique not null,
  user_id text,
  user_nome text,
  quantia integer,
  valor numeric(10,2),
  banco text default 'mistic',
  status text default 'pendente',
  guild_id text,
  guild_nome text,
  key_gerada text,
  nome_pagador text,
  endtoend text,
  criado_em timestamptz default now(),
  pago_em timestamptz
);

create index if not exists pedidos_txid_idx on pedidos_pix(txid);
create index if not exists pedidos_user_id_idx on pedidos_pix(user_id);
create index if not exists pedidos_status_idx on pedidos_pix(status);

create table if not exists guild_config (
  id text primary key,
  saldo integer default 0,
  cargo_sala_id text,
  cargo_cliente_id text,
  criado_por text,
  criado_em timestamptz default now(),
  canal_compras_id text,
  preco_sala numeric(10,4),
  cargos_por_qtd jsonb default '{}',
  avaliacao jsonb default '{}',
  chat_cmd jsonb default '{}',
  pix_creds jsonb default '{}'
);

create table if not exists lucro_config (
  user_id text primary key,
  valor_por_sala numeric(10,4) default 0,
  go_tempo integer default 0,
  sala_senha text,
  orgs jsonb default '[]'
);

create table if not exists botconfig (
  id text primary key default 'main',
  data jsonb not null default '{}'
);

create table if not exists bonus_data (
  user_id text primary key,
  user_nome text,
  total_comprado integer default 0,
  bonus_resgatado integer default 0,
  historico jsonb default '[]'
);

create table if not exists metas (
  user_id text primary key,
  alvo integer,
  dias integer,
  salas_inicio integer default 0,
  criado_em timestamptz,
  expira_em timestamptz
);

create table if not exists users_config (
  user_id text primary key,
  user_token text,
  token_mode_ativo boolean default false,
  token_mode_servidores jsonb default '[]'
);

create table if not exists invites_system (
  id text primary key,
  tipo text,
  inviter_id text,
  guild_id text,
  user_id text,
  codigo text,
  joined_em timestamptz,
  valido boolean default true,
  motivo text,
  aprovado boolean default false,
  aprovado_em timestamptz,
  saiu boolean default false,
  motivo_saida text,
  rejoined_em timestamptz,
  criado_em timestamptz default now()
);

create index if not exists invites_inviter_guild_idx on invites_system(inviter_id, guild_id);
create index if not exists invites_tipo_idx on invites_system(tipo);

-- Sistema de orgs
create table if not exists orgs (
  guild_id text primary key,
  ativo boolean default true,
  nome text,
  owner_discord_id text,
  porcentagem integer default 70,
  faturamento_total numeric(10,2) default 0,
  saldo_acumulado numeric(10,2) default 0,
  salas_total integer default 0,
  criado_em timestamptz default now()
);

create table if not exists saques (
  id text primary key default gen_random_uuid()::text,
  guild_id text,
  owner_discord_id text,
  valor numeric(10,2),
  pix_key text,
  status text default 'pendente',
  criado_em timestamptz default now(),
  resolvido_em timestamptz,
  motivo_rejeicao text
);

create index if not exists saques_status_idx on saques(status);
create index if not exists saques_guild_id_idx on saques(guild_id);

create table if not exists guild_commands (
  guild_id text primary key,
  commands jsonb default '{}'
);

-- Sessoes do site (auth por Supabase)
create table if not exists sessions (
  id text primary key,
  user_id text,
  user_name text,
  user_avatar text,
  data jsonb default '{}',
  expires_at timestamptz,
  criado_em timestamptz default now()
);

create index if not exists sessions_expires_at_idx on sessions(expires_at);
