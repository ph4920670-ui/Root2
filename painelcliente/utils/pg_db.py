"""
utils/pg_db.py — painel-cliente
Postgres via pg8000 (pure Python, sem compilação).
"""
import os
import json
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional
import pg8000.native

DATABASE_URL = os.environ.get(
    'NEON_DATABASE_URL',
    'postgresql://postgres.yrfmxplfwvjhgzxuxuii:kitinho1210@aws-1-sa-east-1.pooler.supabase.com:6543/postgres'
)

_lock = threading.Lock()
_conn_cache = threading.local()

def _parse_url(url):
    # postgresql://user:pass@host:port/db
    import re
    m = re.match(r'postgresql://([^:]+):([^@]+)@([^:/]+):(\d+)/([^?]+)', url)
    if not m:
        raise ValueError(f"URL inválida: {url}")
    return {
        'user': m.group(1),
        'password': m.group(2),
        'host': m.group(3),
        'port': int(m.group(4)),
        'database': m.group(5),
    }

def _get_conn():
    if not getattr(_conn_cache, 'conn', None):
        params = _parse_url(DATABASE_URL)
        _conn_cache.conn = pg8000.native.Connection(**params, ssl_context=True)
    return _conn_cache.conn

def _run(sql, params=None):
    import re as _re2
    conn = _get_conn()
    if params:
        fixed = _re2.sub(':([0-9]+)', lambda m: '$' + m.group(1), sql)
    else:
        fixed = sql
    try:
        return conn.run(fixed, *params) if params else conn.run(fixed)
    except Exception:
        _conn_cache.conn = None
        conn = _get_conn()
        return conn.run(fixed, *params) if params else conn.run(fixed)

def _columns(conn):
    return [c['name'] for c in conn.columns]

def _to_dict(row, cols):
    if row is None:
        return None
    return dict(zip(cols, row))

def _rows_to_dicts(rows, cols):
    return [dict(zip(cols, r)) for r in rows]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS clientes (
    _id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    client_id       TEXT UNIQUE NOT NULL,
    client_pass     TEXT DEFAULT '',
    nome            TEXT NOT NULL,
    app_id          TEXT DEFAULT '',
    vencimento      TEXT DEFAULT '',
    saldo_salas     INTEGER NOT NULL DEFAULT 0,
    ultimo_saldo_visto INTEGER,
    ultimo_erro_sala TEXT DEFAULT '',
    meta            JSONB,
    variaveis       JSONB NOT NULL DEFAULT '{}'::jsonb,
    criado_em       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_clientes_nome ON clientes(LOWER(nome));

-- Migrations defensivas: garantem que colunas existam mesmo se a tabela
-- foi criada por outro projeto (bot-org cria 'clientes' com schema diferente).
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS client_id TEXT;
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS client_pass TEXT DEFAULT '';
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS app_id TEXT DEFAULT '';
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS vencimento TEXT DEFAULT '';
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS saldo_salas INTEGER NOT NULL DEFAULT 0;
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS ultimo_saldo_visto INTEGER;
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS ultimo_erro_sala TEXT DEFAULT '';
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS meta JSONB;
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS variaveis JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW();
UPDATE clientes SET client_id = LOWER(REGEXP_REPLACE(COALESCE(nome,'cliente'), '[^a-z0-9]+', '_', 'gi')) || '_' || id WHERE client_id IS NULL OR client_id = '';
CREATE UNIQUE INDEX IF NOT EXISTS idx_clientes_client_id ON clientes(client_id);

CREATE TABLE IF NOT EXISTS consumo (
    id          BIGSERIAL PRIMARY KEY,
    doc_id      TEXT NOT NULL,
    client_id   TEXT NOT NULL DEFAULT '',
    qtd         INTEGER NOT NULL DEFAULT 0,
    lucro_unit  NUMERIC(10,2) NOT NULL DEFAULT 0,
    ts          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_consumo_doc_id_ts ON consumo(doc_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_consumo_ts ON consumo(ts);
CREATE TABLE IF NOT EXISTS pagamentos (
    id              BIGSERIAL PRIMARY KEY,
    client_id       TEXT DEFAULT '',
    doc_id          TEXT DEFAULT '',
    transaction_id  TEXT UNIQUE NOT NULL,
    our_txid        TEXT DEFAULT '',
    qtd             INTEGER NOT NULL DEFAULT 0,
    valor           NUMERIC(10,2) NOT NULL DEFAULT 0,
    status          TEXT DEFAULT 'PENDENTE',
    creditado       BOOLEAN NOT NULL DEFAULT FALSE,
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pagamentos_txid ON pagamentos(transaction_id);
CREATE TABLE IF NOT EXISTS parceiros (
    _id     TEXT PRIMARY KEY,
    nome    TEXT NOT NULL,
    saldo   NUMERIC(10,2) NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS saques (
    id              BIGSERIAL PRIMARY KEY,
    parceiro        TEXT NOT NULL,
    amount          NUMERIC(10,2) NOT NULL DEFAULT 0,
    pix_key         TEXT DEFAULT '',
    pix_key_type    TEXT DEFAULT '',
    transaction_id  TEXT DEFAULT '',
    job_id          TEXT DEFAULT '',
    status          TEXT DEFAULT 'QUEUED',
    msg             TEXT DEFAULT '',
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS vendas (
    id              BIGSERIAL PRIMARY KEY,
    transaction_id  TEXT DEFAULT '',
    client_id       TEXT DEFAULT '',
    doc_id          TEXT DEFAULT '',
    cliente_nome    TEXT DEFAULT '',
    qtd             INTEGER NOT NULL DEFAULT 0,
    valor           NUMERIC(10,2) NOT NULL DEFAULT 0,
    parte_parceiro  NUMERIC(10,2) NOT NULL DEFAULT 0,
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vendas_ts ON vendas(ts DESC);
CREATE TABLE IF NOT EXISTS eventos_bot (
    id          BIGSERIAL PRIMARY KEY,
    client_id   TEXT NOT NULL,
    tipo        TEXT NOT NULL,
    detalhes    JSONB NOT NULL DEFAULT '{}'::jsonb,
    ts          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_eventos_client_id_ts ON eventos_bot(client_id, ts DESC);
CREATE TABLE IF NOT EXISTS config_global (
    _id             TEXT PRIMARY KEY DEFAULT 'global',
    misticpay_ci    TEXT DEFAULT '',
    misticpay_cs    TEXT DEFAULT '',
    valor_por_sala  NUMERIC(10,2) NOT NULL DEFAULT 1.0,
    extra           JSONB NOT NULL DEFAULT '{}'::jsonb
);
INSERT INTO config_global (_id) VALUES ('global') ON CONFLICT DO NOTHING;
"""

def init_schema():
    conn = _get_conn()
    for stmt in _SCHEMA.split(';'):
        stmt = stmt.strip()
        if stmt:
            try:
                conn.run(stmt)
            except Exception:
                pass

try:
    init_schema()
except Exception as e:
    print(f"[PG_DB] ⚠️ schema: {e}", flush=True)


# ══════════════════════════════════════════════════════
# CLIENTES
# ══════════════════════════════════════════════════════

def cliente_criar(dados: dict) -> dict:
    import re as _re
    base = _re.sub(r'[^a-z0-9]+', '_', (dados.get('nome') or 'cliente').lower()).strip('_') or 'cliente'
    client_id = base
    sufixo = 1
    while True:
        rows = _run("SELECT 1 FROM clientes WHERE client_id=:1", [client_id])
        if not rows:
            break
        sufixo += 1
        client_id = f"{base}_{sufixo}"
    variaveis = dados.get('variaveis') or {}
    _run("""
        INSERT INTO clientes (client_id, client_pass, nome, app_id, vencimento, saldo_salas, variaveis)
        VALUES (:1,:2,:3,:4,:5,:6,:7::jsonb)
    """, [client_id, dados.get('client_pass',''), dados.get('nome',''),
          dados.get('app_id',''), dados.get('vencimento',''),
          int(dados.get('saldo_salas',0) or 0), json.dumps(variaveis)])
    return cliente_buscar(client_id=client_id)


def _fetch_one_dict(sql, params=None):
    conn = _get_conn()
    rows = _run(sql, params)
    if not rows:
        return None
    cols = [c['name'] for c in conn.columns]
    return dict(zip(cols, rows[0]))


def _fetch_all_dicts(sql, params=None):
    conn = _get_conn()
    rows = _run(sql, params)
    if not rows:
        return []
    cols = [c['name'] for c in conn.columns]
    return [dict(zip(cols, r)) for r in rows]


def cliente_buscar(client_id=None, _id=None, nome=None) -> Optional[dict]:
    if _id:
        return _fetch_one_dict("SELECT * FROM clientes WHERE _id=:1", [_id])
    elif client_id:
        return _fetch_one_dict("SELECT * FROM clientes WHERE client_id=:1", [client_id])
    elif nome:
        return _fetch_one_dict("SELECT * FROM clientes WHERE LOWER(nome)=LOWER(:1)", [nome])
    return None


def cliente_buscar_por_nome_flex(usuario: str) -> Optional[dict]:
    import unicodedata
    doc = _fetch_one_dict("SELECT * FROM clientes WHERE client_id=:1", [usuario])
    if doc: return doc
    doc = _fetch_one_dict("SELECT * FROM clientes WHERE app_id=:1", [usuario])
    if doc: return doc
    doc = _fetch_one_dict("SELECT * FROM clientes WHERE LOWER(nome)=LOWER(:1)", [usuario])
    if doc: return doc
    def norm(s):
        s = unicodedata.normalize('NFKD', str(s))
        s = ''.join(c for c in s if not unicodedata.combining(c))
        return ' '.join(s.lower().split())
    alvo = norm(usuario)
    for c in cliente_listar():
        if norm(c.get('nome','')) == alvo:
            return c
    return None


def cliente_listar() -> list:
    return _fetch_all_dicts("SELECT * FROM clientes ORDER BY LOWER(nome)")


def cliente_atualizar(_id: str, campos: dict) -> bool:
    if not campos:
        return False
    sets = []
    vals = []
    i = 1
    for k, v in campos.items():
        if k == 'meta':
            sets.append(f"meta=:{i}::jsonb")
            vals.append(json.dumps(v) if v is not None else None)
        else:
            sets.append(f"{k}=:{i}")
            vals.append(v)
        i += 1
    vals.append(_id)
    _run(f"UPDATE clientes SET {', '.join(sets)} WHERE _id=:{i}", vals)
    return True


def cliente_set_variavel(_id: str, campo: str, valor: str) -> bool:
    _run(
        "UPDATE clientes SET variaveis = variaveis || jsonb_build_object(:1::text, :2::text) WHERE _id=:3",
        [campo, valor, _id]
    )
    return True


def cliente_inc_saldo(_id: str, qtd: int) -> Optional[dict]:
    _run("UPDATE clientes SET saldo_salas=saldo_salas+:1 WHERE _id=:2", [qtd, _id])
    return cliente_buscar(_id=_id)


def cliente_unset_erro(_id: str):
    _run("UPDATE clientes SET ultimo_erro_sala='' WHERE _id=:1", [_id])


# ══════════════════════════════════════════════════════
# CONSUMO
# ══════════════════════════════════════════════════════

def consumo_inserir(doc_id, client_id, qtd, lucro_unit):
    _run("INSERT INTO consumo (doc_id, client_id, qtd, lucro_unit) VALUES (:1,:2,:3,:4)",
         [doc_id, client_id, qtd, lucro_unit])


def consumo_agregar(doc_id, ts_from=None, ts_to=None) -> dict:
    conds = ["doc_id=:1"]
    vals = [doc_id]
    i = 2
    if ts_from:
        conds.append(f"ts>=:{i}")
        vals.append(ts_from)
        i += 1
    if ts_to:
        conds.append(f"ts<:{i}")
        vals.append(ts_to)
        i += 1
    rows = _run(
        f"SELECT COALESCE(SUM(qtd),0), COALESCE(SUM(qtd*lucro_unit),0) FROM consumo WHERE {' AND '.join(conds)}",
        vals
    )
    if rows:
        return {'salas': int(rows[0][0]), 'lucro': float(rows[0][1])}
    return {'salas': 0, 'lucro': 0.0}


def consumo_serie_diaria(doc_id, dias=14) -> list:
    agora = datetime.now(timezone.utc)
    inicio = agora.replace(hour=0,minute=0,second=0,microsecond=0) - timedelta(days=dias-1)
    rows = _run("""
        SELECT TO_CHAR(ts AT TIME ZONE 'UTC','YYYY-MM-DD') as dia,
               SUM(qtd), SUM(qtd*lucro_unit)
        FROM consumo WHERE doc_id=:1 AND ts>=:2
        GROUP BY dia ORDER BY dia
    """, [doc_id, inicio])
    bucket = {r[0]: {'salas': int(r[1]), 'lucro': float(r[2])} for r in (rows or [])}
    serie = []
    for i in range(dias):
        d = (inicio + timedelta(days=i)).strftime('%Y-%m-%d')
        item = bucket.get(d, {'salas': 0, 'lucro': 0.0})
        serie.append({'data': d, 'salas': item['salas'], 'lucro': item['lucro']})
    return serie


def consumo_listar_recente(limit=30) -> list:
    return _fetch_all_dicts("""
        SELECT c.*, cl.nome as cliente_nome
        FROM consumo c LEFT JOIN clientes cl ON cl._id=c.doc_id
        ORDER BY c.ts DESC LIMIT :1
    """, [limit])


# ══════════════════════════════════════════════════════
# PAGAMENTOS
# ══════════════════════════════════════════════════════

def pagamento_inserir(dados):
    _run("""
        INSERT INTO pagamentos (client_id,doc_id,transaction_id,our_txid,qtd,valor,status,creditado)
        VALUES (:1,:2,:3,:4,:5,:6,:7,:8)
    """, [dados.get('client_id',''), dados.get('doc_id',''), dados.get('transactionId',''),
          dados.get('our_txid',''), int(dados.get('qtd',0)), float(dados.get('valor',0)),
          dados.get('status','PENDENTE'), bool(dados.get('creditado',False))])


def pagamento_buscar(transaction_id) -> Optional[dict]:
    return _fetch_one_dict("SELECT * FROM pagamentos WHERE transaction_id=:1", [transaction_id])


def pagamento_creditar(pag_id) -> Optional[dict]:
    _run("UPDATE pagamentos SET creditado=TRUE, status='COMPLETO' WHERE id=:1 AND creditado=FALSE", [pag_id])
    return _fetch_one_dict("SELECT * FROM pagamentos WHERE id=:1", [pag_id])


def pagamento_falha(pag_id):
    _run("UPDATE pagamentos SET status='FALHA' WHERE id=:1", [pag_id])


# ══════════════════════════════════════════════════════
# PARCEIROS
# ══════════════════════════════════════════════════════

def parceiro_upsert(pk, nome):
    _run("INSERT INTO parceiros (_id,nome,saldo) VALUES (:1,:2,0) ON CONFLICT (_id) DO NOTHING", [pk, nome])


def parceiro_get(pk) -> Optional[dict]:
    return _fetch_one_dict("SELECT * FROM parceiros WHERE _id=:1", [pk])


def parceiro_inc_saldo(pk, amount):
    _run("UPDATE parceiros SET saldo=saldo+:1 WHERE _id=:2", [amount, pk])


def parceiro_dec_saldo_atomic(pk, amount) -> bool:
    _run("UPDATE parceiros SET saldo=saldo-:1 WHERE _id=:2 AND saldo>=:3", [amount, pk, amount-0.001])
    doc = parceiro_get(pk)
    # Verifica se debitou (saldo não ficou negativo)
    return doc is not None


def parceiro_saldo_total_saques(pk) -> float:
    rows = _run("""
        SELECT COALESCE(SUM(amount),0) FROM saques
        WHERE parceiro=:1 AND status IN ('QUEUED','PROCESSANDO','COMPLETO','PENDENTE')
    """, [pk])
    return float(rows[0][0]) if rows else 0.0


def parceiro_saques(pk, limit=50) -> list:
    return _fetch_all_dicts("SELECT * FROM saques WHERE parceiro=:1 ORDER BY ts DESC LIMIT :2", [pk, limit])


def saque_inserir(dados):
    _run("""
        INSERT INTO saques (parceiro,amount,pix_key,pix_key_type,transaction_id,job_id,status,msg)
        VALUES (:1,:2,:3,:4,:5,:6,:7,:8)
    """, [dados.get('parceiro',''), float(dados.get('amount',0)),
          dados.get('pixKey',''), dados.get('pixKeyType',''),
          dados.get('transactionId',''), dados.get('jobId',''),
          dados.get('status','QUEUED'), dados.get('msg','')])


# ══════════════════════════════════════════════════════
# VENDAS
# ══════════════════════════════════════════════════════

def venda_inserir(dados):
    _run("""
        INSERT INTO vendas (transaction_id,client_id,doc_id,cliente_nome,qtd,valor,parte_parceiro)
        VALUES (:1,:2,:3,:4,:5,:6,:7)
    """, [dados.get('transactionId',''), dados.get('client_id',''), dados.get('doc_id',''),
          dados.get('cliente_nome',''), int(dados.get('qtd',0)),
          float(dados.get('valor',0)), float(dados.get('parte_parceiro',0))])


def venda_listar(limit=200) -> list:
    return _fetch_all_dicts("SELECT * FROM vendas ORDER BY ts DESC LIMIT :1", [limit])


def venda_total_parceiros() -> float:
    rows = _run("SELECT COALESCE(SUM(parte_parceiro),0) FROM vendas")
    return float(rows[0][0]) if rows else 0.0


# ══════════════════════════════════════════════════════
# EVENTOS
# ══════════════════════════════════════════════════════

def evento_inserir(client_id, tipo, detalhes=None):
    _run("INSERT INTO eventos_bot (client_id,tipo,detalhes) VALUES (:1,:2,:3::jsonb)",
         [client_id, tipo.upper()[:40], json.dumps(detalhes or {})])


def eventos_contar_hoje(client_id) -> dict:
    agora = datetime.now(timezone.utc)
    hoje = agora.replace(hour=0,minute=0,second=0,microsecond=0)
    rows = _run("""
        SELECT tipo, COUNT(*) FROM eventos_bot
        WHERE client_id=:1 AND ts>=:2 GROUP BY tipo
    """, [client_id, hoje])
    out = {'PAGAMENTO_CONFIRMADO': 0, 'SALA_CRIADA': 0, 'GO_INICIADO': 0, 'MSG_RESPONDIDA': 0}
    for r in (rows or []):
        out[r[0]] = int(r[1])
    return out


# ══════════════════════════════════════════════════════
# CONFIG GLOBAL
# ══════════════════════════════════════════════════════

def config_get() -> dict:
    return _fetch_one_dict("SELECT * FROM config_global WHERE _id='global'") or {}


def config_set(campos: dict):
    if not campos:
        return
    sets = ', '.join(f"{k}=:{i+1}" for i, k in enumerate(campos))
    vals = list(campos.values())
    _run(f"UPDATE config_global SET {sets} WHERE _id='global'", vals)
