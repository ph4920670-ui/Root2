# Painel Cliente — Versão Mobile Premium

Transformação do dashboard desktop numa experiência mobile premium (estilo
Nubank / Stripe / Wise / Linear): dark mode, gradientes roxo neon, glassmorphism,
cantos 20-28px e bottom navigation flutuante.

Tudo é **escopado para mobile** (`@media max-width:640px`) — o layout desktop
permanece intacto. Identidade visual, cores (roxo `#a855f7`/`#7c3aed`), tipografia
(Space Grotesk + Manrope) e estilo do tema HELIX original foram mantidos.

## O que mudou em `templates/index.html`

- **Bottom navigation premium**: barra flutuante glass com brilho neon, ícones
  SVG modernos e pill animada no item ativo. Itens: Início, Stats, Carteira
  (quando wallet ativa), Bots. *(Config permanece oculto conforme decisão
  anterior `SEM CONFIGS no site`.)*
- **Nova view `Bots`** (`#view-bots`): card premium do bot com toggle iOS roxo,
  status ao vivo, saldo de salas, grade de métricas (Operações de hoje) e
  controle rápido (Iniciar / Reiniciar / Parar). Espelha os dados já carregados
  pelo dashboard — nunca diverge da fonte de verdade.
- Refinos de cartões/estatísticas/carteira para leitura mobile.

Nenhuma rota de backend foi alterada.

## Mockups (4K)

Renderizados em `mockups/` (viewport 412×915, deviceScaleFactor 3.5):
`mock_inicio.png`, `mock_stats.png`, `mock_carteira.png`, `mock_bots.png`.
