# Mudanças aplicadas no painel — Maio 2026

Resolve: **cliente paga, dinheiro entra na MisticPay, mas saldo de salas continua zerado.**

## O que foi adicionado

### 1. Worker de reconciliação (background)

A cada 2 minutos, o painel agora verifica automaticamente pagamentos `PENDENTE`
mais antigos que 1 minuto e mais novos que 24h. Pra cada um, consulta o status
real na MisticPay (`/api/transactions/check`). Se estiver `COMPLETO`, credita
as salas no cliente — mesmo que o webhook nunca tenha chegado.

Idempotente: nunca credita duas vezes (usa o guard `find_one_and_update` do
`_creditar_salas`).

### 2. Aba "PIX Pendentes" no painel admin

Nova aba no painel admin (`adm-tab-pendentes`):

- **Lista pagamentos pendentes** das últimas 72h com idade em min e botão "Resolver"
- **Campo de recrédito manual** — você cola o `transactionId` (do extrato MisticPay ou do Mongo) e clica em "Recreditar"

Endpoints novos:
- `GET /api/admin/pagamentos-pendentes` — lista
- `POST /api/admin/recreditar-pix` — body `{transactionId}`

## Ação OBRIGATÓRIA pós-deploy

### Configurar a env do webhook no Discloud

Sem isso, todo pagamento futuro vai depender só do reconciliador (latência de até 2min).
Com isso, o webhook chega na hora.

No dashboard Discloud do app `painel-clientej`, adicione na seção Variáveis:

```
PIX_WEBHOOK_URL=https://painel-clientej.discloud.app/webhook/misticpay
```

(troque pelo domínio real se for diferente)

### Resolver o caso atual da Promisse

Depois do deploy:
1. Login como admin no painel
2. Aba **PIX Pendentes**
3. Ela vai listar a transação da Promisse (e qualquer outra travada)
4. Clica em **Resolver** → o sistema consulta a MisticPay e credita

Se a transação for mais antiga que 72h e não aparecer:
- Pega o `transactionId` no extrato MisticPay (NÃO é o endToEndId longo,
  é o ID curto interno tipo `31484480`)
- Cola no campo "Recreditar PIX manualmente" e clica

## Arquivos modificados

- `main.py` — adicionado bloco no fim (worker + 2 endpoints)
- `templates/index.html` — adicionado aba "PIX Pendentes" no admin

Nenhuma rota existente foi alterada. Deploy é seguro.
