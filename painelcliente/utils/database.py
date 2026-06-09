"""
Database em SQLite (aiosqlite).

Arquivo único: data.db
Tabelas: painel_config, produtos, transacoes, saldos, salas_criadas
"""

import os
import aiosqlite
from typing import Optional
from datetime import datetime

DB_PATH = os.getenv("SQLITE_PATH", "data.db")


# ─── INIT ──────────────────────────────────────────────────────────
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS painel_config (
                guild_id INTEGER PRIMARY KEY,
                titulo TEXT DEFAULT '🛍️ Painel de Compras',
                descricao TEXT DEFAULT 'Selecione um produto abaixo para comprar.',
                imagem_url TEXT DEFAULT ''
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS produtos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                nome TEXT NOT NULL,
                preco REAL NOT NULL,
                salas INTEGER NOT NULL DEFAULT 0,
                descricao TEXT DEFAULT '',
                criado_em TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS transacoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                produto_id TEXT NOT NULL,
                txid TEXT UNIQUE NOT NULL,
                valor REAL NOT NULL,
                salas INTEGER NOT NULL DEFAULT 0,
                status TEXT DEFAULT 'pendente',
                criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS saldos (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                saldo INTEGER NOT NULL DEFAULT 0,
                criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TEXT,
                PRIMARY KEY (guild_id, user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS salas_criadas (
                pedidoid TEXT PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                salaid TEXT NOT NULL,
                infinito INTEGER NOT NULL DEFAULT 0,
                ativa INTEGER NOT NULL DEFAULT 1,
                criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
                finalizado_em TEXT
            )
        """)
        # ─── Stats: total criadas pelo usuário (sempre cresce) ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_stats (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                total_criadas INTEGER NOT NULL DEFAULT 0,
                bonus_disponivel INTEGER NOT NULL DEFAULT 0,
                ultimo_bonus_em INTEGER NOT NULL DEFAULT 0,
                ja_pegou_teste INTEGER NOT NULL DEFAULT 0,
                atualizado_em TEXT,
                PRIMARY KEY (guild_id, user_id)
            )
        """)
        # ─── Ranking diário: contagem do dia atual (BRT) ────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ranking_diario (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                dia TEXT NOT NULL,
                salas_no_dia INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, dia)
            )
        """)
        # ─── Histórico de ranking premiado ──────────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ranking_premiacoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                dia TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                posicao INTEGER NOT NULL,
                salas_premio INTEGER NOT NULL,
                criado_em TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_produtos_guild ON produtos(guild_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_transacoes_user ON transacoes(guild_id, user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_salas_infinito ON salas_criadas(ativa, infinito)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ranking_dia ON ranking_diario(guild_id, dia)")

        # ─── /botconfig: logs, cargos e canais ────────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS guild_config (
                guild_id INTEGER PRIMARY KEY,
                cargo_suporte_id INTEGER,
                cargo_cliente_id INTEGER,
                categoria_tickets_id INTEGER,
                canal_logs_tickets_id INTEGER,
                canal_logs_vendas_id INTEGER,
                canal_logs_saldo_id INTEGER,
                canal_logs_teste_id INTEGER,
                canal_logs_bonus_id INTEGER,
                canal_logs_ranking_id INTEGER,
                canal_logs_compras_id INTEGER,
                preco_sala_discord REAL DEFAULT 0.07,
                plano_semanal_nome TEXT DEFAULT 'Semanal',
                plano_semanal_preco TEXT DEFAULT 'R$ 20,99',
                plano_mensal_nome TEXT DEFAULT '1 Mês',
                plano_mensal_preco TEXT DEFAULT 'R$ 60,00',
                atualizado_em TEXT
            )
        """)
        # Migrations: adiciona colunas se a tabela já existe sem elas
        for _coluna, _ddl in [
            ("preco_sala_discord", "REAL DEFAULT 0.07"),
            ("canal_logs_compras_id", "INTEGER"),
            ("cargo_cliente_id", "INTEGER"),
            ("plano_semanal_nome", "TEXT DEFAULT 'Semanal'"),
            ("plano_semanal_preco", "TEXT DEFAULT 'R$ 20,99'"),
            ("plano_mensal_nome", "TEXT DEFAULT '1 Mês'"),
            ("plano_mensal_preco", "TEXT DEFAULT 'R$ 60,00'"),
        ]:
            try:
                await db.execute(
                    f"ALTER TABLE guild_config ADD COLUMN {_coluna} {_ddl}"
                )
            except Exception:
                pass  # coluna já existe

        # ─── Config global do bot (chave-valor) ───────────────
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_config (
                chave TEXT PRIMARY KEY,
                valor TEXT
            )
        """)
        await db.commit()
    print(f"✅ SQLite conectado ({DB_PATH})")


# ─── CONFIG GLOBAL DO BOT (chave-valor) ────────────────────────────
async def get_bot_config(chave: str) -> Optional[str]:
    """Lê um valor da config global do bot."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT valor FROM bot_config WHERE chave = ?", (chave,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def set_bot_config(chave: str, valor: str) -> None:
    """Grava um valor na config global do bot."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO bot_config (chave, valor) VALUES (?, ?) "
            "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
            (chave, valor),
        )
        await db.commit()


async def get_credenciais_misticpay() -> tuple[Optional[str], Optional[str]]:
    """Retorna (client_id, client_secret) do MisticPay salvos no banco."""
    ci = await get_bot_config("misticpay_ci")
    cs = await get_bot_config("misticpay_cs")
    return ci, cs


async def set_credenciais_misticpay(ci: str, cs: str) -> None:
    """Salva as credenciais do MisticPay na config global."""
    await set_bot_config("misticpay_ci", ci)
    await set_bot_config("misticpay_cs", cs)


# ─── CONFIG DO PAINEL ──────────────────────────────────────────────
async def get_config(guild_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM painel_config WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            await db.execute(
                "INSERT INTO painel_config (guild_id) VALUES (?)", (guild_id,)
            )
            await db.commit()
            async with db.execute(
                "SELECT * FROM painel_config WHERE guild_id = ?", (guild_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return dict(row)


async def update_config(guild_id: int, **kwargs):
    if not kwargs:
        return
    await get_config(guild_id)  # garante que existe
    campos = ", ".join(f"{k} = ?" for k in kwargs.keys())
    valores = list(kwargs.values()) + [guild_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE painel_config SET {campos} WHERE guild_id = ?", valores
        )
        await db.commit()


# ─── PRODUTOS ──────────────────────────────────────────────────────
async def listar_produtos(guild_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM produtos WHERE guild_id = ? ORDER BY id ASC", (guild_id,)
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            {
                "id": str(r["id"]),
                "guild_id": r["guild_id"],
                "nome": r["nome"],
                "preco": r["preco"],
                "salas": int(r["salas"] or 0),
                "descricao": r["descricao"] or "",
            }
            for r in rows
        ]


async def get_produto(produto_id: str) -> Optional[dict]:
    try:
        pid = int(produto_id)
    except (ValueError, TypeError):
        return None
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM produtos WHERE id = ?", (pid,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "guild_id": row["guild_id"],
            "nome": row["nome"],
            "preco": row["preco"],
            "salas": int(row["salas"] or 0),
            "descricao": row["descricao"] or "",
        }


async def adicionar_produto(guild_id: int, nome: str, preco: float, salas: int = 0, descricao: str = "") -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO produtos (guild_id, nome, preco, salas, descricao) VALUES (?, ?, ?, ?, ?)",
            (guild_id, nome, preco, salas, descricao),
        )
        await db.commit()
        return str(cursor.lastrowid)


async def atualizar_produto(produto_id: str, nome: str, preco: float, salas: int = 0, descricao: str = ""):
    try:
        pid = int(produto_id)
    except (ValueError, TypeError):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE produtos SET nome = ?, preco = ?, salas = ?, descricao = ? WHERE id = ?",
            (nome, preco, salas, descricao, pid),
        )
        await db.commit()


async def remover_produto(produto_id: str):
    try:
        pid = int(produto_id)
    except (ValueError, TypeError):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM produtos WHERE id = ?", (pid,))
        await db.commit()


# ─── TRANSAÇÕES ────────────────────────────────────────────────────
async def criar_transacao(guild_id: int, user_id: int, produto_id: str, txid: str, valor: float, salas: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO transacoes (guild_id, user_id, produto_id, txid, valor, salas)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (guild_id, user_id, produto_id, txid, valor, salas),
        )
        await db.commit()


async def atualizar_status_transacao(txid: str, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE transacoes SET status = ?, atualizado_em = ? WHERE txid = ?",
            (status, datetime.utcnow().isoformat(), txid),
        )
        await db.commit()


async def get_transacao(txid: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM transacoes WHERE txid = ?", (txid,)
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None


# ─── SALDO DE USUÁRIO (em salas) ───────────────────────────────────
async def get_saldo(guild_id: int, user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT saldo FROM saldos WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row else 0


async def adicionar_saldo(guild_id: int, user_id: int, quantidade: int) -> int:
    """Adiciona N salas. Retorna o novo saldo."""
    agora = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        # UPSERT (SQLite >= 3.24)
        await db.execute(
            """INSERT INTO saldos (guild_id, user_id, saldo, atualizado_em)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(guild_id, user_id) DO UPDATE SET
                   saldo = saldo + excluded.saldo,
                   atualizado_em = excluded.atualizado_em""",
            (guild_id, user_id, quantidade, agora),
        )
        await db.commit()
        async with db.execute(
            "SELECT saldo FROM saldos WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row else 0


async def consumir_saldo(guild_id: int, user_id: int, quantidade: int = 1) -> bool:
    """Tenta descontar N salas atomicamente. Retorna True se conseguiu."""
    agora = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        # Só desconta se tiver saldo suficiente
        cursor = await db.execute(
            """UPDATE saldos
               SET saldo = saldo - ?, atualizado_em = ?
               WHERE guild_id = ? AND user_id = ? AND saldo >= ?""",
            (quantidade, agora, guild_id, user_id, quantidade),
        )
        await db.commit()
        return cursor.rowcount > 0


# ─── SALAS CRIADAS ─────────────────────────────────────────────────
async def registrar_sala(pedidoid: str, guild_id: int, user_id: int, channel_id: int,
                          message_id: int, salaid: str, infinito: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO salas_criadas
               (pedidoid, guild_id, user_id, channel_id, message_id, salaid, infinito, ativa)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
            (pedidoid, guild_id, user_id, channel_id, message_id, salaid, 1 if infinito else 0),
        )
        await db.commit()


async def get_sala_registrada(pedidoid: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM salas_criadas WHERE pedidoid = ?", (pedidoid,)
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None


async def finalizar_sala(pedidoid: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE salas_criadas SET ativa = 0, finalizado_em = ? WHERE pedidoid = ?",
            (datetime.utcnow().isoformat(), pedidoid),
        )
        await db.commit()


async def listar_salas_ativas_infinitas() -> list[dict]:
    """Pra recuperar loops infinitos após restart do bot."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM salas_criadas WHERE ativa = 1 AND infinito = 1"
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════
# STATS DE USUÁRIO (total criadas, bônus disponível, sala teste)
# ═══════════════════════════════════════════════════════════════════

BONUS_A_CADA_N_SALAS = 10
BONUS_QUANTIDADE = 2
SALAS_TESTE = 10


async def get_user_stats(guild_id: int, user_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM user_stats WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            await db.execute(
                "INSERT INTO user_stats (guild_id, user_id) VALUES (?, ?)",
                (guild_id, user_id),
            )
            await db.commit()
            return {
                "guild_id": guild_id,
                "user_id": user_id,
                "total_criadas": 0,
                "bonus_disponivel": 0,
                "ultimo_bonus_em": 0,
                "ja_pegou_teste": 0,
            }
        return dict(row)


async def registrar_sala_criada(guild_id: int, user_id: int) -> dict:
    """
    Incrementa total + ranking diário.
    Se cruzar múltiplo de 10, gera bônus de 2.
    Retorna dict com info: novo_total, ganhou_bonus (bool).
    """
    agora = datetime.utcnow().isoformat()
    dia_brt = _dia_brt()

    async with aiosqlite.connect(DB_PATH) as db:
        # Garante user_stats
        await db.execute(
            "INSERT OR IGNORE INTO user_stats (guild_id, user_id) VALUES (?, ?)",
            (guild_id, user_id),
        )
        # Incrementa total
        await db.execute(
            """UPDATE user_stats
               SET total_criadas = total_criadas + 1,
                   atualizado_em = ?
               WHERE guild_id = ? AND user_id = ?""",
            (agora, guild_id, user_id),
        )
        # Lê novo total
        async with db.execute(
            "SELECT total_criadas, ultimo_bonus_em FROM user_stats WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
        novo_total = int(row[0])
        ultimo = int(row[1])

        # Calcula quantos múltiplos de 10 ele cruzou desde o último bônus
        proximos_multiplos = (novo_total // BONUS_A_CADA_N_SALAS) - (ultimo // BONUS_A_CADA_N_SALAS)
        ganhou_bonus = proximos_multiplos > 0
        if ganhou_bonus:
            bonus_total = proximos_multiplos * BONUS_QUANTIDADE
            await db.execute(
                """UPDATE user_stats
                   SET bonus_disponivel = bonus_disponivel + ?,
                       ultimo_bonus_em = ?
                   WHERE guild_id = ? AND user_id = ?""",
                (bonus_total, novo_total, guild_id, user_id),
            )

        # Ranking diário
        await db.execute(
            """INSERT INTO ranking_diario (guild_id, user_id, dia, salas_no_dia)
               VALUES (?, ?, ?, 1)
               ON CONFLICT(guild_id, user_id, dia) DO UPDATE SET
                   salas_no_dia = salas_no_dia + 1""",
            (guild_id, user_id, dia_brt),
        )

        await db.commit()

        return {
            "novo_total": novo_total,
            "ganhou_bonus": ganhou_bonus,
            "bonus_ganho": (proximos_multiplos * BONUS_QUANTIDADE) if ganhou_bonus else 0,
        }


async def resgatar_bonus(guild_id: int, user_id: int) -> int:
    """Move o bonus_disponivel pro saldo. Retorna quantas foram resgatadas."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT bonus_disponivel FROM user_stats WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
        if not row or int(row[0]) <= 0:
            return 0
        bonus = int(row[0])

        # Zera bônus
        await db.execute(
            "UPDATE user_stats SET bonus_disponivel = 0 WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        await db.commit()

    # Adiciona ao saldo
    await adicionar_saldo(guild_id, user_id, bonus)
    return bonus


async def pegar_sala_teste(guild_id: int, user_id: int) -> tuple[bool, int]:
    """
    Marca que o usuário pegou as 10 salas teste.
    Retorna (sucesso, quantidade). Se já pegou antes, retorna (False, 0).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO user_stats (guild_id, user_id) VALUES (?, ?)",
            (guild_id, user_id),
        )
        cursor = await db.execute(
            """UPDATE user_stats
               SET ja_pegou_teste = 1
               WHERE guild_id = ? AND user_id = ? AND ja_pegou_teste = 0""",
            (guild_id, user_id),
        )
        await db.commit()
        if cursor.rowcount == 0:
            return False, 0

    # Credita saldo
    await adicionar_saldo(guild_id, user_id, SALAS_TESTE)
    return True, SALAS_TESTE


# ═══════════════════════════════════════════════════════════════════
# RANKING DIÁRIO
# ═══════════════════════════════════════════════════════════════════

def _dia_brt() -> str:
    """Retorna a data atual no fuso America/Sao_Paulo (BRT, UTC-3)."""
    from datetime import timezone, timedelta
    brt = datetime.utcnow().replace(tzinfo=timezone.utc).astimezone(
        timezone(timedelta(hours=-3))
    )
    return brt.strftime("%Y-%m-%d")


async def top_ranking_dia(guild_id: int, dia: str = None, limit: int = 10) -> list[dict]:
    if dia is None:
        dia = _dia_brt()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT user_id, salas_no_dia
               FROM ranking_diario
               WHERE guild_id = ? AND dia = ?
               ORDER BY salas_no_dia DESC
               LIMIT ?""",
            (guild_id, dia, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def listar_guilds_com_ranking(dia: str = None) -> list[int]:
    """Lista guild_ids que tiveram atividade no dia (pra premiar)."""
    if dia is None:
        dia = _dia_brt()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT DISTINCT guild_id FROM ranking_diario WHERE dia = ?",
            (dia,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [r[0] for r in rows]


async def registrar_premiacao(guild_id: int, dia: str, user_id: int, posicao: int, salas: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO ranking_premiacoes (guild_id, dia, user_id, posicao, salas_premio)
               VALUES (?, ?, ?, ?, ?)""",
            (guild_id, dia, user_id, posicao, salas),
        )
        await db.commit()


async def ja_premiou_dia(guild_id: int, dia: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM ranking_premiacoes WHERE guild_id = ? AND dia = ? LIMIT 1",
            (guild_id, dia),
        ) as cursor:
            row = await cursor.fetchone()
        return row is not None


# ═══════════════════════════════════════════════════════════════════
# GUILD CONFIG (logs, cargos, canais — /botconfig)
# ═══════════════════════════════════════════════════════════════════

CAMPOS_GUILD_CONFIG = [
    "cargo_suporte_id",
    "cargo_cliente_id",
    "categoria_tickets_id",
    "canal_logs_tickets_id",
    "canal_logs_vendas_id",
    "canal_logs_saldo_id",
    "canal_logs_teste_id",
    "canal_logs_bonus_id",
    "canal_logs_ranking_id",
    "canal_logs_compras_id",
    "preco_sala_discord",
    "plano_semanal_nome",
    "plano_semanal_preco",
    "plano_mensal_nome",
    "plano_mensal_preco",
]


async def get_guild_config(guild_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            await db.execute(
                "INSERT INTO guild_config (guild_id) VALUES (?)", (guild_id,)
            )
            await db.commit()
            async with db.execute(
                "SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return dict(row)


async def update_guild_config(guild_id: int, **kwargs):
    if not kwargs:
        return
    # Filtra apenas campos válidos
    kwargs = {k: v for k, v in kwargs.items() if k in CAMPOS_GUILD_CONFIG}
    if not kwargs:
        return
    await get_guild_config(guild_id)
    kwargs["atualizado_em"] = datetime.utcnow().isoformat()
    campos = ", ".join(f"{k} = ?" for k in kwargs.keys())
    valores = list(kwargs.values()) + [guild_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE guild_config SET {campos} WHERE guild_id = ?", valores
        )
        await db.commit()


# ─── Histórico de compras (pro /perfil) ───────────────────────────

async def ultimas_transacoes(guild_id: int, user_id: int, limit: int = 5) -> list[dict]:
    """Últimas transações CONFIRMADAS do usuário, mais recente primeiro."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT id, produto_id, txid, valor, salas, status, criado_em
               FROM transacoes
               WHERE guild_id = ? AND user_id = ? AND status = 'pago'
               ORDER BY criado_em DESC
               LIMIT ?""",
            (guild_id, user_id, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
