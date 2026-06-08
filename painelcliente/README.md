# Bot Painel de Compras

Bot de vendas em Discord com painel em Components V2, integração PIX via MisticPay e persistência em SQLite.

## Comandos

- `/painelcompras` — abre menu admin (Editar Painel / Enviar Painel)

## Fluxo

**Admin:**
1. `/painelcompras` → menu efêmero com 2 botões
2. **Editar Painel** → muda imagem, texto, adiciona/edita/remove produtos
3. **Enviar Painel** → envia o painel público no canal onde o comando foi usado

**Cliente:**
1. Vê painel com imagem por cima + título + lista de produtos + select menu
2. Seleciona produto no menu → bot gera PIX via MisticPay
3. Recebe valor + código copia-cola (ephemeral)
4. Clica **Copiar PIX** → recebe só o código limpo numa mensagem (toca pra copiar, cola no banco)
5. Clica **Já paguei — Verificar** → bot consulta status no MisticPay

## Configurar

1. Cola tokens no `.env`
2. `pip install -r requirements.txt`
3. `python main.py`

## ⚠️ MisticPay

O arquivo `utils/misticpay.py` tem um template do endpoint. Confirme na doc atual do MisticPay:
- URL base correta
- Nomes dos campos no payload (`valor`, `descricao`, etc)
- Nomes dos campos na resposta (`txid`, `copia_cola`, `qrcode`)

Sem `MISTICPAY_TOKEN` no `.env`, o bot gera PIX falso pra testar a UI.

## Estrutura

```
bot-painelcompras/
├── main.py
├── cogs/
│   └── painelcompras.py        # slash /painelcompras
├── views/
│   ├── admin_view.py           # menu admin + modais
│   └── painel_view.py          # painel público (Components V2)
├── utils/
│   ├── database.py             # SQLite
│   └── misticpay.py            # integração PIX
├── .env
├── requirements.txt
└── discloud.config
```
