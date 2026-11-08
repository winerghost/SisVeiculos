# IA opcional (OpenAI e Anthropic)

Recursos de IA são **opcionais**: o sistema funciona igual com eles desligados. A IA só redige ou
resume; nunca decide autorização, disponibilidade nem validação. Nenhum dado pessoal (nome,
e-mail, placa, trajeto) vai ao provedor.

## Camadas de liga/desliga (a primeira que disser "não" vale)

1. `AI_ENABLED` no `.env` do servidor. `false` (ou ausente) vence tudo.
2. Chave geral (`mestre`) em Configurações → Inteligência artificial.
3. Interruptor da função (hoje: `saudacao`).
4. Cota: por usuário/hora (`AI_RATE_LIMIT_PER_USER`), por unidade/hora (`AI_RATE_LIMIT_PER_UNIT`)
   e teto de tokens por mês (o menor entre `AI_MONTHLY_TOKEN_CAP` e o da tela).
5. Por provedor: chave presente, circuito fechado (`AI_CIRCUIT_*`), timeout (`AI_TIMEOUT_SECONDS`).

Qualquer falha devolve "sem IA" e o chamador usa o texto padrão. Provedor reserva opcional: se o
ativo falhar, tenta o outro antes do texto padrão.

## Variáveis (`.env`, repassadas pelo `docker-compose.yml`)

Ver o bloco "IA opcional" em `.env.example`. Chaves: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` (deixe
vazia a que não for usada). Modelos permitidos por provedor: padrão no código
(`backend/app/ia/config.py`), substituíveis por `AI_MODELOS_OPENAI` / `AI_MODELOS_ANTHROPIC`
(lista separada por vírgulas; o primeiro é o padrão). **Confirme o nome do modelo OpenAI na
documentação do provedor antes de ligar**; o padrão do código é só um ponto de partida.

## Segurança e LGPD

- Chaves só no ambiente do backend: nunca no banco, no frontend, em logs ou em respostas da API
  (a tela só mostra "chave configurada: sim/não").
- URLs dos provedores fixas no código (anti-SSRF). Sem redirecionamento.
- Prompts e respostas nunca são logados nem gravados. `ia_uso` guarda só função, provedor, modelo,
  usuário, unidade, tokens, status e latência (append-only).
- Alterações de configuração vão para `auditoria` (`configuracao_alterada`, `detalhes.recurso`
  começando por `ia.`), com o ator vindo do JWT.

## Atualizar o servidor

- Mudou `backend/`, `frontend/` ou `docker-compose.yml`: `docker compose up -d --build`.
- Mudou só valores de `AI_*` no `.env`: `docker compose up -d`.
- A migration `20261108130000_ia_fundacao.sql` vai por `supabase db push` pelo pipeline, nunca
  pelo servidor.

## Fases

0. Fundação (esta): migration, provedores, cota, circuito, tela de Configurações.
1. Saudação sazonal no login (calendário no backend, textos pré-gerados e aprovados pelo admin).
2. Resumo narrativo em Dashboards/Relatórios (só dados agregados).
3. Ajuda de redação (opt-in por usuário; só com consentimento e aval da AGU).

## Limites conhecidos (aceitos na Fase 0)

- **Cota com folga sob concorrência:** o consumo é lido antes da chamada e registrado depois,
  então requisições simultâneas (vários workers) podem ultrapassar um pouco o limite por hora e o
  teto mensal. O excesso é limitado por `AI_MAX_TOKENS` e pelo número de workers. Se for preciso
  exatidão, reservar a cota antes da chamada exige mudar o modelo de `ia_uso`.
- **Circuito por processo:** com N workers o provedor pode receber até N vezes o limite de falhas
  antes de todos os circuitos abrirem. A cota (no banco) não tem essa limitação.
- **Servidor com `AI_ENABLED=false`:** a tela só permite DESLIGAR (chave geral e funções); ligar
  ou alterar provedor/modelo/teto é recusado.
- **Fase 1:** a saudação deve exigir JWT (sem perfil só vale o teto mensal global) e montar o
  prompt apenas com a data comemorativa, sem nome, unidade, e-mail, placa ou trajeto.
