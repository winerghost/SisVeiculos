# CLAUDE.md — Controle de Saída e Chegada de Veículos (AGU)

Sistema PWA para agendar, autorizar e registrar saída/chegada de veículos da AGU.
Dados de servidores e deslocamentos oficiais: tratar como sensíveis (LGPD). Idioma da UI e das respostas: pt-BR.

## Regra de ouro: o backend manda, o frontend só exibe
- Toda regra de negócio e toda validação (campos, permissões, estados, conflito de horário) vivem apenas no backend (Flask) e no banco.
- O frontend exibe dados e envia a intenção do usuário. Sem cálculo de disponibilidade, checagem de permissão ou validação de regra no cliente.
- No cliente só atributos HTML nativos de UX (`required`, `type`). A API sempre revalida; o front mostra a mensagem de erro recebida.
- Botões e ações aparecem conforme o que a API devolve (ex.: `acoes_permitidas` na resposta), nunca por role calculada no cliente.

## Stack
- Frontend: Next.js (App Router) + TypeScript + shadcn/ui (Tailwind), `output: 'export'` servido por nginx-alpine. Apenas client components (sem SSR, route handlers ou Server Actions).
- Backend: Python 3.12 + Flask (factory + blueprints sob `/api`; regra de negócio em `services/`, nunca nas rotas).
- Banco/Auth: Supabase hospedado (Postgres + Auth). O front usa o SDK só para login; os dados passam pelo Flask.
- Automação: n8n externo via webhook (só notifica e agrega; nunca decide autorização). Webhook autenticado por token, chamado de forma assíncrona pelo backend. Infra: Docker com imagens Alpine. CI: GitHub Actions.
- Trade-offs: Front→Flask→Supabase centraliza regras e secrets ao custo de um serviço a mais. Conflito de horário no banco (exclusion constraint) é seguro contra concorrência, mas exige `btree_gist`. Export estático é leve e compatível com Capacitor, mas `NEXT_PUBLIC_*` fica embutido no build.

## Contas e perfis (role vem de `perfis` no banco, nunca do cliente)
- Dois tipos de conta, usuário e administrador, ambas no Supabase Auth com linha em `perfis` (nome, unidade, role, ativo).
- `solicitante` (conta de usuário): cria requisição; vê e cancela as próprias enquanto `solicitada`.
- `autorizador` (usuário da UEA no grupo autorizador): aprova/nega, registra saída e chegada, vê tudo.
- `admin` (conta de administrador): gerencia usuários, grupos, veículos e Configurações. Toda ação administrativa vai para `auditoria`.
- Sem autocadastro: o admin cria ou convida a conta por e-mail, institucional ou Gmail (convite de uso único, com prazo de validade). O primeiro admin nasce por seed/migration, nunca por endpoint público.
- O backend impede o admin de rebaixar ou desativar a si mesmo e garante sempre ao menos um admin ativo.
- Conta inativa não faz login e mantém o histórico (nunca apagar). Senha e redefinição pelo fluxo do Supabase.

## Requisição
- `usuario_logado` vem do JWT no servidor, nunca do body. `nome_solicitante`, `unidade` (PU, PF, CJU, UEA), `hora_saida`, `trajeto` obrigatórios.
- `hora_saida` é a saída **prevista**. A saída e a chegada reais ficam em colunas da própria requisição: `saida_real_em`, `chegada_real_em` (nulas até ocorrerem), `saida_registrada_por`, `chegada_registrada_por`. O autorizador registra ambas.
- `observacao` opcional e limitada; `veiculo_id`; `autorizado_por`, `autorizado_em`. Placa fica em `veiculos`; km e motorista só via recurso opcional.
- `status`: solicitada → autorizada → em_uso → concluida; também negada e cancelada. CHECK `chegada_real_em > saida_real_em`; `em_uso` exige saída real e chegada nula; `concluida` exige as duas. Sem DELETE físico. Sem prazo máximo nem antecedência mínima na v1.

## Regra central: veículo ocupado
Só abre requisição se o veículo estiver livre. Decisão: uma requisição aberta por veículo (sem reserva futura, funciona como fila). O veículo só aceita novo pedido após a conclusão, negativa ou cancelamento da anterior.
- Índice único parcial em `veiculo_id` para status abertos (solicitada, autorizada, em_uso). Sem `hora_chegada_prevista`. Negada, cancelada e concluida liberam o veículo.
- A API traduz o erro do banco em mensagem clara. A UI só exibe a disponibilidade informada pela API.
- Teste de concorrência obrigatório: duas requisições simultâneas, só uma persiste.

## Recursos opcionais (menu de Configurações)
- Desligados por padrão: só funcionam se o `admin` os habilitar em Configurações. Estado na tabela `configuracoes`; cada alteração vai para `auditoria`.
- O backend é a fonte da verdade: com o recurso desligado, a API ignora os campos e recusa os endpoints. O frontend só exibe o que a API informar como habilitado.
- Quilometragem: ligada, km de saída e de chegada são obrigatórios e o km de chegada não pode ser menor que o de saída.
- Abastecimento e manutenção: ligado, registra abastecimentos e manutenções por veículo, com alerta de revisão por km (exige Quilometragem ligada).
- Checklist: ligado, é obrigatório na saída e na chegada (avarias, combustível, fotos). Fotos em Storage privado do Supabase com URL assinada.
- MFA: ligado, o login de `admin` e `autorizador` exige segundo fator (TOTP do Supabase Auth); a verificação é feita no backend. `solicitante` segue só com senha. Desligado, vale só senha para todos.
- Desligar um recurso nunca apaga dados já gravados.

## Banco
- Migrations em `supabase/migrations/`. Tabelas: `perfis`, `veiculos`, `requisicoes`, `auditoria` (append-only, toda transição gera linha).
- RLS ativo em todas as tabelas (deny-by-default), mesmo usando `service_role` no Flask. `timestamptz` sempre, fuso fixo documentado.

## Segurança (inegociável)
- Nunca commitar segredos. `service_role` só no backend; no front, apenas URL e `anon key`. gitleaks no CI.
- Validar JWT (assinatura, exp, aud, iss) em toda rota protegida; checar role no servidor; filtrar por dono (anti-IDOR).
- CORS restrito, rate limit em login e criação, CSP no nginx do frontend e HSTS no Cloudflare, IP real do cliente via `CF-Connecting-IP`, payload limitado, SQL parametrizado, saída sanitizada.
- LGPD: coletar o mínimo, sem dados pessoais em logs, n8n ou IA sem aprovação. Confirmar a política de segurança da AGU antes de produção.
- `pip-audit`, `npm audit`, Trivy; containers não-root, read-only e com versões fixadas. Alpine por padrão; usar `slim` só se uma dependência falhar em musl, informando o motivo.

## PWA e visual
- Visual: shadcn/ui com tokens da AGU (primária `#0F3D75`, foco `#1A5FB4`, erro `#B3261E`, fonte Inter). Raio 8px, alvos de toque ≥ 44px e borda de campo `#8493AB` (contraste ≥ 3:1, mais escura que o padrão do shadcn). Data e hora: `Drawer` com `Calendar` em pt-BR e seletores de hora e minuto; o servidor valida.
- Manifest (`display: standalone`, ícones 192/512 maskable) e service worker: cache-first para assets, network-first para API.
- Sem escrita offline na v1; não cachear dados autenticados de forma persistente. HTTPS obrigatório. Mobile-first, toque ≥ 44px, WCAG/eMAG.

## Deploy (VPS Hostinger)
- No servidor só se roda `git pull` e `docker compose`. Nunca editar arquivos na VPS. O `.env` fica fora do git (criado uma vez a partir de `.env.example`).
- Fluxo: Cloudflare (SSL Full strict) → proxy reverso do CloudPanel → `127.0.0.1:8080` (frontend nginx, que encaminha `/api` ao backend gunicorn). Só o frontend publica porta, e apenas em 127.0.0.1. O n8n não roda nesta stack: o backend só chama webhooks dele.
- O código vai dentro da imagem (sem bind mount). Ao final de cada alteração, informe explicitamente se o servidor precisa de `--build`, decidindo com `git diff --name-only`:
  - `docker compose up -d --build`: mudou `backend/`, `frontend/`, Dockerfile, `requirements.txt`, `package*.json`, `next.config.*`, `SUPABASE_URL` ou `SUPABASE_ANON_KEY`.
  - `docker compose up -d` (sem build): mudou só `.env` do backend ou o `docker-compose.yml`.
  - Migrations: `supabase db push` pelo pipeline, nunca pelo servidor. Mudanças em `nginx.conf` ou `security-headers.conf` já contam como `frontend/` (exigem `--build`).
- Comandos git de leitura (`git diff`, `git status`, `git log`) são livres. Commit e push só quando o usuário pedir.

## Convenções e testes
- Python: type hints, ruff + black, erros em `{"erro": {"codigo", "mensagem"}}`. TS estrito, ESLint + Prettier. Conventional Commits.
- pytest: máquina de estados, permissões, IDOR, sobreposição, concorrência. Playwright: login → solicitar → autorizar → saída → chegada.

## Pontos em aberto (perguntar, não presumir)
- Hoje há um só veículo, mas mantenha a tabela `veiculos` e o `veiculo_id` (o admin cadastra mais no futuro). `hora_saida` é prevista, com saída real registrada à parte?
- Quem registra a chegada? Prazo máximo de uso e antecedência mínima?
- Login por SSO gov.br em vez de e-mail e senha? Campos extras (motorista, placa, km)?

## Como trabalhar
1. Apresente alternativas com trade-offs antes de decisões não triviais e aguarde confirmação.
2. Ordem: migrations + RLS + constraint → auth JWT → CRUD e estados → UI → PWA → n8n → CI/CD → hardening.
3. Mudanças pequenas; rode lint e testes antes de concluir. Nunca desabilite RLS ou validações para "fazer funcionar".
4. Nunca imprima ou commite segredos; se achar um, avise de imediato.
5. Fora do escopo v1: offline com escrita, app em loja e GPS.
6. Antes de criar qualquer tela, consulte `docs/mockups/` (imagens e README) e siga o layout. Os mockups são referência visual, não fonte de regras: em conflito, vale este arquivo.
