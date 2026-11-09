# IA opcional (OpenAI e Anthropic)

Recursos de IA são **opcionais**: o sistema funciona igual com eles desligados. A IA só redige ou
resume; nunca decide autorização, disponibilidade nem validação. Nenhum dado pessoal (nome,
e-mail, placa, trajeto) vai ao provedor.

## Camadas de liga/desliga (a primeira que disser "não" vale)

1. `AI_ENABLED` no `.env` do servidor. `false` (ou ausente) vence tudo.
2. Chave geral (`mestre`) em Configurações → Inteligência artificial.
3. Interruptor da função (`saudacao`, `resumo`).
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

## Teste de conexão (admin)

- `POST /api/admin/ia/testar` (corpo opcional `{"provedor": "openai"|"anthropic"}`; limite de 5 por
  minuto por usuário) faz uma chamada mínima com prompt fixo, sem dado pessoal, usando o provedor e
  o modelo já salvos. Serve para validar chave e modelo antes de ligar a chave geral.
- Exige `AI_ENABLED=true` (senão 404) e chave no servidor; só testa o provedor ativo ou o reserva
  (senão 409). `GET /api/admin/ia` lista em `testes_disponiveis` o que pode ser testado.
- Falha do provedor volta 200 com `ok=false`, `tipo` (`auth`, `limite`, `timeout`, `rede`, `http`,
  `formato`, `cota`, `erro`) e `mensagem` em pt-BR. O texto devolvido pelo modelo é descartado.
- Ignora circuito aberto como bloqueio, mas o resultado o atualiza; passa pela cota; registra uso
  em `ia_uso` com `funcao='teste'` (exige a migration que amplia o CHECK).

## Atualizar o servidor

- Mudou `backend/`, `frontend/` ou `docker-compose.yml`: `docker compose up -d --build`.
- Mudou só valores de `AI_*` no `.env`: `docker compose up -d`.
- A migration `20261108130000_ia_fundacao.sql` vai por `supabase db push` pelo pipeline, nunca
  pelo servidor.

## Fases

0. Fundação (esta): migration, provedores, cota, circuito, tela de Configurações.
1. Saudação sazonal (implementada; ver a seção abaixo).
2. Resumo narrativo em Dashboards/Relatórios (implementado; ver a seção abaixo).
3. Ajuda de redação (opt-in por usuário; só com consentimento e aval da AGU).

## Fase 1: saudação sazonal

Frase curta de boas-vindas no estilo "Acordado, coruja noturna?" ou "Bom dia, {nome}! Café na
mão e bora.", escolhida e montada **no servidor**. O frontend só exibe o texto recebido.

- **Exige JWT** (`GET /api/saudacao`, qualquer perfil ativo), portanto **não aparece na tela de
  login**: só depois de autenticar. O nome vem do perfil (`perfis`), nunca do cliente; usa-se só o
  primeiro nome. Sem nome no perfil, só entram frases sem `{nome}`.
- **Gate:** chave geral (`mestre`) E `saudacao` ligadas em Configurações. Não depende de chave de
  provedor nem de `AI_ENABLED`: o catálogo embutido funciona sem IA. Desligado, responde
  `{"texto": null}` e a UI não mostra nada. Falha de banco também vira `null`.
- **Fuso:** `America/Sao_Paulo` (-03:00 fixo, `FUSO` de `services/dashboards.py`). Instantes com
  outro fuso são convertidos antes de decidir data e hora.
- **Ocasião (maior prioridade vence):** data comemorativa (fixas e móveis Carnaval seg/ter, Sexta
  Santa, Páscoa, Corpus Christi; `dia_servidor` = 28/10) > dia especial (segunda, sexta, fim de
  semana) > período (madrugada 00:00-04:59, manhã 05:00-11:59, tarde 12:00-17:59, noite 18:00-23:59).
- **Escolha estável:** pool = catálogo embutido (`app/saudacao/catalogo.py`) + frases aprovadas no
  banco da ocasião; índice por hash de (usuário, data, ocasião). A mesma pessoa vê a mesma frase
  o dia todo.
- **Curadoria (admin):** tabela `saudacoes` (migration `20261115120100_saudacoes.sql`). Texto de 3
  a 80 caracteres, só o marcador `{nome}`, sem `<`, `>`, links nem quebra de linha. INSERT nasce
  `pendente`; manual é inserida e aprovada em seguida. Transições: pendente para aprovada ou
  rejeitada, e aprovada para rejeitada (e vice-versa); nunca volta a pendente. A auditoria sai de
  trigger do banco (o backend não grava por conta própria).

| Endpoint (admin) | O que faz |
|---|---|
| `GET /api/admin/ia/saudacoes?status=&ocasiao=` | `{"itens": [...], "ocasioes": [...]}`; cada item traz `acoes_permitidas` |
| `POST /api/admin/ia/saudacoes` `{ocasiao, texto}` | frase manual (fica aprovada) |
| `POST /api/admin/ia/saudacoes/gerar` `{ocasiao, quantidade 1..10}` | gera pendentes pela IA (limite 5 por hora por usuário) |
| `POST /api/admin/ia/saudacoes/<id>/aprovar` e `/rejeitar` | decide a frase |

A geração exige `ServicoIA.disponivel('saudacao')` (AI_ENABLED, chaves geral e da função, chave de
provedor). O prompt leva **só a ocasião**: nunca nome, unidade, e-mail, placa ou trajeto. Cada
linha devolvida é validada (3-80 caracteres, sem HTML/link/quebra, só `{nome}`) e deduplicada
contra catálogo e banco; as inválidas são descartadas e contadas em `descartadas`. O uso entra em
`ia_uso` com `funcao='saudacao'`. Falha do provedor ou cota: 502 `ia_falhou` com mensagem em pt-BR.

## Fase 2: resumo narrativo em dashboards e relatórios

Botão "Resumir" no painel e na prévia de relatório: a IA descreve em 2 a 4 frases os números que o
servidor já calculou. Ela só redige: não altera dado, não decide regra e não recomenda ações.

- **Liga/desliga:** chave geral (`mestre`) E `resumo` ligados em Configurações (coluna
  `ia_configuracoes.resumo`, migration `20261115120300_ia_resumo.sql`, que também amplia o CHECK de
  `ia_uso.funcao` para `resumo`). Mesmas regras das demais chaves: com `AI_ENABLED=false` só se
  pode desligar; ligar `resumo` exige a chave geral. Cada alteração vai para `auditoria`.
- **Quem vê o botão:** os GETs `/api/dashboards/<painel>` e `/api/relatorios/<tipo>/previa` trazem
  `resumo_ia` (booleano). É `true` só se `ServicoIA.disponivel('resumo')` (servidor, chave geral e
  resumo) e, na prévia, se o relatório admite resumo (a trilha de auditoria nunca admite).
- **Endpoints (POST, sem corpo, filtros na query string iguais aos do GET):**
  `/api/dashboards/<painel>/resumo` e `/api/relatorios/<tipo>/previa/resumo`. Resposta:
  `{"texto", "gerado_por_ia": true, "gerado_em", "escopo"}`. Reaproveitam a MESMA autorização, os mesmos
  filtros e o mesmo escopo do GET (solicitante só resume os próprios dados). Erros no formato do
  projeto: 409 `ia_indisponivel` (desligado), 502 `ia_falhou` (provedor, cota ou saída
  inválida), 429 (limite), além dos 400/403/404 do painel ou relatório.
- **Escopo do resumo de relatório:** cobre o relatório INTEIRO (até o limite de processamento
  do arquivo, `relatorio_max_linhas`, 5000 por padrão), não só as 50 linhas da prévia. O prompt diz "conjunto
  completo do relatório, N registros" (ou "parcial" se passou do limite) e a resposta traz
  `escopo`, um texto curto para a tela (ex.: "Resumo do relatório completo (1200 registros)").
  O GET da prévia traz `escopo` fixo ("Resumo do relatório completo, não só das linhas da
  prévia") quando `resumo_ia` é `true`, senão `null`. No painel, `escopo` é "Resumo dos números
  do painel com os filtros atuais".
- **Limite e cache:** 10 gerações por hora por usuário (`chave_usuario`, depois da autenticação).
  Cache em memória por processo, 5 minutos, com chave = hash de (usuário, papel, painel ou
  relatório, filtros e números do prompt). Mudou um número ou um filtro, gera de novo. Saídas
  inválidas não entram no cache. A cada gravação o cache poda as entradas vencidas e guarda no
  máximo 512 (descarta as mais antigas). O cache não ultrapassa o gate: com o recurso desligado responde 409.
- **O que o prompt contém (lista branca em `services/resumo_ia.py`):** contagens, percentuais,
  durações (média, mediana, máxima), situação das requisições, código da unidade (PU, PF, CJU,
  UEA), período e filtros. O prompt é montado no servidor a partir dos agregados do serviço, nunca de
  dados do cliente. **Nunca** vão: nomes, e-mails, placas, trajetos, motoristas, observações,
  localização, ids de qualquer tipo, rótulos de veículo, auditoria. Relatórios são consultados
  com `incluir_nomes` e `incluir_trajeto` forçados a falso, mesmo que a query peça.
- **Células pequenas:** todo total ou contagem abaixo de 3 (inclusive totais de requisições,
  checklists, viagens, soma por grupo e o pico da série) vira "menos de 3"; zero continua
  "0". Percentuais só aparecem quando a base e o numerador têm 3 ou mais. Estatística de tempo
  com menos de 3 casos é omitida; horas de uso e km só com 3 usos ou viagens. Veículos entram
  só em conjunto.
- **Saída:** o modelo é instruído a descrever apenas os números dados, copiando os valores como
  aparecem. O servidor valida (10 a 600 caracteres, uma linha, sem `<`, `>`, `@`, `{`, `}`,
  `http`, `www.` nem padrão de domínio como `agu.gov.br`; "Bom dia." passa) e descarta o que
  fugir (502). Também descarta o texto que cita um número de 2 ou mais dígitos, ou um
  percentual, que não esteja no prompt (milhar "1.200" equivale a 1200; números de 1 dígito
  passam). Isso barra números inventados, não um erro de interpretação: a tela deve mostrar o
  texto como "gerado por IA" ao lado dos números reais.
- **Registro:** `ia_uso` com `funcao='resumo'` (usuário, unidade, tokens, status e latência); nunca
  o prompt nem o texto, que também não vão a logs.
- **Deploy:** aplique a migration `20261115120300_ia_resumo.sql` ANTES de subir o backend com
  `--build`. Sem ela, ler/gravar `ia_configuracoes` não conhece `resumo` e o registro em `ia_uso`
  com `funcao='resumo'` viola o CHECK (a geração continua, mas não conta na cota).

## Limites conhecidos (aceitos na Fase 0)

- **Cota com folga sob concorrência:** o consumo é lido antes da chamada e registrado depois,
  então requisições simultâneas (vários workers) podem ultrapassar um pouco o limite por hora e o
  teto mensal. O excesso é limitado por `AI_MAX_TOKENS` e pelo número de workers. Se for preciso
  exatidão, reservar a cota antes da chamada exige mudar o modelo de `ia_uso`.
- **Circuito por processo:** com N workers o provedor pode receber até N vezes o limite de falhas
  antes de todos os circuitos abrirem. A cota (no banco) não tem essa limitação.
- **Servidor com `AI_ENABLED=false`:** a tela só permite DESLIGAR (chave geral e funções); ligar
  ou alterar provedor/modelo/teto é recusado.
- **Fase 1:** a saudação exige JWT e o prompt de geração leva só a ocasião (ver a seção acima).

## Ordem de deploy do teste de conexão (revisão de segurança)

- **Aplique a migration `20261111120200_ia_uso_funcao_teste.sql` ANTES de subir o backend com
  `--build`.** Sem ela, o INSERT em `ia_uso` com `funcao = 'teste'` viola o CHECK; o teste continua
  funcionando, mas deixa de contar na cota por usuário/unidade/mês (a falha só aparece no log
  `ia_uso_nao_registrado`).
- **Antes do `supabase db push`**, rode `supabase migration list`. Se já existirem migrations
  remotas com timestamp posterior (ex.: `20261112*`/`20261113*`), o push recusa uma migration mais
  antiga; nesse caso renomeie a ainda não aplicada para um timestamp maior que o último remoto.
- **Efeitos aceitos:** o teste consome a cota da hora do admin e da unidade dele, e uma falha de
  teste conta no circuito do provedor (um teste com sucesso fecha um circuito aberto). O limite de
  5 testes por minuto é por processo (memória): com vários workers o teto efetivo é maior; o gasto
  real é limitado pela cota por hora, pelo teto mensal de tokens e por `AI_MAX_TOKENS`.
