#!/usr/bin/env python3
"""
migrate_mongo_to_supabase.py — Migra dados do MongoDB para o Supabase.

Uso:
  pip install pymongo supabase
  SUPABASE_URL=https://xxx.supabase.co SUPABASE_KEY=service_role_key python migrate_mongo_to_supabase.py

Migra:
  - keys          → saldo dos clientes (mais importante)
  - salas         → histórico de salas criadas
  - pedidos_pix   → histórico de compras
  - guild_config  → config dos servidores
  - lucro_config  → config de lucro/go/senha por usuário
  - bonus_data    → dados de bônus
  - botconfig     → config do bot
"""

import os
import sys
from datetime import datetime, timezone

# ─── Conexão MongoDB ───────────────────────────────────────────────────────────
MONGO_URI = os.environ.get(
    "MONGO_URI",
    "mongodb+srv://pedrinnight12_db_user:kitinho1210@cluster0.pde47ik.mongodb.net/salasff?retryWrites=true&w=majority"
)

# ─── Conexão Supabase ──────────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ Defina SUPABASE_URL e SUPABASE_KEY como variáveis de ambiente.")
    sys.exit(1)

from pymongo import MongoClient
from supabase import create_client

print("🔌 Conectando ao MongoDB...")
mongo = MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000)
mdb = mongo["salasff"]

print("🔌 Conectando ao Supabase...")
supa = create_client(SUPABASE_URL, SUPABASE_KEY)
print("✅ Conectado.\n")


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _ts(v) -> str | None:
    """Converte datetime/string do Mongo para ISO string UTC."""
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.isoformat()
    if isinstance(v, str) and v:
        return v
    return None

def _upsert(table: str, rows: list[dict], conflict_col: str = "id"):
    """Insere em lotes de 200, ignorando conflitos."""
    if not rows:
        return 0
    total = 0
    for i in range(0, len(rows), 200):
        batch = rows[i:i+200]
        supa.table(table).upsert(batch, on_conflict=conflict_col).execute()
        total += len(batch)
    return total


# ─── 1. KEYS (saldo dos clientes) ─────────────────────────────────────────────

print("📦 Migrando keys (saldo dos clientes)...")
rows = []
for doc in mdb["keys"].find():
    rows.append({
        "id":           str(doc.get("id") or doc["_id"]),
        "code":         doc.get("code", ""),
        "quantia":      int(doc.get("quantia", 0)),
        "salas_usadas": int(doc.get("salas_usadas", 0)),
        "modo":         int(doc.get("modo", 0)),
        "user_id":      doc.get("user_id") or doc.get("dono_id"),
        "user_nome":    doc.get("user_nome") or doc.get("dono_nome"),
        "dono_id":      doc.get("dono_id") or doc.get("user_id"),
        "dono_nome":    doc.get("dono_nome") or doc.get("user_nome"),
        "origem":       doc.get("origem", "venda"),
        "criado_por":   doc.get("criado_por"),
        "criado_em":    _ts(doc.get("criado_em")),
        "resgatado_em": _ts(doc.get("resgatado_em")),
        "usado_em":     _ts(doc.get("usado_em")),
    })
n = _upsert("keys", rows)
print(f"   ✅ {n} keys migradas")


# ─── 2. SALAS (histórico) ─────────────────────────────────────────────────────

print("📦 Migrando salas...")
rows = []
for doc in mdb["salas"].find():
    rows.append({
        "id":           str(doc.get("id") or doc["_id"]),
        "user_id":      doc.get("user_id"),
        "user_nome":    doc.get("user_nome"),
        "modo":         doc.get("modo"),
        "guild_id":     doc.get("guild_id"),
        "saldo_origem": doc.get("saldo_origem") or doc.get("origem", "pessoal"),
        "pedidoid":     doc.get("pedidoid") or doc.get("pedido_id"),
        "sala_id":      doc.get("sala_id"),
        "sala_senha":   doc.get("sala_senha") or doc.get("senha"),
        "sala_nome":    doc.get("sala_nome") or doc.get("nome_sala"),
        "sala_link":    doc.get("sala_link") or doc.get("link"),
        "criado_em":    _ts(doc.get("criado_em")),
        "atualizado_em":_ts(doc.get("atualizado_em")),
    })
n = _upsert("salas", rows)
print(f"   ✅ {n} salas migradas")


# ─── 3. PEDIDOS PIX ───────────────────────────────────────────────────────────

print("📦 Migrando pedidos PIX...")
rows = []
for doc in mdb["pedidos_pix"].find():
    txid = doc.get("txid", "")
    if not txid:
        continue
    rows.append({
        "id":          str(doc.get("id") or doc["_id"]),
        "txid":        txid,
        "user_id":     doc.get("user_id"),
        "user_nome":   doc.get("user_nome"),
        "quantia":     doc.get("quantia") or doc.get("quantidade"),
        "valor":       float(doc.get("valor", 0)),
        "banco":       doc.get("banco", "mistic"),
        "status":      doc.get("status", "pendente"),
        "guild_id":    doc.get("guild_id"),
        "guild_nome":  doc.get("guild_nome"),
        "key_gerada":  doc.get("key_gerada"),
        "nome_pagador":doc.get("nome_pagador"),
        "endtoend":    doc.get("endtoend"),
        "criado_em":   _ts(doc.get("criado_em")),
        "pago_em":     _ts(doc.get("pago_em")),
    })
n = _upsert("pedidos_pix", rows, conflict_col="txid")
print(f"   ✅ {n} pedidos migrados")


# ─── 4. GUILD CONFIG ──────────────────────────────────────────────────────────

print("📦 Migrando guild_config...")
rows = []
for doc in mdb["guild_config"].find():
    gid = str(doc.get("guild_id") or doc["_id"])
    rows.append({
        "id":               gid,
        "saldo":            int(doc.get("saldo", 0)),
        "cargo_sala_id":    doc.get("cargo_sala_id") or doc.get("cargo_id"),
        "cargo_cliente_id": doc.get("cargo_cliente_id") or doc.get("cargo_saldo_id"),
        "canal_compras_id": doc.get("canal_compras_id"),
        "cargos_por_qtd":   doc.get("cargos_por_qtd", {}),
        "avaliacao":        doc.get("avaliacao", {}),
        "chat_cmd":         doc.get("chat_cmd", {}),
        "pix_creds":        doc.get("pix_creds", {}),
        "criado_em":        _ts(doc.get("criado_em")),
    })
n = _upsert("guild_config", rows)
print(f"   ✅ {n} servidores migrados")


# ─── 5. LUCRO CONFIG (go, senha, orgs por usuário) ────────────────────────────

print("📦 Migrando lucro_config...")
rows = []
for doc in mdb["lucro_config"].find():
    uid = str(doc.get("user_id") or doc["_id"])
    rows.append({
        "user_id":       uid,
        "valor_por_sala":float(doc.get("valor_por_sala", 0)),
        "go_tempo":      int(doc.get("go_tempo", 0)),
        "sala_senha":    doc.get("sala_senha"),
        "orgs":          doc.get("orgs", []),
    })
n = _upsert("lucro_config", rows, conflict_col="user_id")
print(f"   ✅ {n} lucro_configs migrados")


# ─── 6. BONUS DATA ────────────────────────────────────────────────────────────

print("📦 Migrando bonus_data...")
rows = []
for doc in mdb["bonus_data"].find():
    uid = str(doc.get("user_id") or doc["_id"])
    rows.append({
        "user_id":         uid,
        "user_nome":       doc.get("user_nome"),
        "total_comprado":  int(doc.get("total_comprado", 0)),
        "bonus_resgatado": int(doc.get("bonus_resgatado", 0)),
        "historico":       doc.get("historico", []),
    })
n = _upsert("bonus_data", rows, conflict_col="user_id")
print(f"   ✅ {n} bonus migrados")


# ─── 7. BOTCONFIG ─────────────────────────────────────────────────────────────

print("📦 Migrando botconfig...")
doc = mdb["botconfig"].find_one({"_id": "main"})
if doc:
    doc.pop("_id", None)
    supa.table("botconfig").upsert({"id": "main", "data": doc}, on_conflict="id").execute()
    print("   ✅ botconfig migrado")
else:
    print("   ⚠️  botconfig não encontrado no MongoDB")


# ─── 8. METAS ────────────────────────────────────────────────────────────────

print("📦 Migrando metas...")
rows = []
col_metas = mdb.get_collection("metas") if "metas" in mdb.list_collection_names() else None
if col_metas:
    for doc in col_metas.find():
        uid = str(doc.get("user_id") or doc["_id"])
        rows.append({
            "user_id":      uid,
            "alvo":         doc.get("alvo"),
            "dias":         doc.get("dias"),
            "salas_inicio": int(doc.get("salas_inicio", 0)),
            "criado_em":    _ts(doc.get("criado_em")),
            "expira_em":    _ts(doc.get("expira_em")),
        })
    n = _upsert("metas", rows, conflict_col="user_id")
    print(f"   ✅ {n} metas migradas")
else:
    print("   ⚠️  collection 'metas' não existe — pulando")


# ─── Resumo ───────────────────────────────────────────────────────────────────
print("\n✅ Migração concluída!")
print("   Verifique os dados no Table Editor do Supabase antes de trocar o bot.")
