import os
import re
import imaplib
import email
import unicodedata
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
import requests
from flask import Flask, render_template, request, jsonify, session, g
from pymongo import MongoClient
from bson import ObjectId

# Carrega o .env pras variáveis (TURBOFY_*, SUPABASE, etc.) ficarem disponíveis
# via os.getenv ao rodar localmente ou onde o host não injeta o .env sozinho.
# Sem isso, a Wallet2 reclamava "Wallet2 não configurada" mesmo com o .env certo.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'painel-secret-2025')

# Sessão persistente e compatível atrás do proxy HTTPS do Discloud. Sem isso,
# alguns navegadores descartam o cookie de login entre uma request e outra
# (o login "dá OK" no servidor mas a próxima request volta 401 e joga de volta
# pra tela de login — exatamente o "fico clicando e não vai").
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# A página roda em HTTPS (Discloud). Sem o flag Secure, vários webviews iOS
# (in-app browser, WKWebView) DESCARTAM o cookie de sessão numa página segura —
# o login "dá OK" e a request seguinte volta 401 → cai de novo na tela de login.
# Com Secure=True o cookie é aceito e a sessão persiste.
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True

@app.before_request
def _sessao_permanente():
    session.permanent = True

# ─────────────────────────────────────────────────────────────────────────
# AUTH POR TOKEN (fallback pra webview/in-app browser que bloqueia cookie)
# Alguns navegadores in-app (ex.: o de dentro do Discord) NÃO persistem o
# cookie de sessão — o login dá OK e a próxima request volta 401. Pra esses,
# emitimos no /login um token assinado que o front guarda no localStorage e
# manda no header 'X-Auth-Token' de TODA request. Aqui, se não houver sessão
# por cookie, reconstruímos a sessão a partir do token. Não depende de cookie.
# ─────────────────────────────────────────────────────────────────────────
import hmac as _hmac, hashlib as _hashlib, base64 as _b64, json as _json
_AUTH_KEYS = ('logado', 'usuario', 'is_admin', 'is_panel_admin', 'is_partner',
              'partner_key', 'client_id', 'doc_id', 'rev_id')

def _b64e(b):
    return _b64.urlsafe_b64encode(b).decode().rstrip('=')

def _b64d(s):
    return _b64.urlsafe_b64decode(s + ('=' * (-len(s) % 4)))

def _fazer_auth_token():
    """Token assinado (HMAC-SHA256, só stdlib) com os campos de sessão."""
    raw = _json.dumps({k: session.get(k) for k in _AUTH_KEYS}, separators=(',', ':')).encode()
    body = _b64e(raw)
    sig = _hmac.new(app.secret_key.encode(), body.encode(), _hashlib.sha256).digest()
    return body + '.' + _b64e(sig)

def _ler_auth_token(tok):
    try:
        body, sig = tok.split('.', 1)
        esperado = _b64e(_hmac.new(app.secret_key.encode(), body.encode(), _hashlib.sha256).digest())
        if not _hmac.compare_digest(sig, esperado):
            return None
        return _json.loads(_b64d(body))
    except Exception:
        return None

def _resp_login(extra=None):
    body = {'ok': True, 'auth_token': _fazer_auth_token()}
    if extra:
        body.update(extra)
    return jsonify(body)

@app.before_request
def _auth_por_token():
    # Já logado por cookie? então nada a fazer.
    if session.get('logado'):
        return
    tok = request.headers.get('X-Auth-Token') or request.args.get('_t')
    if not tok:
        return
    data = _ler_auth_token(tok)
    if isinstance(data, dict) and data.get('logado'):
        for k in _AUTH_KEYS:
            if k in data:
                session[k] = data[k]

import traceback
import threading
from werkzeug.exceptions import HTTPException

@app.errorhandler(Exception)
def handle_exception(e):
    # Erros HTTP (404, 401, 405...) NÃO são bug do servidor — devolve o status
    # certo, sem virar 500 nem cuspir traceback. No caso de 404, loga só uma
    # linha com o método + URL pra dar pra ver quem está chamando rota que não
    # existe (a causa do log lotado).
    if isinstance(e, HTTPException):
        if e.code == 404:
            try:
                print(f"[404] {request.method} {request.path} (ref: {request.referrer or '-'})", flush=True)
            except Exception:
                pass
        # Sempre JSON (o front faz r.json() em tudo) — só muda o status code.
        return jsonify({'ok': False, 'msg': getattr(e, 'description', None) or e.name}), (e.code or 500)
    tb = traceback.format_exc()
    print(f"[ERRO]\n{tb}", flush=True)
    return jsonify({'ok': False, 'msg': f'{type(e).__name__}: {str(e)}'}), 500

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb+srv://ederleonardo2025xd_db_user:qZyN4KTER5uj7nhm@cluster0.7jvjudr.mongodb.net/')
DISCLOUD_TOKEN = os.environ.get('DISCLOUD_TOKEN', 'eyJhbGciOiJIUzI1NiJ9.eyJpZCI6IjEyNjgzNzkxNjc1MTk0MDgxMzkiLCJrZXkiOiJiMTI3OWFmNTQyYzcyZTFmOWQ5MzY5Zjc4OGJkIn0.D2SqVOnr8-xiC1tQ9jTrF6davJuZFcH7S_Gbth1rRxY')
DISCLOUD_API_URL = 'https://api.discloud.app/v2'
DISCLOUD_HEADERS = {'api-token': DISCLOUD_TOKEN}

# PROVEDOR DE DEPLOY — onde os bots dos clientes rodam. Discloud (padrão) OU
# SquareCloud (instância Square). No Square, status/ações/logs do bot têm que ir
# pra API da SquareCloud — senão o painel consulta a Discloud, não acha o app, e
# mostra "offline" mesmo o bot estando ON (bug relatado).
PROVEDOR_DEPLOY = (os.environ.get('PROVEDOR_DEPLOY', 'discloud') or 'discloud').strip().lower()
SQUARE_API_URL = 'https://api.squarecloud.app/v2'
SQUARE_API_KEY = (os.environ.get('SQUARE_API_KEY', '') or '').strip()


def _usar_square():
    return PROVEDOR_DEPLOY == 'square'


def _square_headers():
    return {'Authorization': SQUARE_API_KEY}


def _chk_square(app_id):
    """Status do app na SquareCloud numa chamada leve. 'online'/'offline'/'checando'.
    'checando' = não deu pra confirmar (sem key, timeout, erro)."""
    if not SQUARE_API_KEY:
        return 'checando'
    try:
        r = requests.get(f'{SQUARE_API_URL}/apps/{app_id}/status',
                         headers=_square_headers(), timeout=6)
        d = r.json()
        if d.get('status') == 'success':
            return 'online' if (d.get('response') or {}).get('running') else 'offline'
    except Exception:
        return 'checando'
    return 'checando'


def _online_de_apps(apps):
    """Decide online/offline/checando a partir do bloco 'apps' do status Discloud.
    Só diz 'offline' com sinal EXPLÍCITO de parado — evita falso-off."""
    if not isinstance(apps, dict):
        return 'checando'
    c = str(apps.get('container') or '').lower()
    if any(k in c for k in ('offline', 'stopped', 'exited', 'crashed', 'dead', 'parado', 'desligado')):
        return 'offline'
    if any(k in c for k in ('online', 'running', 'started', 'starting', 'ativo', 'rodando', 'up')):
        return 'online'
    if apps.get('online') is True:
        return 'online'
    try:
        import re as _re
        nums = _re.findall(r'(\d+(?:\.\d+)?)', str(apps.get('memory') or apps.get('ram') or ''))
        if nums and float(nums[0]) > 0:
            return 'online'  # consumindo RAM = vivo
    except Exception:
        pass
    return 'checando'  # app existe mas sem sinal claro → não afirma off


def _chk_discloud(app_id):
    """Status do app na Discloud numa chamada leve. 'online'/'offline'/'checando'.
    ROBUSTO: só afirma 'offline' se o container disser EXPLICITAMENTE que parou.
    Container desconhecido + app existindo = trata como vivo (evita falso-off)."""
    if not (DISCLOUD_HEADERS or {}).get('api-token'):
        return 'checando'
    try:
        r = requests.get(f'{DISCLOUD_API_URL}/app/{app_id}/status',
                         headers=DISCLOUD_HEADERS, timeout=10)
        d = r.json()
        if d.get('status') == 'ok':
            apps = d.get('apps', {}) or {}
            return _online_de_apps(apps)
    except Exception:
        return 'checando'
    return 'checando'


def _status_simples(app_id):
    """Status do bot em chamadas leves (sem logs). Devolve:
      'online'   → bot rodando (verde)
      'offline'  → bot parado/não existe (vermelho)
      'checando' → não deu pra confirmar agora (amarelo: timeout/erro/sem app_id)
    Usado no GATE de login: só bloqueia quando é 'offline' (verde e amarelo passam).

    IMPORTANTE: consulta os DOIS provedores (Square + Discloud). O bot do cliente
    pode estar hospedado em qualquer um — checar só o PROVEDOR_DEPLOY global
    fazia o painel mostrar 'offline'/'off' pra bot que estava ON no outro
    provedor. A ordem começa pelo provedor configurado (mais provável)."""
    app_id = (app_id or '').strip()
    if not app_id:
        return 'offline'
    checks = ([_chk_square, _chk_discloud] if _usar_square()
              else [_chk_discloud, _chk_square])
    visto_offline = False
    for fn in checks:
        r = fn(app_id)
        if r == 'online':
            return 'online'      # achou rodando em algum provedor → verde
        if r == 'offline':
            visto_offline = True
    # Só 'offline' se ALGUM provedor confirmou parado e nenhum disse online.
    # Se os dois deram 'checando' (timeout/sem credencial), fica amarelo.
    return 'offline' if visto_offline else 'checando'


ADMIN_ID = 'phadm'
ADMIN_PASS = '22'
PANEL_ADMIN_USER = os.environ.get('PANEL_ADMIN_USER', 'admin').strip()
PANEL_ADMIN_PASS = os.environ.get('PANEL_ADMIN_PASS', '12345678910').strip()
ENV_CLIENT_ID = os.environ.get('CLIENT_ID', '').strip()

# Parceiros (dividem 50% cada de toda venda PIX nova). Login direto, hardcoded.
PARTNERS = {
    'shadow': {'pass': '2233', 'nome': 'Shadow'},
    'snoop': {'pass': '22', 'nome': 'Snoop'},
}
# % do valor de cada venda que vai pra CADA parceiro (50/50)
PARTNER_SPLIT = 0.5
# Saque minimo em R$ (Cash-Out PIX da MisticPay nao tem minimo oficial, mas
# evita saques ridiculos de centavos).
PARTNER_SAQUE_MIN = float(os.environ.get('PARTNER_SAQUE_MIN', '1').strip() or '1')
# Minimo de salas por compra PIX
PIX_MIN_SALAS = int(os.environ.get('PIX_MIN_SALAS', '20').strip() or '20')

# URL + secret do configurador admin. Usado pelo /api/salvar-campo pra disparar
# /bot/aplicar no admin (refaz .env + reinicia bot) após o cliente salvar.
# Mesmos defaults dos outros apps — em produção vem por env.
CONFIG_URL = os.environ.get('CONFIG_URL', 'https://configadm.discloud.app').rstrip('/')
BOT_FETCH_SECRET = os.environ.get('BOT_FETCH_SECRET', 'secret-bot-fetch-2026')

mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
db = mongo_client['painel_bots']
# Banco do admin (sistema). Aqui moram: clientes do admin, revendedores,
# index de roteamento, config_global, pagamentos/vendas/saques/parceiros etc.
clientes_col_admin = db['clientes']
# Alias legado pra não quebrar referências antigas — vira o ROUTER ao final.
clientes_col = clientes_col_admin
# Coleção 'consumo' — registra cada queda no saldo_salas do cliente. Cada doc:
#   {client_id, doc_id, qtd: int, lucro_unit: float, ts: datetime UTC}
# Usado pelo /api/lucro pra calcular ganho por período (hoje/ontem/semana/mês/total).
# Detecção da queda acontece no /api/dados (compara saldo atual vs último).
consumo_col = db['consumo']
config_col = db['config_global']
pagamentos_col = db['pagamentos']
# Saldo e historico dos parceiros (Shadow/Snoop)
parceiros_col = db['parceiros']
saques_col = db['saques']
# Log de vendas PIX (1 doc por pagamento confirmado, com valor R$)
vendas_col = db['vendas']
# Eventos operacionais reportados pelos bots — usado pra contadores "hoje" no dashboard.
# Coleção populada pelo endpoint /bot/evento do configadm.
eventos_col = db['eventos_bot']
# REVENDA: index de roteamento (mantido pelo configadm) e doc dos revendedores.
# Painel-clientej usa pra achar em qual banco mora o cliente logado.
client_index_col = db['client_index']
revendedores_col = db['revendedores']
try:
    consumo_col.create_index([('doc_id', 1), ('ts', -1)])
    consumo_col.create_index([('client_id', 1), ('ts', -1)])
    pagamentos_col.create_index([('transactionId', 1)])
    saques_col.create_index([('parceiro', 1), ('ts', -1)])
    vendas_col.create_index([('ts', -1)])
    # Índice composto (client_id, ts) que _contagem_eventos_hoje exige. Sem
    # ele, o aggregate do dashboard faz collection scan na eventos_bot inteira
    # (que os bots populam o tempo todo) — principal causa do painel lento.
    eventos_col.create_index([('client_id', 1), ('ts', -1)])
except Exception as e:
    print(f"[WARN] Falha ao criar indices: {e}", flush=True)


# ═══════════════════════════════════════════════════════════════════════
# REVENDA — ROTEAMENTO MULTI-TENANT PRO PAINEL-CLIENTEJ
# ═══════════════════════════════════════════════════════════════════════
# Cada revendedor TEM um banco próprio (Mongo ou Supabase). Os clientes dele
# moram lá. O painel-clientej resolve o banco certo pelo client_id usando o
# `client_index_col` (no banco do admin), que o configadm mantém em sincronia
# em todo create/update/delete de cliente.
#
# Cliente do admin: revendedor_id vazio no index → usa `clientes_col_admin`.
# Cliente de revendedor: connecta no banco dele (cached).
#
# Pro Supabase tem o mesmo adaptador do configadm — só com leitura+update
# (escrita simples), suficiente pra todas operações do painel.
# ═══════════════════════════════════════════════════════════════════════
import threading
_db_conn_cache = {}
_db_conn_lock = threading.Lock()


def _doc_match(doc, filt):
    """Avalia um filtro estilo-Mongo (subset usado pelo painel)."""
    for k, v in (filt or {}).items():
        actual = doc.get(k)
        if isinstance(v, dict):
            if '$ne' in v and actual == v['$ne']:
                return False
            if '$exists' in v and (k in doc) != bool(v['$exists']):
                return False
            if '$regex' in v:
                import re as _re_local
                pat = v['$regex']
                flags = 0
                if 'i' in (v.get('$options') or ''):
                    flags |= _re_local.IGNORECASE
                if not _re_local.search(pat, str(actual or ''), flags):
                    return False
        elif k == '_id':
            if str(actual) != str(v):
                return False
        elif actual != v:
            return False
    return True


def _doc_project(doc, projection):
    if not projection:
        return doc
    incl = {k for k, val in projection.items() if val and k != '_id'}
    if incl:
        keep = set(incl)
        if projection.get('_id', 1):
            keep.add('_id')
        return {k: val for k, val in doc.items() if k in keep}
    excl = {k for k, val in projection.items() if not val}
    return {k: val for k, val in doc.items() if k not in excl}


class _ResultObj:
    def __init__(self, **kw):
        for k, val in kw.items():
            setattr(self, k, val)


class SupabaseClientesReader:
    """Adaptador read-mostly pro painel-clientej falar com tabela `clientes`
    no Supabase do revendedor. Suporta find/find_one/update_one ($set/$inc/$unset).
    Mesma lógica do configadm — só o subset que o painel usa."""

    def __init__(self, url, key):
        self.base = url.rstrip('/') + '/rest/v1/clientes'
        self.h = {'apikey': key, 'Authorization': 'Bearer ' + key,
                  'Content-Type': 'application/json'}

    def _linhas(self):
        r = requests.get(self.base, params={'select': '*'}, headers=self.h, timeout=15)
        r.raise_for_status()
        out = []
        for row in r.json():
            doc = dict(row.get('data') or {})
            try:
                doc['_id'] = ObjectId(row['_id'])
            except Exception:
                doc['_id'] = row['_id']
            out.append(doc)
        return out

    def find(self, filtro=None, projection=None):
        docs = [d for d in self._linhas() if _doc_match(d, filtro or {})]
        if projection:
            docs = [_doc_project(d, projection) for d in docs]
        return docs

    def find_one(self, filtro=None, projection=None):
        for d in self._linhas():
            if _doc_match(d, filtro or {}):
                return _doc_project(d, projection) if projection else d
        return None

    def _patch(self, hexid, data):
        r = requests.patch(self.base, params={'_id': 'eq.' + hexid},
                           headers={**self.h, 'Prefer': 'return=minimal'},
                           json={'data': data}, timeout=15)
        r.raise_for_status()

    def update_one(self, filtro, update, upsert=False):
        alvo = None
        for d in self._linhas():
            if _doc_match(d, filtro or {}):
                alvo = d
                break
        if alvo is None:
            return _ResultObj(matched_count=0, modified_count=0)
        data = {k: v for k, v in alvo.items() if k != '_id'}
        for k, v in (update.get('$set') or {}).items():
            data[k] = v
        for k, v in (update.get('$inc') or {}).items():
            data[k] = (data.get(k) or 0) + v
        for k in (update.get('$unset') or {}).keys():
            data.pop(k, None)
        self._patch(str(alvo['_id']), data)
        return _ResultObj(matched_count=1, modified_count=1)


def _json_safe(v):
    """Converte datetime → ISO recursivamente (jsonb não tem datetime nativo)."""
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    return v


class _SupaCursor:
    """Imita o cursor do pymongo (suporta .sort().limit()) sobre uma lista."""
    def __init__(self, docs):
        self._docs = list(docs)
    def sort(self, field, direction=1):
        self._docs.sort(key=lambda d: (d.get(field) is None, d.get(field)),
                        reverse=(direction < 0))
        return self
    def limit(self, n):
        if n:
            self._docs = self._docs[:int(n)]
        return self
    def __iter__(self):
        return iter(self._docs)
    def __len__(self):
        return len(self._docs)


class SupabaseAnalyticsCol:
    """Adaptador Supabase pras coleções de analytics do painel (consumo). Estrutura
    _id/data jsonb, igual o resto. Suporta insert_one (serializa datetime), find
    (filtro por igualdade server-side + cursor com sort/limit) e find_one. NÃO faz
    aggregate — as funções de lucro agregam em Python quando a coleção é Supabase."""
    def __init__(self, url, key, tabela):
        self.base = url.rstrip('/') + '/rest/v1/' + tabela
        self.h = {'apikey': key, 'Authorization': 'Bearer ' + key,
                  'Content-Type': 'application/json'}

    def create_index(self, *a, **k):
        return None

    def insert_one(self, doc):
        d = dict(doc)
        _id = d.pop('_id', None)
        _id = str(_id) if _id else str(ObjectId())
        try:
            r = requests.post(self.base, headers={**self.h, 'Prefer': 'return=minimal'},
                              json={'_id': _id, 'data': _json_safe(d)}, timeout=15)
            r.raise_for_status()
        except Exception as e:
            print(f"[SUPA-ANALYTICS] insert falhou: {e}", flush=True)
        return _ResultObj(inserted_id=_id)

    def _linhas(self, params=None):
        r = requests.get(self.base, params={'select': '*', **(params or {})},
                         headers=self.h, timeout=20)
        r.raise_for_status()
        out = []
        for row in r.json():
            d = dict(row.get('data') or {})
            d['_id'] = row['_id']
            out.append(d)
        return out

    def find(self, filtro=None, projection=None):
        params, mem = {}, {}
        opmap = {'$gte': 'gte', '$gt': 'gt', '$lte': 'lte', '$lt': 'lt', '$ne': 'neq', '$eq': 'eq'}
        for k, v in (filtro or {}).items():
            if k != '_id' and isinstance(v, (str, int, float, bool)):
                params[f'data->>{k}'] = f'eq.{v}'  # filtra no servidor
            elif k != '_id' and isinstance(v, dict) and all(op in opmap for op in v):
                for op, val in v.items():
                    sval = val.isoformat() if isinstance(val, datetime) else val
                    params[f'data->>{k}'] = f'{opmap[op]}.{sval}'  # range no servidor
            else:
                mem[k] = v
        try:
            rows = self._linhas(params)
        except Exception as e:
            print(f"[SUPA-ANALYTICS] find falhou: {e}", flush=True)
            rows = []
        if mem:
            rows = [d for d in rows if _doc_match(d, mem)]
        return _SupaCursor(rows)

    def find_one(self, filtro=None, projection=None):
        for d in self.find(filtro):
            return d
        return None


# ─────────── SQUARE: clientes/índice/revendedores no SUPABASE ───────────
# Quando DB_TIPO=supabase (instância isolada do Square), o painel lê/grava os
# CLIENTES, o ÍNDICE de login e os REVENDEDORES no Supabase do Square — em vez do
# Mongo. As coleções de ANALYTICS (consumo/eventos/vendas/pagamentos) seguem no
# Mongo (lucro/dashboard), indexadas por doc_id, sem colidir com a instância
# Discloud. Assim login e edição de config do cliente do Square funcionam no banco
# isolado, reaproveitando o mesmo SupabaseClientesReader.
_DB_TIPO = (os.environ.get('DB_TIPO', 'mongo') or 'mongo').strip().lower()
_DB_SUPABASE_URL = (os.environ.get('DB_SUPABASE_URL', '') or '').strip()
_DB_SUPABASE_KEY = (os.environ.get('DB_SUPABASE_KEY', '') or '').strip()


def _mk_supabase_reader(tabela):
    ad = SupabaseClientesReader(_DB_SUPABASE_URL, _DB_SUPABASE_KEY)
    ad.base = _DB_SUPABASE_URL.rstrip('/') + '/rest/v1/' + tabela
    return ad


if _DB_TIPO == 'supabase' and _DB_SUPABASE_URL and _DB_SUPABASE_KEY:
    try:
        clientes_col_admin = _mk_supabase_reader('clientes')
        client_index_col = _mk_supabase_reader('client_index')
        revendedores_col = _mk_supabase_reader('revendedores')
        config_col = _mk_supabase_reader('config_global')
        # Analytics do LUCRO (consumo) também no Supabase — o painel escreve e lê,
        # então fica self-contained (lucro/salas/série/meta no banco isolado).
        consumo_col = SupabaseAnalyticsCol(_DB_SUPABASE_URL, _DB_SUPABASE_KEY, 'consumo')
        eventos_col = SupabaseAnalyticsCol(_DB_SUPABASE_URL, _DB_SUPABASE_KEY, 'eventos_bot')
        print(f"[DB] 🟢 painel no SUPABASE (clientes/index/revendedores/consumo/eventos) — "
              f"isolado. {_DB_SUPABASE_URL[:40]}...", flush=True)
    except Exception as _e_sb:
        print(f"[DB] ⚠️ falha ao ativar Supabase no painel, seguindo no Mongo: {_e_sb}", flush=True)


def _col_revendedor(rev):
    """Coleção de clientes do banco do revendedor. None se não configurado."""
    if not rev:
        return None
    t = rev.get('db_tipo')
    if t == 'mongo':
        uri = (rev.get('db_mongo_uri') or '').strip()
        if not uri:
            return None
        rid = str(rev['_id'])
        with _db_conn_lock:
            cached = _db_conn_cache.get(rid)
            if cached and cached.get('key') == ('mongo|' + uri):
                return cached['col']
            try:
                cli = MongoClient(uri, serverSelectionTimeoutMS=5000)
                col = cli['painel_revenda']['clientes']
                _db_conn_cache[rid] = {'key': 'mongo|' + uri, 'col': col}
                return col
            except Exception as e:
                print(f"[REV-DB] falha conectando mongo do rev {rid}: {e}", flush=True)
                return None
    if t == 'supabase':
        url = (rev.get('db_supabase_url') or '').strip()
        key = (rev.get('db_supabase_key') or '').strip()
        if not (url and key):
            return None
        rid = str(rev['_id'])
        ck = 'supabase|' + url + '|' + key[:12]  # cache key sem expor a key inteira
        with _db_conn_lock:
            cached = _db_conn_cache.get(rid)
            if cached and cached.get('key') == ck:
                return cached['col']
            col = SupabaseClientesReader(url, key)
            _db_conn_cache[rid] = {'key': ck, 'col': col}
            return col
    return None


def _col_para_client_id(client_id):
    """Resolve a coleção certa pelo client_id (slug). Lookup via client_index_col."""
    if not client_id:
        return clientes_col_admin
    try:
        ix = client_index_col.find_one({'client_id': client_id})
    except Exception:
        ix = None
    if ix and ix.get('revendedor_id'):
        try:
            rev = revendedores_col.find_one({'_id': ObjectId(ix['revendedor_id'])})
            col = _col_revendedor(rev)
            if col is not None:
                return col
        except Exception as e:
            print(f"[ROUTING] falha {client_id}: {e}", flush=True)
    return clientes_col_admin


def _col_atual_da_sessao():
    """Resolve a coleção do cliente logado. Usa session['rev_id'] (se setado
    no login) ou faz lookup pelo client_id.

    PERFORMANCE: memoiza o resultado em flask.g (1x por request). Antes CADA
    clientes_col.find/update re-resolvia o banco com query(s) ao Mongo Atlas —
    e endpoints chamam clientes_col várias vezes, então cada ação acumulava
    round-trips e ficava lenta. Além disso, quando o login marcou rev_id=None
    (cliente do admin, o caso comum), devolve direto o banco do admin SEM o
    lookup no índice."""
    try:
        if hasattr(g, '_col_sessao'):
            return g._col_sessao
    except Exception:
        pass
    col = None
    if 'rev_id' in session:
        rev_id = session.get('rev_id')
        if rev_id:
            try:
                rev = revendedores_col.find_one({'_id': ObjectId(rev_id)})
                c = _col_revendedor(rev)
                if c is not None:
                    col = c
            except Exception:
                col = None
        if col is None:
            col = clientes_col_admin  # rev_id None = cliente do admin (sem índice)
    else:
        col = _col_para_client_id(session.get('client_id'))  # sessão legada
    try:
        g._col_sessao = col
    except Exception:
        pass
    return col


class _ClientesRouter:
    """Router que substitui o `clientes_col` legado. Cada chamada resolve o
    banco do cliente logado e delega.

    NOTA: usado SÓ por código com sessão. Helpers que recebem client_id
    explícito devem chamar `_col_para_client_id(...)` direto."""
    def _c(self):
        return _col_atual_da_sessao()
    def find(self, *a, **k): return self._c().find(*a, **k)
    def find_one(self, *a, **k): return self._c().find_one(*a, **k)
    def update_one(self, *a, **k): return self._c().update_one(*a, **k)
    def count_documents(self, *a, **k):
        c = self._c()
        try:
            return c.count_documents(*a, **k)
        except AttributeError:
            return 0

# Substitui o clientes_col por um router multi-tenant.
# Código antigo que faz clientes_col.find_one({...}) continua funcionando
# porque busca no banco do cliente da sessão atual.
clientes_col = _ClientesRouter()
# ═══════════════════════════════════════════════════════════════════════

CAMPOS_VARIAVEIS = [
    'DISCORD_TOKEN', 'GUILD_IDS', 'FORUM_CHANNEL_IDS', 'CATEGORIA_ID', 'FORMATO_SALA',
    'PIX_KEY',
    'LUCRO_POR_SALA', 'MENSAGEM_AUTO', 'IMAGEM_AUTO', 'PIX_AUTO_ATIVO',
    'MENSAGEM_AUTO_2', 'MENSAGEM_AUTO_2_ATIVO',
    'MOSTRAR_VALOR_PAGAMENTO',
    'STILO_CAIXA',
    'MODO_PADRAO', 'SALA_NOME', 'SENHA_SALA', 'AUTO_START',
    'GMAIL_USUARIO', 'GMAIL_SENHA_IMAP',
    # Respostas rápidas (gatilho→resposta) + IDs de admin (.c1/.c2/.ban)
    'RESPOSTAS_RAPIDAS',
]


@app.route('/')
def index():
    resp = app.make_response(render_template('index.html'))
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


def _norm_login(s):
    """Normaliza string de login pra match tolerante: minúsculo, sem acento,
    sem espaços nas pontas, espaços internos colapsados. 'João  Silva ' →
    'joao silva'. Usado pra o cliente logar pelo nome sem se preocupar com
    acento/maiúscula."""
    if not s:
        return ''
    s = unicodedata.normalize('NFKD', str(s))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return ' '.join(s.lower().split())


# Login só quando o BOT está ON. Critério: o status do bot NÃO pode estar
# 'offline' (vermelho). Online (verde) e incerto (amarelo: timeout/checando)
# liberam — assim um hiccup da API não tranca cliente legítimo. Sem app_id =
# offline = bloqueia. Liga/desliga por env EXIGIR_APP_ATIVO (default 1).
_EXIGIR_APP_ATIVO = (os.environ.get('EXIGIR_APP_ATIVO', '1') or '1').strip() == '1'


def _tem_app_ativo(doc):
    """True se o login é permitido: bot ON (verde) ou incerto (amarelo).
    False só quando o bot está confirmadamente OFFLINE (vermelho) ou sem app."""
    if not doc:
        return False
    return _status_simples(doc.get('app_id')) != 'offline'


@app.route('/check-id', methods=['POST'])
def check_id():
    """Passo 1 do login: verifica se o usuario existe e se precisa de senha."""
    data = request.get_json() or {}
    usuario = (data.get('usuario') or '').strip()
    if usuario.lower() == PANEL_ADMIN_USER.lower():
        return jsonify({'ok': True, 'nome': 'Administrador', 'precisa_senha': True, 'is_admin': True})
    if usuario == ADMIN_ID:
        return jsonify({'ok': True, 'nome': 'Admin', 'precisa_senha': False, 'is_admin': True})
    # Parceiros (shadow/snoop) - sempre exigem senha
    if usuario.lower() in PARTNERS:
        p = PARTNERS[usuario.lower()]
        return jsonify({'ok': True, 'nome': p['nome'], 'precisa_senha': True, 'is_partner': True})
    doc, _rev_id = _resolver_cliente(usuario)
    # Só loga quem tem aplicação ativa. Sem app → "ID incorreto" (igual ID errado).
    if doc and _EXIGIR_APP_ATIVO and not _tem_app_ativo(doc):
        return jsonify({'ok': False, 'msg': 'ID incorreto.'}), 404
    if doc:
        # Senha REMOVIDA pro cliente: login é só com o client_id, sem senha.
        # (Admin e parceiros acima continuam exigindo senha.) O nome volta pra
        # aparecer na saudação ao logar.
        return jsonify({'ok': True, 'nome': doc.get('nome') or usuario,
                        'precisa_senha': False, 'is_admin': False})
    return jsonify({'ok': False, 'msg': 'ID incorreto.'}), 404


def _resolver_cliente(usuario):
    """Acha o doc do cliente em qualquer banco (admin ou revendedor).
    Tenta nesta ordem:
      1. client_index_col por client_id exato → vai pro banco do dono
      2. client_index_col por app_id exato → vai pro banco do dono
      3. client_index_col por nome_norm (login fuzzy) → vai pro banco do dono
      4. Fallback: banco do admin (clientes legados sem entrada no índice)

    Devolve (doc, rev_id) — None, None se nada bate.
    """
    if not usuario:
        return None, None

    # 1-3: usa o índice (mantido pelo configadm) pra descobrir o banco certo
    alvo = (usuario or '').strip()
    alvo_norm = _norm_login(alvo)
    # LOGIN SÓ POR login_id: nome, client_id e app_id NÃO logam mais (segurança —
    # sem senha, o ID aleatório é a única chave; nome seria fácil de adivinhar).
    for filtro in (
        {'login_id': alvo.lower()},
    ):
        if not filtro:
            continue
        try:
            ix = client_index_col.find_one(filtro)
        except Exception:
            ix = None
        if not ix:
            continue
        rev_id = (ix.get('revendedor_id') or '').strip()
        client_id_real = ix.get('client_id')
        # Decide o banco
        col = clientes_col_admin
        if rev_id:
            try:
                rev = revendedores_col.find_one({'_id': ObjectId(rev_id)})
                c = _col_revendedor(rev)
                if c is not None:
                    col = c
            except Exception:
                pass
        # Busca o doc real lá
        try:
            doc = col.find_one({'client_id': client_id_real}) if client_id_real else None
        except Exception:
            doc = None
        if doc:
            return doc, (rev_id or None)

    # 4: fallback — procura login_id direto no banco do admin (clientes que
    # ainda não estão no índice com login_id, mas têm o campo no doc).
    # NÃO loga mais por nome/client_id/app_id — só login_id.
    doc = clientes_col_admin.find_one({'login_id': alvo.lower()})
    if doc:
        return doc, None
    return None, None


@app.route('/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    usuario = (data.get('usuario') or '').strip()
    senha = (data.get('senha') or '').strip()

    if usuario.lower() == PANEL_ADMIN_USER.lower():
        if senha != PANEL_ADMIN_PASS:
            return jsonify({'ok': False, 'msg': 'Senha incorreta.'}), 401
        session['logado'] = True
        session['usuario'] = 'Administrador'
        session['is_admin'] = True
        session['is_panel_admin'] = True
        session['client_id'] = None
        return _resp_login({'is_admin': True})

    if usuario == ADMIN_ID:
        session['logado'] = True
        session['usuario'] = usuario
        session['is_admin'] = True
        session['is_panel_admin'] = True
        session['client_id'] = None
        return _resp_login({'is_admin': True})

    # Login Shadow / Snoop (parceiros)
    pkey = usuario.lower()
    if pkey in PARTNERS:
        if senha != PARTNERS[pkey]['pass']:
            return jsonify({'ok': False, 'msg': 'Senha incorreta.', 'precisa_senha': True}), 401
        session['logado'] = True
        session['usuario'] = PARTNERS[pkey]['nome']
        session['is_admin'] = False
        session['is_panel_admin'] = False
        session['is_partner'] = True
        session['partner_key'] = pkey
        session['client_id'] = None
        session['doc_id'] = None
        # Garante doc inicial
        parceiros_col.update_one({'_id': pkey},
                                 {'$setOnInsert': {'_id': pkey, 'nome': PARTNERS[pkey]['nome'], 'saldo': 0.0}},
                                 upsert=True)
        return _resp_login({'is_partner': True})

    doc, rev_id = _resolver_cliente(usuario)

    # Só loga quem tem aplicação ativa (app_id preenchido). Sem app → "ID incorreto".
    if doc and _EXIGIR_APP_ATIVO and not _tem_app_ativo(doc):
        print(f"[LOGIN] ID '{usuario}' sem aplicação ativa — negado.", flush=True)
        return jsonify({'ok': False, 'msg': 'ID incorreto.'}), 401

    if not doc and ENV_CLIENT_ID and usuario == ENV_CLIENT_ID:
        session['logado'] = True
        session['usuario'] = usuario
        session['is_admin'] = False
        session['is_panel_admin'] = False
        session['client_id'] = usuario
        session['doc_id'] = None
        session['rev_id'] = None
        return _resp_login()

    if not doc:
        print(f"[LOGIN] ID '{usuario}' nao bateu.", flush=True)
        return jsonify({'ok': False, 'msg': 'ID nao encontrado.'}), 401

    # Senha REMOVIDA pro cliente: login é só com o client_id. Não checa senha,
    # mesmo que o doc ainda tenha um client_pass salvo de antes (ignorado).
    session['logado'] = True
    session['usuario'] = doc.get('nome') or usuario
    session['is_admin'] = False
    session['is_panel_admin'] = False
    session['client_id'] = doc.get('client_id')
    session['doc_id'] = str(doc.get('_id'))
    # REVENDA: revendedor_id guardado pra todas as queries subsequentes acharem
    # o cliente no banco certo via _col_atual_da_sessao().
    session['rev_id'] = rev_id
    print(f"[LOGIN] OK '{usuario}' (client_id={doc.get('client_id')}, rev_id={rev_id}).", flush=True)
    return _resp_login()


@app.route('/api/limpar-erro-sala', methods=['POST'])
def api_limpar_erro_sala():
    if not session.get('logado'):
        return jsonify({'ok': False}), 401
    client_id = session.get('client_id')
    doc_id = session.get('doc_id')
    if doc_id:
        try:
            filtro = {'_id': ObjectId(doc_id)}
        except Exception:
            filtro = {'client_id': client_id}
    elif client_id:
        filtro = {'client_id': client_id}
    else:
        return jsonify({'ok': False, 'msg': 'sem cliente'}), 400
    clientes_col.update_one(filtro, {'$unset': {'ultimo_erro_sala': ''}})
    return jsonify({'ok': True})


@app.route('/api/admin/visao-geral')
def api_admin_visao_geral():
    if not session.get('logado') or not session.get('is_panel_admin'):
        return jsonify({'ok': False, 'msg': 'acesso negado'}), 403
    clientes = []
    for c in clientes_col.find({}, {'client_id': 1, 'nome': 1, 'app_id': 1,
                                    'saldo_salas': 1, 'vencimento': 1, 'ultimo_erro_sala': 1}):
        clientes.append({
            'id': str(c.get('_id')),
            'client_id': c.get('client_id', ''),
            'nome': c.get('nome') or c.get('client_id') or '-',
            'app_id': c.get('app_id', ''),
            'saldo_salas': int(c.get('saldo_salas', 0) or 0),
            'vencimento': c.get('vencimento', ''),
            'tem_erro': bool(c.get('ultimo_erro_sala')),
        })
    clientes.sort(key=lambda x: x['nome'].lower())
    nome_por_doc = {}
    for c in clientes_col.find({}, {'nome': 1, 'client_id': 1}):
        nome_por_doc[str(c['_id'])] = c.get('nome') or c.get('client_id') or '-'
    ultimas = []
    try:
        for v in consumo_col.find({}).sort('ts', -1).limit(30):
            ts = v.get('ts')
            try:
                ts_str = ts.astimezone().strftime('%d/%m %H:%M') if hasattr(ts, 'astimezone') else str(ts)
            except Exception:
                ts_str = str(ts)
            ultimas.append({
                'cliente': nome_por_doc.get(v.get('doc_id', ''), v.get('client_id', '-')),
                'qtd': int(v.get('qtd', 0) or 0),
                'quando': ts_str,
            })
    except Exception as e:
        print(f"[ADMIN] erro vendas: {e}", flush=True)
    return jsonify({'ok': True, 'clientes': clientes, 'total_clientes': len(clientes),
                    'total_saldo': sum(c['saldo_salas'] for c in clientes),
                    'ultimas_vendas': ultimas})


# ===== PIX (MisticPay) — só usado pelo fluxo legado de admin/parceiros =====
# Cliente final NÃO compra mais via PIX (substituído por keys). Mantemos:
#   - _get_misticpay_cfg / _creditar_salas / webhook: pra reconciliar
#     pagamentos antigos pendentes e suportar saque de parceiros
#   - admin "PIX Pendentes" tab: recrédito manual de transações antigas
# As rotas /api/pix/info e /api/pix/criar foram removidas (cliente não usa).
MISTICPAY_API = 'https://api.misticpay.com/api'
PIX_PAYER_NAME = os.environ.get('PIX_PAYER_NAME', 'Painel SalasFF')
PIX_PAYER_DOC = os.environ.get('PIX_PAYER_DOC', '11144477735')


def _get_misticpay_cfg():
    cfg = config_col.find_one({'_id': 'global'}) or {}
    return ((cfg.get('misticpay_ci') or '').strip(),
            (cfg.get('misticpay_cs') or '').strip(),
            float(cfg.get('valor_por_sala') or 1.0))


def _creditar_salas(pagamento):
    from pymongo import ReturnDocument
    from datetime import datetime, timezone
    p = pagamentos_col.find_one_and_update(
        {'_id': pagamento['_id'], 'creditado': {'$ne': True}},
        {'$set': {'creditado': True, 'status': 'COMPLETO'}},
        return_document=ReturnDocument.AFTER)
    if not p:
        return False
    qtd = int(p.get('qtd', 0) or 0)
    valor = float(p.get('valor', 0) or 0)
    filtro = None
    if p.get('doc_id'):
        try:
            filtro = {'_id': ObjectId(p['doc_id'])}
        except Exception:
            filtro = None
    if not filtro and p.get('client_id'):
        filtro = {'client_id': p['client_id']}

    # Resolve a coleção certa pelo client_id do pagamento (multi-tenant)
    col_cliente = _col_para_client_id(p.get('client_id') or '')

    # Nome do cliente pro log
    cli_nome = '-'
    if filtro:
        c = col_cliente.find_one(filtro, {'nome': 1, 'client_id': 1})
        if c:
            cli_nome = c.get('nome') or c.get('client_id') or '-'

    if filtro and qtd > 0:
        col_cliente.update_one(filtro, {'$inc': {'saldo_salas': qtd}})
        print(f"[PIX-LEGADO] Creditadas {qtd} salas (txid={p.get('transactionId')}).", flush=True)

    # Reparte 50/50 entre Shadow e Snoop (legado)
    parte = round(valor * PARTNER_SPLIT, 2)
    if parte > 0:
        for pk in PARTNERS:
            parceiros_col.update_one({'_id': pk},
                                     {'$inc': {'saldo': parte},
                                      '$setOnInsert': {'nome': PARTNERS[pk]['nome']}},
                                     upsert=True)
        print(f"[PIX-LEGADO] R$ {parte:.2f} pra cada parceiro (venda R$ {valor:.2f}).", flush=True)

    # Log da venda
    vendas_col.insert_one({
        'transactionId': p.get('transactionId'),
        'client_id': p.get('client_id') or '',
        'doc_id': p.get('doc_id') or '',
        'cliente_nome': cli_nome,
        'qtd': qtd,
        'valor': valor,
        'parte_parceiro': parte,
        'ts': datetime.now(timezone.utc),
    })
    return True


# ═══════════════════════════════════════════════════════════════════
# RESGATAR KEY — cliente cola key de salas e ganha saldo no bot
# ═══════════════════════════════════════════════════════════════════
# Painel-cliente faz proxy pro endpoint /bot/resgatar-key do configadm,
# que é a fonte de verdade das keys. O configadm sabe rotear pro banco
# certo (incluindo Supabase do revendedor) e credita o saldo.
#
# Mais simples que duplicar a coleção de keys no painel-cliente.
@app.route('/api/resgatar-key', methods=['POST'])
def api_resgatar_key():
    if not session.get('logado'):
        return jsonify({'ok': False, 'msg': 'Não autenticado.'}), 401
    # Admin/parceiro não resgatam — fluxo é só pra cliente final
    if session.get('is_admin') or session.get('is_panel_admin') or session.get('is_partner'):
        return jsonify({'ok': False, 'msg': 'Resgate de key só funciona pra clientes.'}), 403
    client_id = session.get('client_id')
    if not client_id:
        return jsonify({'ok': False, 'msg': 'Sessão sem client_id.'}), 400

    data = request.get_json() or {}
    key = (data.get('key') or '').strip().upper()
    if not key:
        return jsonify({'ok': False, 'msg': 'Cole a key.'}), 400
    # Saneamento básico no formato esperado SALAS-XXXX-XXXX-XXXX
    # Aceita também com/sem hífens (alguns clientes vão colar limpo).
    if len(key.replace('-', '')) < 8:
        return jsonify({'ok': False, 'msg': 'Key muito curta.'}), 400
    if len(key) > 50:
        return jsonify({'ok': False, 'msg': 'Key muito longa.'}), 400

    try:
        r = requests.post(
            f'{CONFIG_URL}/bot/resgatar-key',
            headers={'X-Bot-Secret': BOT_FETCH_SECRET, 'Content-Type': 'application/json'},
            json={'client_id': client_id, 'key': key},
            timeout=20,
        )
    except requests.Timeout:
        return jsonify({'ok': False, 'msg': 'Timeout no servidor de keys.'}), 504
    except Exception as e:
        return jsonify({'ok': False, 'msg': f'Erro de rede: {e}'}), 502

    try:
        resp = r.json()
    except Exception:
        return jsonify({'ok': False, 'msg': f'Resposta inválida do servidor (HTTP {r.status_code}).'}), 502

    # Retorna o status do configadm pra o front mostrar a mensagem certa
    return jsonify(resp), r.status_code


# Rota /api/pix/status/<txid> REMOVIDA — só era usada pelo polling do modal
# de compra de cliente. Reconciliação automática segue ativa via worker em
# background + endpoint admin de recrédito manual.


@app.route('/webhook/misticpay', methods=['POST'])
def webhook_misticpay():
    data = request.get_json(silent=True) or {}
    txid = str(data.get('transactionId') or '')
    status = (data.get('status') or '').upper()
    if not txid:
        return jsonify({'ok': False, 'msg': 'sem transactionId'}), 400
    pag = pagamentos_col.find_one({'transactionId': txid})
    if not pag:
        print(f"[WEBHOOK] txid {txid} nao encontrado.", flush=True)
        return jsonify({'ok': True, 'msg': 'ignorado'})
    if status == 'COMPLETO':
        _creditar_salas(pag)
    elif status == 'FALHA':
        pagamentos_col.update_one({'_id': pag['_id']}, {'$set': {'status': 'FALHA'}})
    return jsonify({'ok': True})


# ===== RENDER DE CARDS (serviço de imagem do selfbot) =====
# O selfbot chama estes endpoints (IMG_RENDER_URL aponta pra este painel) pra
# receber o card PNG bonito. Geramos com Pillow — sem Puppeteer/Chromium, que
# não cabe nos 512MB junto com Flask+IMAP. Se a lib/algo falhar, devolvemos
# erro e o bot cai sozinho no Pillow local (fallback embutido nele).
#
# Auth: header X-Render-Secret == RENDER_SECRET (default '22', igual ao bot).
RENDER_SECRET = (os.environ.get('RENDER_SECRET', '') or '').strip() or '22'


def _render_autorizado():
    return (request.headers.get('X-Render-Secret') or '').strip() == RENDER_SECRET


def _responder_png(png_bytes):
    if not png_bytes:
        # 503: o bot interpreta qualquer não-200/não-imagem como "usa Pillow".
        return jsonify({'ok': False, 'msg': 'falha ao gerar imagem'}), 503
    from flask import Response
    return Response(png_bytes, mimetype='image/png')


@app.route('/render/sala', methods=['POST'])
def render_sala():
    if not _render_autorizado():
        return jsonify({'ok': False, 'msg': 'render secret invalido'}), 401
    try:
        from utils import render_cards
        payload = request.get_json(silent=True) or {}
        return _responder_png(render_cards.gerar_sala(payload))
    except Exception as e:
        print(f"[RENDER] sala falhou: {type(e).__name__}: {e}", flush=True)
        return jsonify({'ok': False, 'msg': 'erro interno'}), 503


@app.route('/render/resultado', methods=['POST'])
def render_resultado():
    if not _render_autorizado():
        return jsonify({'ok': False, 'msg': 'render secret invalido'}), 401
    try:
        from utils import render_cards
        payload = request.get_json(silent=True) or {}
        return _responder_png(render_cards.gerar_resultado(payload))
    except Exception as e:
        print(f"[RENDER] resultado falhou: {type(e).__name__}: {e}", flush=True)
        return jsonify({'ok': False, 'msg': 'erro interno'}), 503


# ===== PARCEIROS (Shadow/Snoop) =====
@app.route('/api/parceiro/dados')
def api_parceiro_dados():
    if not session.get('logado') or not session.get('is_partner'):
        return jsonify({'ok': False, 'msg': 'acesso negado'}), 403
    pk = session.get('partner_key')
    doc = parceiros_col.find_one({'_id': pk}) or {}
    saldo = float(doc.get('saldo', 0) or 0)
    # Total ja sacado (saques aprovados/processando + completos)
    total_sacado = 0.0
    try:
        ag = list(saques_col.aggregate([
            {'$match': {'parceiro': pk, 'status': {'$in': ['QUEUED', 'PROCESSANDO', 'COMPLETO', 'PENDENTE']}}},
            {'$group': {'_id': None, 'tot': {'$sum': '$amount'}}}
        ]))
        if ag:
            total_sacado = float(ag[0].get('tot', 0) or 0)
    except Exception:
        pass
    # Total recebido em vendas (50% das vendas)
    total_recebido = 0.0
    try:
        ag2 = list(vendas_col.aggregate([
            {'$group': {'_id': None, 'tot': {'$sum': '$parte_parceiro'}}}
        ]))
        if ag2:
            total_recebido = float(ag2[0].get('tot', 0) or 0)
    except Exception:
        pass
    # Historico de saques
    saques = []
    for s in saques_col.find({'parceiro': pk}).sort('ts', -1).limit(50):
        ts = s.get('ts')
        try:
            ts_str = ts.astimezone().strftime('%d/%m %H:%M') if hasattr(ts, 'astimezone') else str(ts)
        except Exception:
            ts_str = str(ts)
        saques.append({
            'amount': float(s.get('amount', 0) or 0),
            'pixKey': s.get('pixKey', ''),
            'pixKeyType': s.get('pixKeyType', ''),
            'status': s.get('status', '?'),
            'transactionId': s.get('transactionId', ''),
            'ts': ts_str,
            'msg': s.get('msg', ''),
        })
    # Historico de vendas (todas, pra mostrar quanto gerou)
    vendas = []
    for v in vendas_col.find({}).sort('ts', -1).limit(50):
        ts = v.get('ts')
        try:
            ts_str = ts.astimezone().strftime('%d/%m %H:%M') if hasattr(ts, 'astimezone') else str(ts)
        except Exception:
            ts_str = str(ts)
        vendas.append({
            'cliente': v.get('cliente_nome', '-'),
            'qtd': int(v.get('qtd', 0) or 0),
            'valor': float(v.get('valor', 0) or 0),
            'parte': float(v.get('parte_parceiro', 0) or 0),
            'ts': ts_str,
        })
    return jsonify({'ok': True, 'nome': PARTNERS.get(pk, {}).get('nome', pk),
                    'saldo': saldo, 'total_sacado': total_sacado,
                    'total_recebido': total_recebido,
                    'saques': saques, 'vendas': vendas,
                    'minimo_saque': PARTNER_SAQUE_MIN})


@app.route('/api/parceiro/clientes')
def api_parceiro_clientes():
    """Lista de clientes pra parceiros (Shadow/Snoop) visualizarem.
    Mesma forma do /api/admin/visao-geral mas com projeção mais conservadora —
    parceiros NÃO veem token, PIX key, Gmail, etc. Só dados de saldo/operação.
    Útil pra parceiro saber quem tá ativo, quem tá com saldo zerado, quem
    precisa renovar — sem expor configuração sensível dos bots.
    """
    if not session.get('logado') or not session.get('is_partner'):
        return jsonify({'ok': False, 'msg': 'acesso negado'}), 403
    clientes = []
    # Projeção limitada: SÓ campos não-sensíveis. Mesmo se o doc tiver token,
    # ele não vem nesta query.
    for c in clientes_col.find({}, {
        'client_id': 1, 'nome': 1, 'app_id': 1,
        'saldo_salas': 1, 'vencimento': 1, 'ultimo_erro_sala': 1,
    }):
        saldo = int(c.get('saldo_salas', 0) or 0)
        clientes.append({
            'id': str(c.get('_id')),
            'client_id': c.get('client_id', ''),
            'nome': c.get('nome') or c.get('client_id') or '-',
            'saldo_salas': saldo,
            'vencimento': c.get('vencimento', ''),
            'tem_erro': bool(c.get('ultimo_erro_sala')),
        })
    # Ordena: clientes com saldo > 0 primeiro (ordem decrescente de saldo),
    # depois os zerados (alfabético). Parceiro vê de cara quem tá vendendo.
    clientes.sort(key=lambda x: (-x['saldo_salas'] if x['saldo_salas'] > 0 else 1,
                                 x['nome'].lower()))
    total_saldo = sum(c['saldo_salas'] for c in clientes)
    com_saldo = sum(1 for c in clientes if c['saldo_salas'] > 0)
    sem_saldo = sum(1 for c in clientes if c['saldo_salas'] <= 0)
    return jsonify({
        'ok': True,
        'clientes': clientes,
        'total_clientes': len(clientes),
        'total_saldo': total_saldo,
        'com_saldo': com_saldo,
        'sem_saldo': sem_saldo,
    })


@app.route('/api/parceiro/sacar', methods=['POST'])
def api_parceiro_sacar():
    if not session.get('logado') or not session.get('is_partner'):
        return jsonify({'ok': False, 'msg': 'acesso negado'}), 403
    pk = session.get('partner_key')
    data = request.get_json() or {}
    try:
        amount = float(data.get('amount') or 0)
    except (ValueError, TypeError):
        return jsonify({'ok': False, 'msg': 'Valor invalido.'}), 400
    pix_key = (data.get('pixKey') or '').strip()
    pix_type = (data.get('pixKeyType') or '').strip().upper()
    if amount < PARTNER_SAQUE_MIN:
        return jsonify({'ok': False, 'msg': f'Minimo de saque: R$ {PARTNER_SAQUE_MIN:.2f}'}), 400
    if not pix_key:
        return jsonify({'ok': False, 'msg': 'Informe a chave PIX.'}), 400
    if pix_type not in ('CPF', 'CNPJ', 'EMAIL', 'TELEFONE', 'CHAVE_ALEATORIA'):
        return jsonify({'ok': False, 'msg': 'Tipo de chave PIX invalido.'}), 400
    # Verifica saldo
    doc = parceiros_col.find_one({'_id': pk}) or {}
    saldo = float(doc.get('saldo', 0) or 0)
    if amount > saldo + 0.001:
        return jsonify({'ok': False, 'msg': f'Saldo insuficiente (R$ {saldo:.2f}).'}), 400
    # Debita o saldo de forma atomica (so se ainda tiver)
    from pymongo import ReturnDocument
    updated = parceiros_col.find_one_and_update(
        {'_id': pk, 'saldo': {'$gte': amount - 0.001}},
        {'$inc': {'saldo': -amount}},
        return_document=ReturnDocument.AFTER)
    if not updated:
        return jsonify({'ok': False, 'msg': 'Saldo mudou, recarregue.'}), 409
    # Credenciais MisticPay
    ci, cs, _ = _get_misticpay_cfg()
    if not ci or not cs:
        # Devolve saldo
        parceiros_col.update_one({'_id': pk}, {'$inc': {'saldo': amount}})
        return jsonify({'ok': False, 'msg': 'Pagamento nao configurado.'}), 503
    webhook_url = os.environ.get('PIX_WEBHOOK_URL', '').strip()
    body = {
        'amount': amount,
        'pixKey': pix_key,
        'pixKeyType': pix_type,
        'description': f'Saque {PARTNERS[pk]["nome"]}',
    }
    if webhook_url:
        body['projectWebhook'] = webhook_url
    from datetime import datetime, timezone
    try:
        r = requests.post(f'{MISTICPAY_API}/transactions/withdraw',
                          headers={'ci': ci, 'cs': cs, 'Content-Type': 'application/json'},
                          json=body, timeout=25)
        resp = r.json()
    except Exception as e:
        # Devolve saldo em qualquer erro de rede
        parceiros_col.update_one({'_id': pk}, {'$inc': {'saldo': amount}})
        return jsonify({'ok': False, 'msg': f'Erro de rede: {e}'}), 502
    if r.status_code not in (200, 201):
        parceiros_col.update_one({'_id': pk}, {'$inc': {'saldo': amount}})
        msg = resp.get('message') or f'Erro HTTP {r.status_code}'
        return jsonify({'ok': False, 'msg': msg}), 502
    d = resp.get('data') or {}
    txid = str(d.get('transactionId') or '')
    status = (d.get('status') or 'QUEUED').upper()
    saques_col.insert_one({
        'parceiro': pk,
        'amount': amount,
        'pixKey': pix_key,
        'pixKeyType': pix_type,
        'transactionId': txid,
        'jobId': d.get('jobId', ''),
        'status': status,
        'msg': resp.get('message', ''),
        'ts': datetime.now(timezone.utc),
    })
    return jsonify({'ok': True, 'msg': resp.get('message', 'Saque solicitado.'),
                    'transactionId': txid, 'status': status})


@app.route('/api/admin/vendas')
def api_admin_vendas():
    """Log completo de vendas PIX para o painel admin."""
    if not session.get('logado') or not session.get('is_panel_admin'):
        return jsonify({'ok': False, 'msg': 'acesso negado'}), 403
    vendas = []
    total_valor = 0.0
    total_qtd = 0
    for v in vendas_col.find({}).sort('ts', -1).limit(200):
        ts = v.get('ts')
        try:
            ts_str = ts.astimezone().strftime('%d/%m %H:%M') if hasattr(ts, 'astimezone') else str(ts)
        except Exception:
            ts_str = str(ts)
        val = float(v.get('valor', 0) or 0)
        q = int(v.get('qtd', 0) or 0)
        total_valor += val
        total_qtd += q
        vendas.append({
            'cliente': v.get('cliente_nome', '-'),
            'qtd': q,
            'valor': val,
            'ts': ts_str,
        })
    return jsonify({'ok': True, 'vendas': vendas,
                    'total_valor': round(total_valor, 2),
                    'total_qtd': total_qtd})


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})


@app.route('/api/trocar-senha', methods=['POST'])
def api_trocar_senha():
    """Auto-serviço: o cliente logado troca a própria senha do painel.
    EXIGE a senha atual (proteção contra alguém com a sessão aberta).
    A senha fica em client_pass do doc do cliente no Mongo."""
    if not session.get('logado'):
        return jsonify({'ok': False, 'msg': 'Não autenticado.'}), 401
    # Admin e parceiros não têm client_pass — esse fluxo é só pra cliente.
    if session.get('is_admin') or session.get('is_panel_admin') or session.get('is_partner'):
        return jsonify({'ok': False, 'msg': 'Troca de senha disponível só para clientes.'}), 403

    data = request.get_json() or {}
    senha_atual = (data.get('senha_atual') or '').strip()
    nova = (data.get('nova_senha') or '').strip()
    confirma = (data.get('confirma_senha') or '').strip()

    doc = _resolver_doc_logado()
    if not doc:
        return jsonify({'ok': False, 'msg': 'Cliente não encontrado.'}), 404

    senha_cad = (doc.get('client_pass') or '').strip()

    # Exige senha atual. Se o cliente hoje não tem senha (login sem senha),
    # ainda assim aceita — nesse caso senha_atual deve vir vazia.
    if senha_atual != senha_cad:
        return jsonify({'ok': False, 'msg': 'Senha atual incorreta.'}), 400

    if not nova:
        return jsonify({'ok': False, 'msg': 'Digite a nova senha.'}), 400
    if len(nova) < 3:
        return jsonify({'ok': False, 'msg': 'A nova senha precisa de pelo menos 3 caracteres.'}), 400
    if len(nova) > 100:
        return jsonify({'ok': False, 'msg': 'Senha muito longa (máx 100).'}), 400
    if nova != confirma:
        return jsonify({'ok': False, 'msg': 'A confirmação não bate com a nova senha.'}), 400
    if nova == senha_cad:
        return jsonify({'ok': False, 'msg': 'A nova senha é igual à atual.'}), 400

    clientes_col.update_one(
        {'_id': doc['_id']},
        {'$set': {'client_pass': nova}}
    )
    print(f"[SENHA] Cliente client_id={doc.get('client_id')} trocou a própria senha.", flush=True)
    return jsonify({'ok': True, 'msg': 'Senha alterada com sucesso.'})


@app.route('/api/_debug')
def api_debug():
    """Endpoint temporário pra diagnosticar leitura do Mongo.
    Acesse /api/_debug enquanto logado pra ver qual doc está sendo lido."""
    if not session.get('logado'):
        return jsonify({'ok': False, 'msg': 'não logado'}), 401
    client_id = session.get('client_id')
    usuario = session.get('usuario')

    doc = clientes_col.find_one({'client_id': client_id}) if client_id else None

    # Lista TODOS os docs que tem esse usuário/app_id em qualquer campo (busca ampla)
    candidatos = list(clientes_col.find({'$or': [
        {'client_id': client_id},
        {'client_id': usuario},
        {'app_id': client_id},
        {'app_id': usuario},
    ]}, {'_id': 1, 'client_id': 1, 'app_id': 1, 'nome': 1,
         'variaveis.GMAIL_USUARIO': 1, 'variaveis.MENSAGEM_AUTO': 1, 'variaveis.CATEGORIA_ID': 1}))

    return jsonify({
        'session_usuario': usuario,
        'session_client_id': client_id,
        'session_is_admin': session.get('is_admin'),
        'doc_encontrado': bool(doc),
        'doc_resumo': ({
            '_id': str(doc.get('_id')),
            'client_id': doc.get('client_id'),
            'app_id': doc.get('app_id'),
            'nome': doc.get('nome'),
            'variaveis_keys_com_valor': sorted([k for k, v in (doc.get('variaveis') or {}).items() if v]),
            'GMAIL_USUARIO': (doc.get('variaveis') or {}).get('GMAIL_USUARIO', ''),
            'MENSAGEM_AUTO': (doc.get('variaveis') or {}).get('MENSAGEM_AUTO', ''),
            'CATEGORIA_ID': (doc.get('variaveis') or {}).get('CATEGORIA_ID', ''),
        } if doc else None),
        'candidatos_no_mongo': [{
            '_id': str(c.get('_id')),
            'client_id': c.get('client_id'),
            'app_id': c.get('app_id'),
            'nome': c.get('nome'),
            'GMAIL_USUARIO': (c.get('variaveis') or {}).get('GMAIL_USUARIO', ''),
            'MENSAGEM_AUTO_len': len((c.get('variaveis') or {}).get('MENSAGEM_AUTO', '')),
            'CATEGORIA_ID': (c.get('variaveis') or {}).get('CATEGORIA_ID', ''),
        } for c in candidatos],
        'total_candidatos': len(candidatos),
        'mongo_db': db.name,
        'mongo_collection': clientes_col.name,
    })


def _to_float(v, default=0.0):
    """Converte string brasileira (0,70) ou inglesa (0.70) pra float."""
    if v is None or v == '':
        return default
    try:
        return float(str(v).replace(',', '.'))
    except Exception:
        return default


def _track_consumo_saldo(doc):
    """Detecta queda no saldo_salas e registra na coleção 'consumo'.

    Estratégia: guarda o último saldo conhecido no doc do cliente
    (`ultimo_saldo_visto`). Quando o /api/dados é chamado e o saldo atual
    é MENOR que o último visto, registra a diferença como consumo.

    Atenção: aumentos de saldo (recargas pelo admin) só atualizam o
    ultimo_saldo_visto sem criar registro. Não contam como "lucro".
    """
    if not doc:
        return
    saldo_atual = int(doc.get('saldo_salas', 0) or 0)
    ultimo = doc.get('ultimo_saldo_visto')

    if ultimo is None:
        # Primeira execução pra esse cliente — só registra baseline, sem consumo
        try:
            clientes_col.update_one({'_id': doc['_id']}, {'$set': {'ultimo_saldo_visto': saldo_atual}})
        except Exception as e:
            print(f"[CONSUMO] Erro ao salvar baseline: {e}", flush=True)
        return

    try:
        ultimo_int = int(ultimo)
    except Exception:
        ultimo_int = saldo_atual

    if saldo_atual < ultimo_int:
        # Saldo caiu — registra consumo da diferença
        qtd = ultimo_int - saldo_atual
        variaveis = doc.get('variaveis', {}) or {}
        lucro_unit = _to_float(variaveis.get('LUCRO_POR_SALA'), 0.0)
        try:
            consumo_col.insert_one({
                'doc_id': str(doc['_id']),
                'client_id': doc.get('client_id', ''),
                'qtd': qtd,
                'lucro_unit': lucro_unit,
                'ts': datetime.now(timezone.utc),
            })
            clientes_col.update_one({'_id': doc['_id']}, {'$set': {'ultimo_saldo_visto': saldo_atual}})
            print(f"[CONSUMO] {doc.get('client_id','?')}: -{qtd} salas (lucro_unit=R${lucro_unit:.2f})", flush=True)
        except Exception as e:
            print(f"[CONSUMO] Erro ao registrar: {e}", flush=True)
    elif saldo_atual > ultimo_int:
        # Recarga — só atualiza o baseline, sem registrar como consumo
        try:
            clientes_col.update_one({'_id': doc['_id']}, {'$set': {'ultimo_saldo_visto': saldo_atual}})
        except Exception:
            pass


def _is_supa_analytics(col):
    return isinstance(col, SupabaseAnalyticsCol)


def _consumo_rows(doc_id):
    """Carrega as linhas de consumo de um doc_id normalizadas (qtd:int,
    lucro_unit:float, ts:datetime). Usado pela agregação em Python quando o
    consumo está no Supabase (PostgREST não faz aggregate)."""
    out = []
    for d in consumo_col.find({'doc_id': doc_id}):
        ts = d.get('ts')
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except Exception:
                continue
        if not isinstance(ts, datetime):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        out.append({'qtd': int(d.get('qtd') or 0),
                    'lucro_unit': float(d.get('lucro_unit') or 0.0), 'ts': ts})
    return out


def _periodo_lucro(doc_id):
    """Agrega o consumo de um cliente em 5 buckets: hoje, ontem, semana, mês, total.

    Datas são UTC. Limites:
      - hoje: meia-noite UTC do dia atual → agora
      - ontem: meia-noite UTC do dia anterior → meia-noite UTC do dia atual
      - semana: últimos 7 dias (rolling) → agora
      - mês: últimos 30 dias → agora
      - total: tudo
    Retorna {periodo: {salas: int, lucro: float}}.
    """
    agora = datetime.now(timezone.utc)
    hoje_inicio = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    ontem_inicio = hoje_inicio - timedelta(days=1)
    semana_inicio = agora - timedelta(days=7)
    mes_inicio = agora - timedelta(days=30)

    # SUPABASE: agrega em Python (PostgREST não faz aggregate).
    if _is_supa_analytics(consumo_col):
        rows = _consumo_rows(doc_id)
        def _soma(pred):
            s, l = 0, 0.0
            for r in rows:
                if pred(r['ts']):
                    s += r['qtd']; l += r['qtd'] * r['lucro_unit']
            return {'salas': int(s), 'lucro': round(float(l), 2)}
        return {
            'hoje':   _soma(lambda t: t >= hoje_inicio),
            'ontem':  _soma(lambda t: ontem_inicio <= t < hoje_inicio),
            'semana': _soma(lambda t: t >= semana_inicio),
            'mes':    _soma(lambda t: t >= mes_inicio),
            'total':  _soma(lambda t: True),
        }

    # Grupo (salas + lucro) reusado em cada bucket do $facet.
    def _grp():
        return [{'$group': {
            '_id': None,
            'salas': {'$sum': '$qtd'},
            'lucro': {'$sum': {'$multiply': ['$qtd', '$lucro_unit']}},
        }}]

    def _pick(arr):
        if arr:
            d = arr[0]
            return {'salas': int(d.get('salas', 0)), 'lucro': float(d.get('lucro', 0.0))}
        return {'salas': 0, 'lucro': 0.0}

    # PERFORMANCE: 1 agregação com $facet em vez de 5 round-trips separados ao
    # Mongo. Mesmo resultado, 5x menos idas-e-voltas (deixa o painel mais liso).
    pipeline = [
        {'$match': {'doc_id': doc_id}},
        {'$facet': {
            'hoje':   [{'$match': {'ts': {'$gte': hoje_inicio}}}] + _grp(),
            'ontem':  [{'$match': {'ts': {'$gte': ontem_inicio, '$lt': hoje_inicio}}}] + _grp(),
            'semana': [{'$match': {'ts': {'$gte': semana_inicio}}}] + _grp(),
            'mes':    [{'$match': {'ts': {'$gte': mes_inicio}}}] + _grp(),
            'total':  _grp(),
        }},
    ]
    try:
        r = list(consumo_col.aggregate(pipeline))
        if r:
            f = r[0]
            return {
                'hoje':   _pick(f.get('hoje')),
                'ontem':  _pick(f.get('ontem')),
                'semana': _pick(f.get('semana')),
                'mes':    _pick(f.get('mes')),
                'total':  _pick(f.get('total')),
            }
    except Exception as e:
        print(f"[LUCRO] Erro agregação facet: {e}", flush=True)
    return {k: {'salas': 0, 'lucro': 0.0} for k in ('hoje', 'ontem', 'semana', 'mes', 'total')}


def _contagem_eventos_hoje(client_id):
    """Conta eventos operacionais de hoje (UTC) do cliente, agrupados por tipo.
    Retorna {tipo: int}.

    Tipos esperados:
      - PAGAMENTO_CONFIRMADO: cada vez que o bot validou 1 pagamento (2 por sala normal)
      - SALA_CRIADA: cada sala criada com sucesso via API SalasBot
      - GO_INICIADO: 2 GOs confirmados → /api/v2/start:room OK
      - MSG_RESPONDIDA: bot enviou MENSAGEM_AUTO, secundária, ou resposta_rapida

    Se o cliente não tem client_id (ex: logou com app_id direto), retorna zeros.
    Performance: índice composto (client_id, ts) faz aggregate roda em <50ms.
    """
    zeros = {'PAGAMENTO_CONFIRMADO': 0, 'SALA_CRIADA': 0, 'GO_INICIADO': 0, 'MSG_RESPONDIDA': 0}
    if not client_id:
        return zeros

    agora = datetime.now(timezone.utc)
    hoje_inicio = agora.replace(hour=0, minute=0, second=0, microsecond=0)

    pipeline = [
        {'$match': {'client_id': client_id, 'ts': {'$gte': hoje_inicio}}},
        {'$group': {'_id': '$tipo', 'qtd': {'$sum': 1}}},
    ]
    out = dict(zeros)
    try:
        for r in eventos_col.aggregate(pipeline):
            tipo = r.get('_id')
            qtd = int(r.get('qtd', 0))
            if tipo in out:
                out[tipo] = qtd
            else:
                # Tipo novo (futuro) — adiciona dinamicamente, frontend pode mostrar
                out[tipo] = qtd
    except Exception as e:
        print(f"[EVENTOS-HOJE] Erro agregação: {e}", flush=True)
    return out


def _serie_diaria(doc_id, dias=14):
    """Agrega lucro/salas por dia nos últimos N dias (default 14).
    Retorna lista de {data, salas, lucro} ordenada do mais antigo pro mais novo,
    com 0 nos dias sem consumo (pra o gráfico nunca ter 'buracos').

    A data é string 'YYYY-MM-DD' em UTC — o frontend formata pro fuso local.
    """
    agora = datetime.now(timezone.utc)
    inicio = (agora.replace(hour=0, minute=0, second=0, microsecond=0)
              - timedelta(days=dias - 1))

    bucket_por_data = {}
    if _is_supa_analytics(consumo_col):
        # SUPABASE: agrupa por dia em Python.
        for r in _consumo_rows(doc_id):
            if r['ts'] >= inicio:
                chave = r['ts'].astimezone(timezone.utc).strftime('%Y-%m-%d')
                b = bucket_por_data.setdefault(chave, {'salas': 0, 'lucro': 0.0})
                b['salas'] += r['qtd']
                b['lucro'] = round(b['lucro'] + r['qtd'] * r['lucro_unit'], 2)
    else:
        pipeline = [
            {'$match': {'doc_id': doc_id, 'ts': {'$gte': inicio}}},
            {'$group': {
                '_id': {'$dateToString': {'format': '%Y-%m-%d', 'date': '$ts', 'timezone': 'UTC'}},
                'salas': {'$sum': '$qtd'},
                'lucro': {'$sum': {'$multiply': ['$qtd', '$lucro_unit']}},
            }},
        ]
        try:
            for r in consumo_col.aggregate(pipeline):
                bucket_por_data[r['_id']] = {
                    'salas': int(r.get('salas', 0)),
                    'lucro': float(r.get('lucro', 0.0)),
                }
        except Exception as e:
            print(f"[SERIE] Erro: {e}", flush=True)

    # Preenche dias vazios com 0 — pra gráfico não ficar com gaps.
    serie = []
    for i in range(dias):
        d = inicio + timedelta(days=i)
        chave = d.strftime('%Y-%m-%d')
        item = bucket_por_data.get(chave, {'salas': 0, 'lucro': 0.0})
        serie.append({'data': chave, 'salas': item['salas'], 'lucro': item['lucro']})
    return serie


def _calcular_progresso_meta(doc, lucro_unit):
    """Lê a meta do doc do cliente e calcula tudo que o frontend precisa pra renderizar:
    progresso atual, % atingido, dias restantes, ritmo necessário, projeção, se bateu.

    Estrutura da meta no Mongo (dentro do doc do cliente, campo `meta`):
      {tipo: 'salas'|'reais', alvo: float, data_limite: 'YYYY-MM-DD',
       criada_em: 'YYYY-MM-DD'}

    Retorna None se não tem meta. Caso contrário um dict com:
      tipo, alvo, atual, percentual (0-100, pode passar de 100 se bateu),
      atingida (bool), dias_restantes, ritmo_necessario_dia, ritmo_atual_dia,
      vai_bater (bool — se mantiver o ritmo dos últimos 7 dias),
      data_limite, criada_em, dias_passados
    """
    meta = doc.get('meta')
    if not meta or not isinstance(meta, dict):
        return None
    tipo = meta.get('tipo')
    if tipo not in ('salas', 'reais'):
        return None
    try:
        alvo = float(meta.get('alvo', 0))
    except (ValueError, TypeError):
        return None
    if alvo <= 0:
        return None

    data_limite_str = meta.get('data_limite', '')
    try:
        data_limite = datetime.strptime(data_limite_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None

    criada_em_str = meta.get('criada_em', '')
    try:
        criada_em = datetime.strptime(criada_em_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        # Sem data de criação válida: assume hoje (mas avisa nos logs)
        criada_em = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # Conta consumo desde a data de criação da meta. Não usamos "total geral" senão
    # cliente que define meta hoje aparece com 100% no primeiro segundo por causa
    # do histórico passado.
    salas_acum = 0
    lucro_acum = 0.0
    if _is_supa_analytics(consumo_col):
        for r in _consumo_rows(str(doc['_id'])):
            if r['ts'] >= criada_em:
                salas_acum += r['qtd']
                lucro_acum += r['qtd'] * r['lucro_unit']
        lucro_acum = round(lucro_acum, 2)
    else:
        pipeline = [
            {'$match': {'doc_id': str(doc['_id']), 'ts': {'$gte': criada_em}}},
            {'$group': {
                '_id': None,
                'salas': {'$sum': '$qtd'},
                'lucro': {'$sum': {'$multiply': ['$qtd', '$lucro_unit']}},
            }},
        ]
        try:
            r = list(consumo_col.aggregate(pipeline))
            if r:
                salas_acum = int(r[0].get('salas', 0))
                lucro_acum = float(r[0].get('lucro', 0.0))
        except Exception as e:
            print(f"[META] Erro agregação: {e}", flush=True)

    atual = float(salas_acum if tipo == 'salas' else lucro_acum)
    percentual = (atual / alvo * 100.0) if alvo > 0 else 0.0

    agora = datetime.now(timezone.utc)
    hoje_0h = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    # Dias restantes incluindo hoje (se prazo é hoje, ainda dá pra fazer o que falta)
    dias_restantes = max(0, (data_limite.date() - hoje_0h.date()).days + 1)
    dias_passados = max(1, (hoje_0h.date() - criada_em.date()).days + 1)

    falta = max(0.0, alvo - atual)
    ritmo_necessario = (falta / dias_restantes) if dias_restantes > 0 else 0.0
    ritmo_atual = atual / dias_passados if dias_passados > 0 else 0.0

    atingida = atual >= alvo
    # Se mantiver o ritmo atual, projeção do que vai ter no fim
    projecao_total = ritmo_atual * (dias_passados + dias_restantes - 1) if dias_restantes > 0 else atual
    vai_bater = atingida or (projecao_total >= alvo)

    return {
        'tipo': tipo,
        'alvo': alvo,
        'atual': round(atual, 2) if tipo == 'reais' else int(atual),
        'percentual': round(percentual, 1),
        'atingida': atingida,
        'data_limite': data_limite_str,
        'criada_em': criada_em.strftime('%Y-%m-%d'),
        'dias_restantes': dias_restantes,
        'dias_passados': dias_passados,
        'ritmo_necessario_dia': round(ritmo_necessario, 2),
        'ritmo_atual_dia': round(ritmo_atual, 2),
        'vai_bater': vai_bater,
        'falta': round(falta, 2) if tipo == 'reais' else int(falta),
    }


def _resolver_doc_logado():
    """Helper: devolve o doc do cliente logado. Usa session['doc_id'] como
    prioridade e cai pra session['client_id'] como fallback. None se nada acha.
    Mesmo padrão do /api/dados — extraído pra não duplicar nos endpoints novos."""
    doc_id = session.get('doc_id')
    client_id = session.get('client_id')
    doc = None
    if doc_id:
        try:
            doc = clientes_col.find_one({'_id': ObjectId(doc_id)})
        except Exception:
            doc = None
    if not doc and client_id:
        doc = clientes_col.find_one({'client_id': client_id})
    return doc


@app.route('/api/lucro')
def api_lucro():
    if not session.get('logado'):
        return jsonify({'ok': False}), 401

    doc_id = session.get('doc_id')
    client_id = session.get('client_id')

    # Resolve doc igual /api/dados (prioridade _id, depois client_id)
    doc = None
    if doc_id:
        try:
            doc = clientes_col.find_one({'_id': ObjectId(doc_id)})
        except Exception:
            doc = None
    if not doc and client_id:
        doc = clientes_col.find_one({'client_id': client_id})

    if not doc:
        return jsonify({'ok': True, 'periodos': {}, 'lucro_unit': 0.0})

    variaveis = doc.get('variaveis', {}) or {}
    lucro_unit = _to_float(variaveis.get('LUCRO_POR_SALA'), 0.0)
    periodos = _periodo_lucro(str(doc['_id']))
    serie_14d = _serie_diaria(str(doc['_id']), 14)
    meta_progresso = _calcular_progresso_meta(doc, lucro_unit)
    # Contadores operacionais de hoje (pagamentos, salas, gos, msgs)
    eventos_hoje = _contagem_eventos_hoje(doc.get('client_id'))

    return jsonify({
        'ok': True,
        'lucro_unit': lucro_unit,
        'saldo_atual': int(doc.get('saldo_salas', 0) or 0),
        'periodos': periodos,
        'serie_14d': serie_14d,
        'meta_progresso': meta_progresso,  # None se cliente não tem meta
        'eventos_hoje': eventos_hoje,  # {PAGAMENTO_CONFIRMADO, SALA_CRIADA, GO_INICIADO, MSG_RESPONDIDA}
    })


@app.route('/api/wallet')
def api_wallet():
    """Status + saldo + extrato da carteira do cliente logado.
    Só responde com dados se WALLET_ATIVO estiver ligado pra esse cliente."""
    if not session.get('logado'):
        return jsonify({'ok': False}), 401
    doc_id = session.get('doc_id')
    client_id = session.get('client_id')
    doc = None
    if doc_id:
        try:
            doc = clientes_col.find_one({'_id': ObjectId(doc_id)})
        except Exception:
            doc = None
    if not doc and client_id:
        doc = clientes_col.find_one({'client_id': client_id})
    if not doc:
        return jsonify({'ok': True, 'ativo': False})

    variaveis = doc.get('variaveis', {}) or {}
    ativo = str(variaveis.get('WALLET_ATIVO', '')).strip().lower() in ('1', 'true', 'sim', 'on', 'ativo', 'yes', 's')
    cid = doc.get('client_id') or client_id
    if not ativo:
        return jsonify({'ok': True, 'ativo': False})

    try:
        from utils import wallet as wallet_mod
        info = wallet_mod.resumo(cid)
    except Exception as e:
        print(f"[WALLET] erro resumo: {e}", flush=True)
        info = {'saldo': 0, 'entradas': 0, 'saidas': 0, 'extrato': [], 'supabase_ok': False}

    return jsonify({
        'ok': True,
        'ativo': True,
        'saldo': info.get('saldo', 0),
        'entradas': info.get('entradas', 0),
        'saidas': info.get('saidas', 0),
        'extrato': info.get('extrato', []),
        'supabase_ok': info.get('supabase_ok', False),
    })


# ═══════════════ WALLET2 (Wallet2) — depósito no painel ═══════════════
def _wallet2_cid_logado():
    """client_id do cliente logado (mesmo usado pela wallet2 no bot)."""
    return session.get('client_id') or ENV_CLIENT_ID or ''


@app.route('/api/wallet2', methods=['GET'])
def api_wallet2():
    """Saldo wallet2 do cliente logado. Só responde ativo se WALLET2_ATIVO
    estiver ligado pra esse cliente (toggle no configadm)."""
    if not session.get('logado'):
        return jsonify({'ok': False}), 401
    try:
        from utils import wallet2 as w2
    except Exception as e:
        return jsonify({'ok': True, 'ativo': False, 'erro': str(e)})
    # Checa o flag por-cliente (mesma fonte do /api/wallet)
    doc_id = session.get('doc_id')
    client_id = session.get('client_id')
    doc = None
    if doc_id:
        try:
            doc = clientes_col.find_one({'_id': ObjectId(doc_id)})
        except Exception:
            doc = None
    if not doc and client_id:
        doc = clientes_col.find_one({'client_id': client_id})
    variaveis = (doc or {}).get('variaveis', {}) or {}
    ativo_cli = str(variaveis.get('WALLET2_ATIVO', '')).strip().lower() in (
        '1', 'true', 'sim', 'on', 'ativo', 'yes', 's')
    cid = _wallet2_cid_logado()
    if not ativo_cli or not cid or not w2.turbofy_ok():
        # Diagnóstico (sem vazar segredo): diz POR QUE a carteira não aparece.
        if not ativo_cli:
            motivo = 'toggle WALLET2_ATIVO desligado pra esse cliente'
        elif not cid:
            motivo = 'sem client_id na sessão'
        elif not w2.turbofy_ok():
            motivo = 'painel sem credenciais Wallet2 (TURBOFY_CLIENT_ID/TURBOFY_SECRET no .env do painel)'
        else:
            motivo = 'desconhecido'
        return jsonify({'ok': True, 'ativo': False, 'motivo': motivo})
    return jsonify({'ok': True, 'ativo': True, 'saldo': w2.saldo(cid),
                    'supabase_ok': w2.supabase_ok()})


@app.route('/api/wallet2/depositar', methods=['POST'])
def api_wallet2_depositar():
    """Gera uma cobrança PIX pra depositar na wallet2. Body: {valor}."""
    if not session.get('logado'):
        return jsonify({'ok': False}), 401
    try:
        from utils import wallet2 as w2
    except Exception as e:
        return jsonify({'ok': False, 'msg': f'wallet2 indisponível: {e}'}), 500
    if not w2.turbofy_ok():
        # Nunca expõe provedor/credencial pro cliente — só o nome do erro.
        return jsonify({'ok': False, 'msg': 'Erro de depósito.'}), 400
    data = request.get_json(silent=True) or {}
    try:
        valor = float(str(data.get('valor', '')).replace(',', '.'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'msg': 'Valor inválido.'}), 400
    if valor < 5:
        return jsonify({'ok': False, 'msg': 'Depósito mínimo R$ 5,00.'}), 400
    cid = _wallet2_cid_logado()
    res = w2.criar_cobranca(cid, valor)
    if not res.get('ok'):
        # Loga o erro real no servidor, mas devolve genérico pro cliente.
        print(f"[WALLET2] erro depósito: {res.get('erro')}", flush=True)
        return jsonify({'ok': False, 'msg': 'Erro de depósito.'}), 502
    return jsonify({'ok': True, 'txid': res.get('txid'),
                    'copia_cola': res.get('copia_cola'), 'qr': res.get('qr')})


@app.route('/api/wallet2/checar', methods=['POST'])
def api_wallet2_checar():
    """Consulta a cobrança; se paga, credita o saldo. Body: {txid}."""
    if not session.get('logado'):
        return jsonify({'ok': False}), 401
    try:
        from utils import wallet2 as w2
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)}), 500
    data = request.get_json(silent=True) or {}
    txid = str(data.get('txid') or '').strip()
    if not txid:
        return jsonify({'ok': False, 'msg': 'sem txid'}), 400
    cid = _wallet2_cid_logado()
    res = w2.checar_e_creditar(cid, txid)
    if not res.get('ok'):
        print(f"[WALLET2] erro checar: {res.get('erro')}", flush=True)
        return jsonify({'ok': False, 'msg': 'Erro de depósito.'}), 502
    return jsonify({'ok': True, 'pago': res.get('pago', False),
                    'saldo': res.get('saldo')})


@app.route('/api/wallet2/sacar', methods=['POST'])
def api_wallet2_sacar():
    """Saca da wallet2 via PIX (payout PIX). Debita o saldo e envia o
    pagamento; se falhar, estorna. Body: {valor, chave, tipo, nome?, doc?}."""
    if not session.get('logado'):
        return jsonify({'ok': False}), 401
    try:
        from utils import wallet2 as w2
    except Exception as e:
        return jsonify({'ok': False, 'msg': f'wallet2 indisponível: {e}'}), 500
    if not w2.turbofy_ok():
        # Nunca expõe provedor/credencial pro cliente — só o nome do erro.
        return jsonify({'ok': False, 'msg': 'Erro de saque.'}), 400
    data = request.get_json(silent=True) or {}
    try:
        valor = float(str(data.get('valor', '')).replace(',', '.'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'msg': 'Valor inválido.'}), 400
    if valor < 1:
        return jsonify({'ok': False, 'msg': 'Saque mínimo R$ 1,00.'}), 400
    chave = str(data.get('chave') or '').strip()
    tipo = str(data.get('tipo') or '').strip()
    if not chave:
        return jsonify({'ok': False, 'msg': 'Informe a chave PIX.'}), 400
    cid = _wallet2_cid_logado()
    res = w2.solicitar_saque(cid, valor, chave, tipo,
                             recipient_name=str(data.get('nome') or ''),
                             recipient_doc=str(data.get('doc') or ''))
    if not res.get('ok'):
        print(f"[WALLET2] erro saque: {res.get('erro')}", flush=True)
        return jsonify({'ok': False, 'msg': 'Erro de saque.',
                        'estornado': res.get('estornado', False)}), 502
    return jsonify({'ok': True, 'batch_id': res.get('batch_id'),
                    'status': res.get('status'), 'saldo': res.get('saldo')})


@app.route('/api/meta', methods=['GET'])
def api_meta_get():
    """Retorna a meta atual + progresso calculado (mesmo objeto que
    /api/lucro retorna em meta_progresso). Pra UI poder recarregar só a
    meta sem precisar do dashboard inteiro."""
    if not session.get('logado'):
        return jsonify({'ok': False}), 401
    doc = _resolver_doc_logado()
    if not doc:
        return jsonify({'ok': True, 'meta': None})
    variaveis = doc.get('variaveis', {}) or {}
    lucro_unit = _to_float(variaveis.get('LUCRO_POR_SALA'), 0.0)
    return jsonify({'ok': True, 'meta': _calcular_progresso_meta(doc, lucro_unit)})


@app.route('/api/meta', methods=['PUT'])
def api_meta_put():
    """Cria/atualiza a meta do cliente. Body JSON: {tipo, alvo, data_limite}.

    Validações:
      - tipo: 'salas' ou 'reais'
      - alvo: número > 0; se tipo='salas', precisa ser inteiro
      - data_limite: 'YYYY-MM-DD' válido e no futuro (ou hoje)

    `criada_em` é sempre setada como HOJE no servidor — não dá pro cliente
    fingir que criou semanas atrás pra inflar progresso.
    """
    if not session.get('logado'):
        return jsonify({'ok': False}), 401

    body = request.get_json(silent=True) or {}
    tipo = body.get('tipo')
    if tipo not in ('salas', 'reais'):
        return jsonify({'ok': False, 'msg': 'Tipo inválido — use "salas" ou "reais".'}), 400

    try:
        alvo = float(body.get('alvo'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'msg': 'Alvo precisa ser um número.'}), 400
    if alvo <= 0:
        return jsonify({'ok': False, 'msg': 'Alvo precisa ser maior que zero.'}), 400
    if tipo == 'salas' and alvo != int(alvo):
        return jsonify({'ok': False, 'msg': 'Pra meta de salas, use número inteiro.'}), 400
    if tipo == 'salas':
        alvo = int(alvo)

    data_limite_str = (body.get('data_limite') or '').strip()
    try:
        data_limite = datetime.strptime(data_limite_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return jsonify({'ok': False, 'msg': 'Data inválida — use formato YYYY-MM-DD.'}), 400

    hoje = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if data_limite.date() < hoje.date():
        return jsonify({'ok': False, 'msg': 'A data limite tem que ser hoje ou no futuro.'}), 400

    doc = _resolver_doc_logado()
    if not doc:
        return jsonify({'ok': False, 'msg': 'Cliente não encontrado.'}), 404

    nova_meta = {
        'tipo': tipo,
        'alvo': alvo,
        'data_limite': data_limite_str,
        'criada_em': hoje.strftime('%Y-%m-%d'),
    }
    clientes_col.update_one({'_id': doc['_id']}, {'$set': {'meta': nova_meta}})
    print(f"[META] {doc.get('client_id', '?')}: nova meta {tipo}={alvo} ate {data_limite_str}", flush=True)

    # Devolve já o progresso calculado pra UI não precisar fazer outro GET
    doc_atualizado = clientes_col.find_one({'_id': doc['_id']})
    variaveis = doc_atualizado.get('variaveis', {}) or {}
    lucro_unit = _to_float(variaveis.get('LUCRO_POR_SALA'), 0.0)
    return jsonify({
        'ok': True,
        'meta': _calcular_progresso_meta(doc_atualizado, lucro_unit),
    })


@app.route('/api/meta', methods=['DELETE'])
def api_meta_delete():
    """Remove a meta do cliente."""
    if not session.get('logado'):
        return jsonify({'ok': False}), 401
    doc = _resolver_doc_logado()
    if not doc:
        return jsonify({'ok': False, 'msg': 'Cliente nao encontrado.'}), 404
    clientes_col.update_one({'_id': doc['_id']}, {'$unset': {'meta': ''}})
    print(f"[META] {doc.get('client_id', '?')}: meta removida", flush=True)
    return jsonify({'ok': True, 'meta': None})


@app.route('/api/dados')
def api_dados():
    if not session.get('logado'):
        print(f"[AUTH-401] /api/dados — cookies recebidos: {list(request.cookies.keys())} | logado={session.get('logado')}", flush=True)
        return jsonify({'ok': False}), 401

    # Parceiros (Shadow/Snoop) - despacha pra view propria
    if session.get('is_partner'):
        pk = session.get('partner_key')
        return jsonify({
            'usuario': session.get('usuario'),
            'is_partner': True,
            'partner_key': pk,
            'nome': PARTNERS.get(pk, {}).get('nome', pk),
            'config': {}, 'variaveis': {},
        })

    client_id = session.get('client_id')
    doc_id = session.get('doc_id')

    # Prioridade 1: busca pelo _id do doc (sempre único, sempre certo)
    doc = None
    if doc_id:
        try:
            doc = clientes_col.find_one({'_id': ObjectId(doc_id)})
        except Exception:
            doc = None

    # Prioridade 2: por client_id (fallback)
    if not doc and client_id:
        doc = clientes_col.find_one({'client_id': client_id})

    # Detecta queda de saldo desde a última visita e registra na coleção 'consumo'.
    # Roda toda vez que o painel é carregado/atualizado. Idempotente — se o saldo
    # não mudou, não faz nada. Custa 1-2 queries extras só.
    if doc and not session.get('is_admin'):
        try:
            _track_consumo_saldo(doc)
        except Exception as e:
            print(f"[CONSUMO] Falha não-fatal no tracking: {e}", flush=True)

    # BACKFILL: cliente sem pix_notif_token ganha um agora (pro app FmedPix
    # funcionar mesmo em cliente criado antes dessa feature).
    if doc and not (doc.get('pix_notif_token') or '').strip():
        try:
            import uuid as _uuid_bf
            novo_tok = _uuid_bf.uuid4().hex
            clientes_col.update_one({'_id': doc['_id']},
                                    {'$set': {'pix_notif_token': novo_tok}})
            doc['pix_notif_token'] = novo_tok
        except Exception as e:
            print(f"[PIX-NOTIF] backfill token falhou: {e}", flush=True)

    # Se não tem no Mongo, lê tudo da env
    if not doc and client_id:
        config = {campo: os.environ.get(campo, '') for campo in CAMPOS_VARIAVEIS}
        return jsonify({
            'usuario': session.get('usuario'),
            'is_admin': session.get('is_admin'),
            'nome': client_id,
            'vencimento': os.environ.get('VENCIMENTO', ''),
            'saldo_salas': int(os.environ.get('SALDO_SALAS', '0') or 0),
            'app_id': os.environ.get('APP_ID', ''),
            'config': config,
            'from_env': True,
        })

    return jsonify({
        'usuario': session.get('usuario'),
        'is_admin': session.get('is_admin'),
        'nome': doc.get('nome', '') if doc else (session.get('usuario') or ''),
        'vencimento': doc.get('vencimento', '') if doc else '',
        'saldo_salas': doc.get('saldo_salas', 0) if doc else 0,
        'saldo_infinito': bool(doc.get('saldo_infinito', False)) if doc else False,
        'app_id': doc.get('app_id', '') if doc else '',
        'config': doc.get('variaveis', {}) if doc else {},
        'ultimo_erro_sala': doc.get('ultimo_erro_sala') if doc else None,
        # Dados pro app Android FmedPix (modo APP de leitura de PIX):
        # o client_id identifica o cliente e o token autentica os POSTs.
        'client_id': doc.get('client_id', '') if doc else (client_id or ''),
        'pix_notif_token': doc.get('pix_notif_token', '') if doc else '',
        'from_env': False,
    })


@app.route('/api/salvar-campo', methods=['POST'])
def api_salvar_campo():
    if not session.get('logado'):
        return jsonify({'ok': False}), 401
    client_id = session.get('client_id')
    doc_id = session.get('doc_id')
    if not client_id and not doc_id:
        return jsonify({'ok': False, 'msg': 'Admin não pode editar por aqui.'}), 403

    data = request.get_json() or {}
    campo = data.get('campo')
    valor = data.get('valor', '')

    if campo not in CAMPOS_VARIAVEIS:
        return jsonify({'ok': False, 'msg': 'Campo inválido.'}), 400

    # Strip de espaço/newline em texto. Token quase sempre vem colado com lixo
    # do clipboard ("MTxxx... " com espaço no fim, ou \n no começo). Bot da
    # discord.py-self rejeita com "Improper token" sem dizer porque.
    # IMAGEM_AUTO pode ser data URL grande — não strip.
    if isinstance(valor, str) and campo != 'IMAGEM_AUTO':
        valor = valor.strip()

    # Validação: IMAGEM_AUTO como data URL não pode passar de ~2.5MB (3MB em base64)
    if campo == 'IMAGEM_AUTO' and valor and valor.startswith('data:') and len(valor) > 3_500_000:
        return jsonify({'ok': False, 'msg': 'Imagem muito grande (máx 2.5MB).'}), 400

    # Validação mínima de token: precisa ter 2 pontos (3 partes separadas por '.')
    # e tamanho razoável. Selfbot precisa de token de USUÁRIO, não bot.
    # NÃO validamos o prefixo (MT/NT/OT/etc) — o prefixo é a primeira parte do
    # user ID codificado em base64, e varia conforme a idade da conta (contas
    # antigas começam com NT, novas com MT, etc). A estrutura interna é opaca.
    if campo == 'DISCORD_TOKEN' and valor:
        if valor.lower().startswith('bot '):
            return jsonify({'ok': False, 'msg': 'Token inválido — selfbot precisa de token de usuário (não "Bot ...").'}), 400
        if valor.count('.') < 2 or len(valor) < 50:
            return jsonify({'ok': False, 'msg': 'Token parece incompleto. Cola o valor inteiro do header authorization.'}), 400

    # Validação RESPOSTAS_RAPIDAS: precisa ser JSON válido de array de objetos
    # com keys 'gatilho' e 'resposta'. Limita a 50 itens pra não estourar .env.
    if campo == 'RESPOSTAS_RAPIDAS' and valor:
        import json as _json
        try:
            arr = _json.loads(valor)
        except Exception as e:
            return jsonify({'ok': False, 'msg': f'JSON inválido: {e}'}), 400
        if not isinstance(arr, list):
            return jsonify({'ok': False, 'msg': 'Respostas rápidas precisa ser uma lista.'}), 400
        if len(arr) > 50:
            return jsonify({'ok': False, 'msg': 'Máximo de 50 respostas rápidas.'}), 400
        for i, item in enumerate(arr):
            if not isinstance(item, dict):
                return jsonify({'ok': False, 'msg': f'Item #{i+1} inválido (precisa ser objeto).'}), 400
            gat = str(item.get('gatilho', '')).strip()
            resp = str(item.get('resposta', '')).strip()
            if not gat:
                return jsonify({'ok': False, 'msg': f'Item #{i+1}: gatilho vazio.'}), 400
            if not resp:
                return jsonify({'ok': False, 'msg': f'Item #{i+1}: resposta vazia.'}), 400
            if len(gat) > 50:
                return jsonify({'ok': False, 'msg': f'Item #{i+1}: gatilho muito longo (máx 50).'}), 400
            if len(resp) > 500:
                return jsonify({'ok': False, 'msg': f'Item #{i+1}: resposta muito longa (máx 500).'}), 400

    # Mesma validação pra MENSAGEM_AUTO_2_ATIVO
    if campo == 'MENSAGEM_AUTO_2_ATIVO':
        v_norm = str(valor or '').strip().lower()
        if v_norm not in ('', '0', '1', 'true', 'false', 'sim', 'nao', 'não', 'on', 'off', 'ativo', 'desativado'):
            return jsonify({'ok': False, 'msg': 'Valor inválido. Use 0/1 ou true/false.'}), 400

    # MENSAGEM_AUTO_2: limita tamanho (Discord aceita até 2000 chars/msg)
    if campo == 'MENSAGEM_AUTO_2' and valor:
        if len(valor) > 1900:
            return jsonify({'ok': False, 'msg': 'Mensagem secundária muito longa (máx 1900 caracteres).'}), 400

    # Prioriza filtro por _id (mais confiável)
    if doc_id:
        try:
            filtro = {'_id': ObjectId(doc_id)}
        except Exception:
            filtro = {'client_id': client_id}
    else:
        filtro = {'client_id': client_id}

    clientes_col.update_one(
        filtro,
        {'$set': {f'variaveis.{campo}': valor}}
    )

    # Dispara aplicação no configurador admin EM BACKGROUND — refaz o .env do
    # bot na Discloud e reinicia. Sem isso o cliente edita aqui, "reinicia" pelo
    # painel, mas o bot continua lendo .env velho.
    # CRÍTICO: roda numa thread pra NÃO bloquear o worker do gunicorn. Antes era
    # síncrono (requests.post timeout=20) e, quando o configadm ficava lento,
    # vários saves seguidos saturavam as threads do worker até o WORKER TIMEOUT
    # derrubar tudo. Agora o save responde na hora e o aplicar acontece depois.
    if not client_id:
        doc = clientes_col.find_one(filtro, {'client_id': 1})
        client_id = (doc.get('client_id') if doc else '') or ''
    if client_id:
        _disparar_aplicar_bg(client_id)

    # 'aplicado' agora é assíncrono: o front não espera mais o resultado aqui.
    return jsonify({'ok': True, 'aplicado': 'em_andamento', 'aplicar_msg': ''})


def get_app_id():
    client_id = session.get('client_id')
    if not client_id:
        return None
    doc = clientes_col.find_one({'client_id': client_id})
    return doc.get('app_id') if doc else None


_STATUS_CACHE = {}        # app_id -> (epoch, dict). Evita martelar a API do Discloud.
_STATUS_CACHE_TTL = 25    # segundos
_STATUS_INFLIGHT = set()  # app_ids com consulta em background rodando agora.
_STATUS_LOCK = threading.Lock()


def _atualizar_status_bg(app_id):
    """Consulta a Discloud em background e atualiza o cache. Roda numa thread
    pra NUNCA bloquear o request do painel. Garante 1 consulta por app_id por
    vez (não acumula threads se a API estiver lenta)."""
    import time as _t
    try:
        resultado = _bot_status_calc(app_id)
        _STATUS_CACHE[app_id] = (_t.time(), resultado)
    except Exception as e:
        print(f"[STATUS-BG] Erro: {e}", flush=True)
    finally:
        with _STATUS_LOCK:
            _STATUS_INFLIGHT.discard(app_id)


def _disparar_status_bg(app_id):
    """Agenda a atualização em background se ainda não há uma rodando."""
    with _STATUS_LOCK:
        if app_id in _STATUS_INFLIGHT:
            return
        _STATUS_INFLIGHT.add(app_id)
    threading.Thread(target=_atualizar_status_bg, args=(app_id,), daemon=True).start()


# ─── Aplicar config no bot (chama o configadm) em BACKGROUND ───
# O endpoint /bot/aplicar do configadm refaz o .env do bot e reinicia. É lento
# (pode levar dezenas de segundos) e às vezes o configadm fica indisponível.
# Por isso roda numa thread: o save do cliente responde na hora e o worker do
# gunicorn nunca fica preso esperando essa chamada.
_APLICAR_INFLIGHT = set()   # client_ids com aplicar rodando agora.
_APLICAR_LOCK = threading.Lock()


def _aplicar_bg(client_id):
    try:
        r = requests.post(
            f'{CONFIG_URL}/bot/aplicar/{client_id}',
            headers={'X-Bot-Secret': BOT_FETCH_SECRET},
            timeout=30,
        )
        try:
            rj = r.json()
        except Exception:
            rj = {}
        if not rj.get('ok'):
            print(f"[APLICAR] {client_id}: {r.status_code} {rj.get('msg','')}", flush=True)
        else:
            print(f"[APLICAR] {client_id}: OK", flush=True)
    except Exception as e:
        print(f"[APLICAR] erro chamando configurador ({client_id}): {e}", flush=True)
    finally:
        with _APLICAR_LOCK:
            _APLICAR_INFLIGHT.discard(client_id)


def _disparar_aplicar_bg(client_id):
    """Dispara o /bot/aplicar em background. Evita acumular várias chamadas pro
    mesmo cliente (se o cliente salva vários campos seguidos, 1 aplicar basta —
    o configadm relê todos os campos do banco de qualquer forma)."""
    with _APLICAR_LOCK:
        if client_id in _APLICAR_INFLIGHT:
            return
        _APLICAR_INFLIGHT.add(client_id)
    threading.Thread(target=_aplicar_bg, args=(client_id,), daemon=True).start()


@app.route('/api/bot/status', methods=['GET'])
def bot_status():
    if not session.get('logado'):
        return jsonify({'ok': False}), 401
    app_id = get_app_id()
    if not app_id:
        return jsonify({'ok': True, 'status': 'offline'})
    import time as _t
    hit = _STATUS_CACHE.get(app_id)
    # Cache fresco → responde na hora.
    if hit and (_t.time() - hit[0]) < _STATUS_CACHE_TTL:
        return jsonify(hit[1])
    # Cache velho/inexistente → NÃO bloqueia o painel esperando a Discloud
    # (que pode levar até ~14s quando rate-limited). Dispara a consulta em
    # background e responde já. O front faz poll a cada 60s e pega o valor
    # atualizado no próximo ciclo.
    _disparar_status_bg(app_id)
    if hit:
        # Tem valor antigo: serve ele (stale-while-revalidate) em vez de "checando".
        return jsonify(hit[1])
    # Primeiríssimo acesso, sem nada em cache ainda.
    return jsonify({'ok': True, 'status': 'checando'})


def _bot_status_calc_square(app_id):
    """Status do bot na SquareCloud (instância Square). Mesma forma de retorno do
    _bot_status_calc da Discloud, pro frontend não precisar saber a diferença."""
    try:
        r = requests.get(f'{SQUARE_API_URL}/apps/{app_id}/status',
                         headers=_square_headers(), timeout=8)
        data = r.json()
        if data.get('status') == 'success':
            resp = data.get('response', {}) or {}
            running = bool(resp.get('running'))
            mem_str = str(resp.get('ram') or '')
            mem_used_mb = None
            try:
                import re as _re
                nums = _re.findall(r'(\d+(?:\.\d+)?)', mem_str)
                if nums:
                    mem_used_mb = float(nums[0])
            except Exception:
                pass
            # Detecção de token inválido pelos logs (SquareCloud).
            token_status, token_detail = 'ok', ''
            try:
                lr = requests.get(f'{SQUARE_API_URL}/apps/{app_id}/logs',
                                  headers=_square_headers(), timeout=6)
                ld = lr.json()
                if ld.get('status') == 'success':
                    tl = (str((ld.get('response', {}) or {}).get('logs', '') or '')[-6000:]).lower()
                    if any(s in tl for s in ('login falhou', 'token invalido', 'token inválido',
                                             'improper token', 'loginfailure')):
                        token_status, token_detail = 'invalid', 'Token Discord rejeitado pelo servidor'
                    elif 'captcha' in tl:
                        token_status, token_detail = 'captcha', 'Discord pediu captcha — conta flagada'
                    elif 'rate limit' in tl and 'login' in tl:
                        token_status, token_detail = 'rate_limit', 'IP rate-limited (aguarde 15-30min)'
            except Exception:
                pass
            return {
                'ok': True,
                'status': 'online' if running else 'offline',
                'memory_used_mb': mem_used_mb,
                'memory_total_mb': None,
                'memory_str': mem_str,
                'cpu': str(resp.get('cpu') or ''),
                'uptime': resp.get('uptime') or '',
                'token_status': token_status,
                'token_detail': token_detail,
            }
    except Exception as e:
        print(f"[STATUS-SQ] Erro: {e}", flush=True)
    return {'ok': True, 'status': 'offline'}


def _bot_status_calc(app_id):
    """Status rico (dict) do bot. Consulta os DOIS provedores: começa pelo
    PROVEDOR_DEPLOY configurado e, se ele NÃO estiver online, tenta o outro —
    o bot pode estar hospedado em qualquer um. Isso corrige o painel mostrando
    'off' pra bot que está ON no outro provedor. Chamado em background por
    _atualizar_status_bg, o request do painel nunca espera por ele."""
    primario, secundario = ((_bot_status_calc_square, _bot_status_calc_discloud)
                            if _usar_square() else
                            (_bot_status_calc_discloud, _bot_status_calc_square))
    res = primario(app_id) or {}
    if res.get('status') == 'online':
        return res
    alt = secundario(app_id) or {}
    if alt.get('status') == 'online':
        return alt
    # Nenhum online: devolve o do provedor configurado (mantém token_detail etc).
    return res or alt or {'ok': True, 'status': 'offline'}


def _bot_status_calc_discloud(app_id):
    """Consulta status + logs na Discloud e devolve um DICT (mesma forma do
    _bot_status_calc_square)."""
    try:
        r = requests.get(f'{DISCLOUD_API_URL}/app/{app_id}/status', headers=DISCLOUD_HEADERS, timeout=8)
        data = r.json()
        if data.get('status') == 'ok':
            apps = data.get('apps', {})
            container = (apps.get('container') or '').lower()
            running = 'online' in container or 'running' in container or container == 'started'
            # Parsing de memória: vem em string tipo "256MB/512MB" ou "256.50MB/512.00MB"
            # ou às vezes "256/512MB". Tira números e divide.
            mem_str = apps.get('memory') or ''
            mem_used_mb = None
            mem_total_mb = None
            try:
                import re as _re
                # Pega os 2 primeiros números (com decimal) na string
                nums = _re.findall(r'(\d+(?:\.\d+)?)', mem_str)
                if len(nums) >= 2:
                    mem_used_mb = float(nums[0])
                    mem_total_mb = float(nums[1])
            except Exception:
                pass

            # === DETECÇÃO DE TOKEN INVÁLIDO ===
            # Quando o token Discord do cliente é invalidado (rotacionado, banido,
            # conta deslogada, copiado errado), o bot loga uma sequência clara antes
            # de cair em _idle_forever. Em vez de mostrar só "Bot online com 0 MB"
            # sem motivo (confuso pro cliente), a gente lê os logs da Discloud e
            # detecta os marcadores que o bot deixa.
            token_status = 'ok'
            token_detail = ''
            try:
                lr = requests.get(f'{DISCLOUD_API_URL}/app/{app_id}/logs', headers=DISCLOUD_HEADERS, timeout=6)
                ldata = lr.json()
                if ldata.get('status') == 'ok':
                    terminal = ldata.get('apps', {}).get('terminal', {}) or {}
                    logs_txt = terminal.get('big') or terminal.get('small') or ''
                    tail = logs_txt[-6000:] if logs_txt else ''
                    tail_lower = tail.lower()
                    if ('login falhou' in tail_lower
                        or 'token invalido' in tail_lower
                        or 'token inválido' in tail_lower
                        or 'improper token' in tail_lower
                        or 'loginfailure' in tail_lower):
                        token_status = 'invalid'
                        token_detail = 'Token Discord rejeitado pelo servidor'
                    elif 'captcha exigido' in tail_lower or 'captcha_key' in tail_lower:
                        token_status = 'captcha'
                        token_detail = 'Discord pediu captcha — conta flagada'
                    elif 'rate limit' in tail_lower and 'login' in tail_lower:
                        token_status = 'rate_limit'
                        token_detail = 'IP da Square Cloud rate-limited (aguarde 15-30min)'
            except Exception:
                pass

            return {
                'ok': True,
                'status': 'online' if running else 'offline',
                'memory_used_mb': mem_used_mb,
                'memory_total_mb': mem_total_mb,
                'memory_str': mem_str,
                'cpu': apps.get('cpu') or '',
                'uptime': apps.get('uptime') or apps.get('startedAt') or '',
                'token_status': token_status,
                'token_detail': token_detail,
            }
    except Exception as e:
        print(f"[STATUS] Erro: {e}", flush=True)
    return {'ok': True, 'status': 'offline'}


@app.route('/api/bot/<action>', methods=['POST'])
def bot_action(action):
    if not session.get('logado'):
        return jsonify({'ok': False}), 401
    if action not in ('start', 'stop', 'restart'):
        return jsonify({'ok': False, 'msg': 'Ação inválida.'}), 400
    app_id = get_app_id()
    if not app_id:
        return jsonify({'ok': False, 'msg': 'App não configurado.'}), 400
    msgs = {'start': 'Bot iniciado.', 'stop': 'Bot parado.', 'restart': 'Bot reiniciado.'}
    if _usar_square():
        try:
            r = requests.post(f'{SQUARE_API_URL}/apps/{app_id}/{action}',
                              headers=_square_headers(), timeout=15)
            data = r.json()
            return jsonify({'ok': data.get('status') == 'success', 'msg': msgs[action]})
        except Exception as e:
            return jsonify({'ok': False, 'msg': str(e)})
    try:
        r = requests.put(f'{DISCLOUD_API_URL}/app/{app_id}/{action}', headers=DISCLOUD_HEADERS, timeout=15)
        data = r.json()
        return jsonify({'ok': data.get('status') == 'ok', 'msg': msgs[action]})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/api/bot/logs', methods=['GET'])
def bot_logs():
    if not session.get('logado'):
        return jsonify({'ok': False}), 401
    app_id = get_app_id()
    if not app_id:
        return jsonify({'ok': True, 'logs': ''})
    if _usar_square():
        try:
            r = requests.get(f'{SQUARE_API_URL}/apps/{app_id}/logs',
                             headers=_square_headers(), timeout=10)
            data = r.json()
            if data.get('status') == 'success':
                return jsonify({'ok': True, 'logs': (data.get('response', {}) or {}).get('logs', '') or ''})
            return jsonify({'ok': True, 'logs': ''})
        except Exception as e:
            return jsonify({'ok': False, 'msg': str(e)})
    try:
        r = requests.get(f'{DISCLOUD_API_URL}/app/{app_id}/logs', headers=DISCLOUD_HEADERS, timeout=10)
        data = r.json()
        if data.get('status') == 'ok':
            apps = data.get('apps', {})
            logs = apps.get('terminal', {}).get('big', '') or apps.get('terminal', {}).get('small', '') or ''
            return jsonify({'ok': True, 'logs': logs})
        return jsonify({'ok': True, 'logs': ''})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/api/emails')
def api_emails():
    """Lista emails de Nubank/Inter/PicPay dos últimos 5 minutos."""
    if not session.get('logado'):
        return jsonify({'ok': False, 'msg': 'não logado'}), 401

    # Pega doc do cliente (mesmo padrão do /api/dados — _id primeiro, client_id depois)
    doc_id = session.get('doc_id')
    client_id = session.get('client_id')
    doc = None
    if doc_id:
        try:
            doc = clientes_col.find_one({'_id': ObjectId(doc_id)})
        except Exception:
            doc = None
    if not doc and client_id:
        doc = clientes_col.find_one({'client_id': client_id})
    if not doc:
        return jsonify({'ok': False, 'msg': 'cliente não encontrado'}), 404

    vars_ = doc.get('variaveis') or {}
    gmail_user = (vars_.get('GMAIL_USUARIO') or '').strip()
    gmail_pass = (vars_.get('GMAIL_SENHA_IMAP') or '').strip()
    if not gmail_user or not gmail_pass:
        return jsonify({'ok': False, 'msg': 'Gmail não configurado neste cliente.'})

    BANCOS = {
        'nubank.com.br': 'Nubank',
        'nu.com.br': 'Nubank',
        'bancointer.com.br': 'Inter',
        'inter.co': 'Inter',
        'picpay.com': 'PicPay',
        'picpay.com.br': 'PicPay',
        # Wise: noreply@wise.com é o remetente padrão das notificações de
        # PIX recebido ("Você recebeu um Pix de X BRL de FULANO").
        # transferwise.com é mantido pra clientes que ainda recebem
        # notificações no domínio antigo.
        'wise.com': 'Wise',
        'transferwise.com': 'Wise',
    }

    def _banco_do_endereco(addr):
        if not addr or '@' not in addr:
            return None
        dom = addr.split('@', 1)[1].lower().strip()
        for d, nome in BANCOS.items():
            if dom == d or dom.endswith('.' + d):
                return nome
        return None

    def _decode_mime(s):
        if not s:
            return ''
        try:
            partes = decode_header(s)
            out = []
            for txt, enc in partes:
                if isinstance(txt, bytes):
                    try:
                        out.append(txt.decode(enc or 'utf-8', errors='replace'))
                    except Exception:
                        out.append(txt.decode('utf-8', errors='replace'))
                else:
                    out.append(txt)
            return ''.join(out)
        except Exception:
            return str(s)

    # Regex de valor: cobre os 2 formatos comuns nas notificações de PIX:
    #   "R$ 5,35"     → Nubank, Inter, PicPay, maioria dos bancos BR
    #   "5,35 BRL"    → Wise (notifica em formato internacional)
    # Aceita opcionalmente "R$" antes OU "BRL" depois. Grupo 1 = valor.
    re_valor = re.compile(
        r'(?:R\$\s*(\d{1,6}(?:[.,]\d{3})*[.,]\d{2})'
        r'|(\d{1,6}(?:[.,]\d{3})*[.,]\d{2})\s*BRL)',
        re.IGNORECASE
    )

    def _valor_to_float(s):
        s = s.strip()
        last_dot = s.rfind('.')
        last_comma = s.rfind(',')
        if last_comma > last_dot:
            s = s.replace('.', '').replace(',', '.')
        else:
            s = s.replace(',', '')
        try:
            return float(s)
        except Exception:
            return None

    try:
        m = imaplib.IMAP4_SSL('imap.gmail.com', 993)
        m.login(gmail_user, gmail_pass)
    except Exception as e:
        return jsonify({'ok': False, 'msg': f'Falha ao conectar no Gmail: {e}'})

    encontrados = []
    try:
        m.select('INBOX', readonly=True)
        # SINCE só aceita data (não hora), então usa hoje e filtra hora no Python depois.
        # Pra cobrir virada de meia-noite, busca desde ONTEM.
        ontem = (datetime.now(timezone.utc) - timedelta(days=1)).strftime('%d-%b-%Y')

        # Encadeia OR pros 3 bancos + AND SINCE
        criterios_from = [f'FROM "{d}"' for d in BANCOS]
        def or_chain(c):
            if len(c) == 1: return c[0]
            return f'OR {c[0]} ({or_chain(c[1:])})'
        crit = f'(SINCE {ontem}) ({or_chain(criterios_from)})'

        typ, data = m.uid('search', None, crit)
        if typ != 'OK' or not data or not data[0]:
            return jsonify({'ok': True, 'emails': []})

        uids = data[0].split()[::-1][:50]  # 50 mais recentes desde ontem, mais que suficiente
        corte = datetime.now(timezone.utc) - timedelta(minutes=5)

        for uid in uids:
            typ, msg_data = m.uid('fetch', uid, '(BODY.PEEK[])')
            if typ != 'OK' or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            if not isinstance(raw, (bytes, bytearray)):
                continue
            try:
                msg = email.message_from_bytes(raw)
            except Exception:
                continue

            # Filtro de tempo: descarta se mais antigo que 5 minutos
            data_raw = msg.get('Date', '')
            try:
                dt = parsedate_to_datetime(data_raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt < corte:
                    # Lista vem ordenada do mais novo pro mais velho — se já passou,
                    # os próximos também passaram. Para.
                    break
                hora_fmt = dt.astimezone().strftime('%H:%M')
            except Exception:
                hora_fmt = '?'
                dt = None

            from_raw = _decode_mime(msg.get('From', ''))
            display, addr = parseaddr(from_raw)
            banco = _banco_do_endereco(addr)
            if not banco:
                continue

            assunto = _decode_mime(msg.get('Subject', '(sem assunto)'))

            # Extrai corpo
            corpo = ''
            try:
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == 'text/plain':
                            payload = part.get_payload(decode=True) or b''
                            charset = part.get_content_charset() or 'utf-8'
                            corpo = payload.decode(charset, errors='replace')
                            break
                    if not corpo:
                        for part in msg.walk():
                            if part.get_content_type() == 'text/html':
                                payload = part.get_payload(decode=True) or b''
                                charset = part.get_content_charset() or 'utf-8'
                                html = payload.decode(charset, errors='replace')
                                corpo = re.sub(r'<[^>]+>', ' ', html)
                                corpo = re.sub(r'\s+', ' ', corpo)
                                break
                else:
                    payload = msg.get_payload(decode=True) or b''
                    charset = msg.get_content_charset() or 'utf-8'
                    corpo = payload.decode(charset, errors='replace')
                    if '<' in corpo:
                        corpo = re.sub(r'<[^>]+>', ' ', corpo)
                        corpo = re.sub(r'\s+', ' ', corpo)
            except Exception:
                corpo = ''

            texto_completo = (assunto + ' ' + corpo)
            valor_email = None
            # findall com 2 grupos retorna tuples ('5,35', '') ou ('', '5,35') —
            # um dos lados é sempre vazio. Pega o que veio preenchido.
            for match in re_valor.findall(texto_completo):
                bruto = match[0] or match[1] if isinstance(match, tuple) else match
                if not bruto:
                    continue
                v = _valor_to_float(bruto)
                if v is not None and (valor_email is None or v > valor_email):
                    valor_email = v

            encontrados.append({
                'hora': hora_fmt,
                'banco': banco,
                'valor': valor_email,
                'remetente': display or addr or '',
                'assunto': assunto[:100],
            })
    finally:
        try:
            m.logout()
        except Exception:
            pass

    return jsonify({'ok': True, 'emails': encontrados})


# ═══════════════════════════════════════════════════════════════════
# RECONCILIAÇÃO DE PAGAMENTOS PIX (MisticPay)
# ═══════════════════════════════════════════════════════════════════
# Quando o webhook da MisticPay não chega (PIX_WEBHOOK_URL não setada,
# rede com problema, MisticPay com atraso, cliente fecha aba antes
# do polling pegar), o pagamento fica preso como PENDENTE no Mongo e o
# cliente nunca recebe as salas — mesmo tendo pagado de verdade.
#
# Solução em 2 camadas:
#  1. Worker em background a cada 2 min: pega pagamentos PENDENTE entre
#     1 min e 24h, consulta /transactions/check da MisticPay, credita se
#     estiver COMPLETO. Idempotente (find_one_and_update no _creditar_salas
#     já tem guard de race).
#  2. Endpoint admin pra forçar reconciliação de um txid — quando o
#     cliente reclama, admin cola o txid e o sistema resolve na hora.
# ═══════════════════════════════════════════════════════════════════
import threading
import time as _rec_time

_RECONCILIADOR_LOCK = threading.Lock()
_RECONCILIADOR_TICK = 120  # segundos entre rodadas
_RECONCILIADOR_IDADE_MIN_S = 60     # ignora < 1 min (polling do front tá tentando)
_RECONCILIADOR_IDADE_MAX_S = 86400  # ignora > 24h (MisticPay já expirou)


def _reconciliar_um_pagamento(pag, ci, cs):
    """Consulta status real na MisticPay e credita se COMPLETO.
    Retorna (estado_atual_str, creditou_agora_bool).
    Não levanta — qualquer falha vira log e retorna ('ERRO', False).
    Idempotente: se já creditado, retorna ('COMPLETO', False).
    """
    if pag.get('creditado'):
        return ('COMPLETO', False)
    txid = pag.get('transactionId')
    if not txid:
        return ('ERRO', False)
    try:
        r = requests.post(
            f'{MISTICPAY_API}/transactions/check',
            headers={'ci': ci, 'cs': cs, 'Content-Type': 'application/json'},
            json={'transactionId': str(txid)},
            timeout=15,
        )
        resp = r.json()
    except Exception as e:
        print(f"[RECONCILIADOR] {txid}: erro check {e}", flush=True)
        return ('ERRO', False)

    if r.status_code != 200:
        msg = resp.get('message', '?') if isinstance(resp, dict) else '?'
        print(f"[RECONCILIADOR] {txid}: HTTP {r.status_code} {msg}", flush=True)
        return ('ERRO', False)

    tx = resp.get('transaction') or {}
    estado = (tx.get('transactionState') or '').upper()

    if estado == 'COMPLETO':
        ok = _creditar_salas(pag)
        if ok:
            print(f"[RECONCILIADOR] ✅ {txid}: creditado {pag.get('qtd')} sala(s)", flush=True)
        return ('COMPLETO', bool(ok))
    if estado == 'FALHA':
        pagamentos_col.update_one({'_id': pag['_id']}, {'$set': {'status': 'FALHA'}})
        return ('FALHA', False)
    if estado == 'CANCELADO':
        pagamentos_col.update_one({'_id': pag['_id']}, {'$set': {'status': 'CANCELADO'}})
        return ('CANCELADO', False)
    return ('PENDENTE', False)


def _reconciliador_tick():
    """Uma rodada do reconciliador. Janela [1min, 24h]. Cap 50 por rodada
    pra respeitar rate limit MisticPay (60 req/min/IP no /check)."""
    if not _RECONCILIADOR_LOCK.acquire(blocking=False):
        return  # tick anterior ainda rodando
    try:
        ci, cs, _ = _get_misticpay_cfg()
        if not ci or not cs:
            return
        agora = datetime.now(timezone.utc)
        janela_min = agora - timedelta(seconds=_RECONCILIADOR_IDADE_MAX_S)
        janela_max = agora - timedelta(seconds=_RECONCILIADOR_IDADE_MIN_S)

        pendentes = list(pagamentos_col.find({
            'creditado': {'$ne': True},
            'status': {'$nin': ['FALHA', 'CANCELADO']},
            'ts': {'$gte': janela_min, '$lte': janela_max},
        }).sort('ts', 1).limit(50))

        if not pendentes:
            return

        creditados = 0
        for pag in pendentes:
            _estado, creditou = _reconciliar_um_pagamento(pag, ci, cs)
            if creditou:
                creditados += 1
            _rec_time.sleep(1.2)  # 50/min margem segura sob 60/min

        if creditados or len(pendentes) > 5:
            print(f"[RECONCILIADOR] tick: {len(pendentes)} verificados, {creditados} creditados", flush=True)
    except Exception as e:
        print(f"[RECONCILIADOR] erro no tick: {e}", flush=True)
    finally:
        _RECONCILIADOR_LOCK.release()


def _reconciliador_loop():
    """Loop infinito — dorme TICK segundos entre rodadas."""
    _rec_time.sleep(30)  # boot delay
    print(f"[RECONCILIADOR] worker iniciado (tick={_RECONCILIADOR_TICK}s)", flush=True)
    while True:
        try:
            _reconciliador_tick()
        except Exception as e:
            print(f"[RECONCILIADOR] erro no loop: {e}", flush=True)
        _rec_time.sleep(_RECONCILIADOR_TICK)


_RECONCILIADOR_THREAD_STARTED = False
_RECONCILIADOR_THREAD_LOCK = threading.Lock()


def _iniciar_reconciliador():
    """Sobe a thread. Idempotente — só sobe uma vez por processo."""
    global _RECONCILIADOR_THREAD_STARTED
    with _RECONCILIADOR_THREAD_LOCK:
        if _RECONCILIADOR_THREAD_STARTED:
            return
        _RECONCILIADOR_THREAD_STARTED = True
    t = threading.Thread(target=_reconciliador_loop, daemon=True, name='pix-reconciliador')
    t.start()


# ═══════════════════════════════════════════════════════════════════
# ENDPOINTS ADMIN: pagamentos pendentes + recrédito manual
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/admin/pagamentos-pendentes')
def api_admin_pagamentos_pendentes():
    """Lista pagamentos não creditados das últimas 72h pra admin revisar.
    Ordenados do mais antigo pro mais novo (quem espera há mais tempo primeiro)."""
    if not session.get('logado') or not session.get('is_panel_admin'):
        return jsonify({'ok': False, 'msg': 'acesso negado'}), 403

    corte = datetime.now(timezone.utc) - timedelta(hours=72)
    pendentes = []

    nome_por_doc = {}
    for c in clientes_col.find({}, {'nome': 1, 'client_id': 1}):
        nome_por_doc[str(c['_id'])] = c.get('nome') or c.get('client_id') or '-'

    for p in pagamentos_col.find({
        'creditado': {'$ne': True},
        'status': {'$nin': ['FALHA', 'CANCELADO']},
        'ts': {'$gte': corte},
    }).sort('ts', 1).limit(100):
        ts = p.get('ts')
        try:
            ts_str = ts.astimezone().strftime('%d/%m %H:%M') if hasattr(ts, 'astimezone') else str(ts)
            idade_min = round((datetime.now(timezone.utc) - ts).total_seconds() / 60, 1) if hasattr(ts, 'astimezone') else None
        except Exception:
            ts_str = str(ts)
            idade_min = None
        pendentes.append({
            'transactionId': str(p.get('transactionId', '')),
            'cliente': nome_por_doc.get(p.get('doc_id', ''), p.get('client_id', '-')),
            'qtd': int(p.get('qtd', 0) or 0),
            'valor': float(p.get('valor', 0) or 0),
            'status': p.get('status', '?'),
            'ts': ts_str,
            'idade_min': idade_min,
        })

    return jsonify({'ok': True, 'pendentes': pendentes, 'total': len(pendentes)})


@app.route('/api/admin/recreditar-pix', methods=['POST'])
def api_admin_recreditar_pix():
    """Força reconciliação de um pagamento específico.
    Body: {transactionId: 'string'}.
    Admin cola o txid (do extrato MisticPay ou da lista de pendentes),
    o endpoint consulta o /check e credita se COMPLETO."""
    if not session.get('logado') or not session.get('is_panel_admin'):
        return jsonify({'ok': False, 'msg': 'acesso negado'}), 403

    data = request.get_json() or {}
    txid = str(data.get('transactionId') or '').strip()
    if not txid:
        return jsonify({'ok': False, 'msg': 'transactionId obrigatório.'}), 400

    pag = pagamentos_col.find_one({'transactionId': txid})
    if not pag:
        # Tenta também como int (defesa em profundidade — alguns clientes
        # antigos podem ter salvo nesse formato)
        try:
            pag = pagamentos_col.find_one({'transactionId': int(txid)})
        except (ValueError, TypeError):
            pag = None
    if not pag:
        return jsonify({'ok': False, 'msg': f'Pagamento {txid} não encontrado no Mongo.'}), 404

    if pag.get('creditado'):
        return jsonify({
            'ok': True,
            'msg': f'Já estava creditado (qtd={pag.get("qtd")}).',
            'ja_creditado': True,
            'qtd': int(pag.get('qtd', 0) or 0),
            'valor': float(pag.get('valor', 0) or 0),
        })

    ci, cs, _ = _get_misticpay_cfg()
    if not ci or not cs:
        return jsonify({'ok': False, 'msg': 'Credenciais MisticPay não configuradas.'}), 503

    estado, creditou = _reconciliar_um_pagamento(pag, ci, cs)

    if creditou:
        return jsonify({
            'ok': True,
            'msg': f'Pagamento confirmado e creditado! {pag.get("qtd")} sala(s) para o cliente.',
            'estado': estado,
            'creditado_agora': True,
            'qtd': int(pag.get('qtd', 0) or 0),
            'valor': float(pag.get('valor', 0) or 0),
        })

    if estado == 'COMPLETO':
        return jsonify({
            'ok': True,
            'msg': 'Pagamento já tinha sido creditado por outro processo (race).',
            'estado': estado,
            'creditado_agora': False,
        })

    return jsonify({
        'ok': False,
        'msg': f'Pagamento ainda está {estado} na MisticPay. Não foi creditado.',
        'estado': estado,
    })


# ═══════════════════════════════════════════════════════════════════
# BOOT do worker
# ═══════════════════════════════════════════════════════════════════
# No gunicorn (produção Discloud), o módulo é importado e nunca cai no
# bloco `if __name__ == '__main__'`. Por isso o worker é iniciado aqui
# em nível de módulo. Idempotente (lock interno).
# Em dev local (python main.py), é iniciado dentro do bloco __main__.
_iniciar_reconciliador()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 80))
    app.run(host='0.0.0.0', port=port)
