# Integração com o site Fmed (PIX via Nubank/MacroDroid)

Esta é uma adição **não-destrutiva** ao bot SalasFF. O bot continua funcionando
exatamente como antes; o módulo do site Fmed é opcional e desligado por padrão.

## Como ativar

Defina apenas a URL do site (token é opcional nesta versão):

**Opção 1 — Environment na Discloud:**
```
PIX_SITE_URL=https://fmediador.discloud.app
PIX_SITE_INTERVAL=3
```

**Opção 2 — `botconfig.json`:**
```json
{
  "pix_site_url":      "https://fmediador.discloud.app",
  "pix_site_interval": 3,
  "pix_site_enabled":  true
}
```

Pronto. Logs do bot vão mostrar:
```
[pix_site] polling ativo em https://fmediador.discloud.app a cada 3s
```

## Como o evento chega no bot

Quando um PIX é detectado, o cliente dispara um evento Discord interno:

```python
@commands.Cog.listener()
async def on_pix_recebido(self, pix: dict):
    # pix = {
    #   "id":         "ab12cd34...",
    #   "valor":      "5,50",         # string com vírgula (formato BR)
    #   "nome":       "Gabriel Fernando",
    #   "raw":        "Você recebeu R$ 5,50 de Gabriel Fernando via Pix",
    #   "recebidoEm": "2026-04-28T03:21:15.597Z",
    # }
    ...
```

Já existe um **stub** em `cogs/comprar.py` (logo após `cog_unload`) com
exemplo comentado de match com pedidos pendentes.

## Token (opcional)

Pra reativar autenticação no futuro:

1. No site `index.js`: restaurar middleware `checarAuth` que valida `X-Auth` header
2. No bot: adicionar `pix_site_token` no `botconfig.json` ou `PIX_SITE_TOKEN` em env

O cliente do bot já manda `X-Auth: <token>` automaticamente quando o token
está configurado.

## Garantias

- **Idempotência:** IDs já vistos não são re-disparados
- **ACK automático:** Após disparar evento, faz `POST /pix/ack`
- **Sem porta exposta:** Compatível com `TYPE=bot` da Discloud
- **Cancelamento gracioso:** Sessão HTTP é fechada no `bot.close()`

## Desligar temporariamente

```json
{ "pix_site_enabled": false }
```

Ou apaga `pix_site_url`. O bot continua rodando normalmente, apenas sem polling.

## Troubleshooting

**"[pix_site] desligado (sem URL/token configurados)"**
→ Falta `pix_site_url` no `botconfig.json` ou env `PIX_SITE_URL`.

**"[pix_site] HTTP 401"**
→ Site tem auth ativada mas o bot não tem o token (ou tem token errado).

**"[pix_site] tick falhou"**
→ Site fora do ar ou rede instável. Loop continua tentando.
