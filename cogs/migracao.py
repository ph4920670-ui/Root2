# cogs/migracao.py — Comando one-shot de migração MongoDB → Supabase
#
# Uso: /migrar_mongo (apenas admin)
# Roda de dentro do Discloud (que alcança MongoDB Atlas e Supabase).
# Migra: keys (saldo dos clientes), salas, pedidos PIX, guild_config,
#        lucro_config, bonus_data, botconfig, metas.

import asyncio
import logging
import os
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.database import get_db

_log = logging.getLogger("salasff.migracao")

MONGO_URI = os.environ.get(
    "MONGO_URI",
    "mongodb+srv://pedrinnight12_db_user:kitinho1210@cluster0.pde47ik.mongodb.net/salasff?retryWrites=true&w=majority"
)


def _emb(t="", c=0x5865F2, d=""):
    em = discord.Embed(title=t, color=c)
    if d:
        em.description = d
    return em


def _ts(v):
    """Converte datetime/string do Mongo para ISO string."""
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.isoformat()
    if isinstance(v, str) and v:
        return v
    return None


def _migrar_sync() -> dict:
    """Roda a migração de forma síncrona. Retorna dict com contagens."""
    from pymongo import MongoClient

    mongo = MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000)
    mdb = mongo["salasff"]
    supa = get_db()
    resultado = {}

    def _upsert(table, rows, conflict="id"):
        if not rows:
            return 0
        total = 0
        for i in range(0, len(rows), 200):
            batch = rows[i:i+200]
            supa.table(table).upsert(batch, on_conflict=conflict).execute()
            total += len(batch)
        return total

    # ── KEYS (saldo dos clientes) ──
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
    resultado["keys"] = _upsert("keys", rows)

    # ── SALAS ──
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
    resultado["salas"] = _upsert("salas", rows)

    # ── PEDIDOS PIX ──
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
    resultado["pedidos_pix"] = _upsert("pedidos_pix", rows, conflict="txid")

    # ── GUILD CONFIG ──
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
    resultado["guild_config"] = _upsert("guild_config", rows)

    # ── LUCRO CONFIG ──
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
    resultado["lucro_config"] = _upsert("lucro_config", rows, conflict="user_id")

    # ── BONUS DATA ──
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
    resultado["bonus_data"] = _upsert("bonus_data", rows, conflict="user_id")

    # ── BOTCONFIG ──
    doc = mdb["botconfig"].find_one({"_id": "main"})
    if doc:
        doc.pop("_id", None)
        supa.table("botconfig").upsert({"id": "main", "data": doc}, on_conflict="id").execute()
        resultado["botconfig"] = 1
    else:
        resultado["botconfig"] = 0

    # ── METAS ──
    rows = []
    if "metas" in mdb.list_collection_names():
        for doc in mdb["metas"].find():
            uid = str(doc.get("user_id") or doc["_id"])
            rows.append({
                "user_id":      uid,
                "alvo":         doc.get("alvo"),
                "dias":         doc.get("dias"),
                "salas_inicio": int(doc.get("salas_inicio", 0)),
                "criado_em":    _ts(doc.get("criado_em")),
                "expira_em":    _ts(doc.get("expira_em")),
            })
        resultado["metas"] = _upsert("metas", rows, conflict="user_id")
    else:
        resultado["metas"] = 0

    # ── USERS CONFIG (token mode) ──
    rows = []
    if "users_config" in mdb.list_collection_names():
        for doc in mdb["users_config"].find():
            uid = str(doc.get("user_id") or doc["_id"])
            rows.append({
                "user_id":               uid,
                "user_token":            doc.get("user_token"),
                "token_mode_ativo":      bool(doc.get("token_mode_ativo", False)),
                "token_mode_servidores": doc.get("token_mode_servidores", []),
            })
        resultado["users_config"] = _upsert("users_config", rows, conflict="user_id")
    else:
        resultado["users_config"] = 0

    # ── INVITES SYSTEM ──
    rows = []
    for col_name in ("invites_system", "invites", "convites"):
        if col_name in mdb.list_collection_names():
            for doc in mdb[col_name].find():
                rows.append({
                    "id":           str(doc.get("id") or doc["_id"]),
                    "tipo":         doc.get("tipo"),
                    "inviter_id":   doc.get("inviter_id"),
                    "guild_id":     doc.get("guild_id"),
                    "user_id":      doc.get("user_id"),
                    "codigo":       doc.get("codigo"),
                    "joined_em":    _ts(doc.get("joined_em")),
                    "valido":       bool(doc.get("valido", True)),
                    "motivo":       doc.get("motivo"),
                    "aprovado":     bool(doc.get("aprovado", False)),
                    "aprovado_em":  _ts(doc.get("aprovado_em")),
                    "saiu":         bool(doc.get("saiu", False)),
                    "motivo_saida": doc.get("motivo_saida"),
                    "rejoined_em":  _ts(doc.get("rejoined_em")),
                    "criado_em":    _ts(doc.get("criado_em")),
                })
            break
    resultado["invites_system"] = _upsert("invites_system", rows) if rows else 0

    mongo.close()
    return resultado


class MigracaoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="migrar_mongo", description="[ADMIN] Migra dados do MongoDB para o Supabase (one-shot).")
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    async def cmd_migrar(self, inter):
        if inter.user.id not in config.ADMIN_IDS:
            return await inter.response.send_message(
                embed=_emb("❌  Sem permissão.", config.COR_ERRO), ephemeral=True
            )

        await inter.response.defer(ephemeral=True)
        await inter.followup.send(
            embed=_emb("🔄  Migrando…", 0xFFD700, "Conectando ao MongoDB e copiando os dados para o Supabase.\nIsso pode levar alguns segundos."),
            ephemeral=True,
        )

        try:
            res = await asyncio.to_thread(_migrar_sync)
        except Exception as ex:
            _log.exception("[migrar_mongo] erro")
            return await inter.followup.send(
                embed=_emb("❌  Erro na migração", config.COR_ERRO, f"```{ex}```"),
                ephemeral=True,
            )

        linhas = "\n".join(f"> **{k}**: {v}" for k, v in res.items())
        em = _emb("✅  Migração concluída!", config.COR_SUCESSO, linhas)
        em.set_footer(text="Confira os dados no Table Editor do Supabase.")
        await inter.followup.send(embed=em, ephemeral=True)


async def setup(bot):
    await bot.add_cog(MigracaoCog(bot))
