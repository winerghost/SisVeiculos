# AUDITORIA — SisVeiculos (AGU)

Auditoria somente leitura, feita em 2026-11-08 por 5 agentes (um por etapa). Nenhum arquivo do projeto foi alterado, nenhuma migration foi rodada, nada foi instalado e o Supabase hospedado não foi consultado por SQL (só `list_tables` e `get_advisors`, leitura).

Adaptações ao projeto: não há pagamento, assinatura nem checkout (etapa 4 virou idempotência do n8n, da fila de notificações e de ações repetidas); não há webhook recebido; "papel de admin" e "cota" foram checados contra a regra de ouro do `CLAUDE.md` (backend manda, frontend só exibe).

Legenda: "não verificado" = não foi possível confirmar, com o motivo.

## Top 5 achados de maior risco (em ordem)

| # | Achado | Risco | Etapa |
|---|---|---|---|
| 1 | Nenhum teste liga a rota Flask ao Postgres real. A tradução do erro do índice único em `VeiculoOcupado`, o mapeamento de colunas e o `SupabaseRepositorio` só são provados contra fakes ou em SQL puro. Os testes service+Supabase (concorrência, storage) ficam desligados e usam o projeto remoto sem guarda técnica. | Alto | 1 |
| 2 | Estado crítico em memória que só funciona com `gunicorn --workers 1`: rate limit (`memory://`), controle de reenvio de convite, limite de emissão de upload, circuit breaker de IA, teto/h de e-mail. A invariante está só em comentário, sem guarda no CI. Reiniciar o container zera os limites. | Médio | 3, 4 |
| 3 | Login vai direto ao Supabase Auth pelo SDK, então o Flask não limita tentativas de senha. Proteção contra senha vazada desligada (advisor `auth_leaked_password_protection`). | Médio | 2 |
| 4 | Operações sem atomicidade ou dedupe: reenvio de convite entrega o e-mail antes de revogar/gravar; envio de relatório por e-mail duplica no duplo clique e consome 2 da cota; cota de IA é check-then-act (lê, chama o provedor, grava depois). | Médio | 3, 4 |
| 5 | Webhook n8n sem ID de evento estável (`X-Entrega-Id` é por entrega) e fila só em memória: reinício perde eventos e retentativa após timeout pode duplicar a ação no n8n. | Médio | 4 |

Fora do top 5, mas relevante: `supabase_repo.py` (1388 linhas, 87 métodos) é o candidato nº 1 de refatoração (etapa 5).

## Etapa 1 — Testes de integração

Ferramentas: pytest em `backend/tests` (repositório fake), pytest em `supabase/tests` (SQL real em Postgres local descartável, guarda de 5 camadas em `dbhelpers.py`), Playwright em `frontend/e2e` (API mockada), CI com gitleaks, banco, backend, frontend e trivy.

Resultado de execução:
- Backend (`cd backend && python -m pytest`, Python 3.11.0 local): **1329 passaram, 0 falharam, 3 pulados** (1 por falta do módulo `resource` no Windows, 2 integrações desligadas por padrão).
- `supabase/tests` e Playwright: **não verificado**. Exigem Supabase local (Docker + CLI) e navegador, e não se ligou nenhum banco nesta auditoria.
- Resultado em Python 3.12 (versão do CI): não verificado.

Cobertura por fluxo (A = rota real + banco real; B = rota Flask + repositório fake; C = unitário; D = nada):

| Fluxo | Classe | Arquivos |
|---|---|---|
| Convite/aceite | B + SQL real | `test_admin_convites.py`, `supabase/tests/test_convites.py` |
| Login + MFA | B; TOTP real: D | `test_auth.py`, `test_acessos.py`, `e2e/fluxo.spec.ts` (mock) |
| Criar, autorizar, saída, chegada, cancelar | B + SQL real | `test_requisicoes.py`, `supabase/tests/test_transicoes.py` |
| Veículo ocupado / concorrência | B + SQL real em threads | `supabase/tests/test_veiculo_ocupado.py:71`; `test_integracao_concorrencia.py` desligado |
| Admin CRUD | B + SQL real | `test_admin_*.py`, `test_guarda_admin.py` |
| Notificações / webhook n8n | B + SQL; envio real ao n8n: D | `test_notificacoes*.py` |
| Upload de foto/checklist | B + SQL; storage real só na integração remota | `test_checklist*.py`, `test_foto_perfil*.py` |

Nenhum fluxo é classe A completa (Flask + Postgres real).

| Achado | Risco | Arquivo:linha | Correção sugerida |
|---|---|---|---|
| Nenhum teste exercita a rota Flask contra o Postgres real; divergência fake vs. real passa despercebida | Alto | `backend/tests/conftest.py:82-111` | Teste de contrato rodando criar, autorizar, saída, chegada, cancelar e ocupado com `SupabaseRepositorio` no Supabase local do CI |
| Testes de integração usam `SUPABASE_URL` e service role reais com `RUN_INTEGRATION=1`; só o docstring protege contra produção; não rodam no CI | Alto | `test_integracao_concorrencia.py:1-27`, `test_integracao_storage.py:26` | Guarda técnica (host/projeto de teste, como `dbhelpers.py`) e portar para o Supabase local do CI |
| E2E Playwright só com API mockada; o fluxo real previsto no CLAUDE.md não é testado | Médio | `e2e/mock-api.ts`, `e2e/fluxo.spec.ts:12` | Smoke E2E com Flask + Supabase local (opcional, noturno) |
| Verificação real de TOTP/AAL2 e JWT do gotrue não testadas (JWT de teste vem de chave local) | Médio | `backend/tests/conftest.py:30-48` | Teste com gotrue local ou registrar como risco aceito |
| Job `banco` só roda se `supabase/**` ou o `ci.yml` mudar; mudança em `backend/app/repositorios/**` não o aciona | Médio | `.github/workflows/ci.yml:41-60` | Incluir `backend/app/repositorios/**` nos gatilhos |
| Envio real ao n8n só testado com fake | Baixo/Médio | `test_notificacoes_despachante.py` | Servidor HTTP local simulando n8n (500, timeout, token) |
| Teste de concorrência do backend passa pelo pré-check do serviço, então não prova que o índice decide | Médio | `test_integracao_concorrencia.py` | Chamar `repo.inserir_requisicao` direto nas duas threads |
| Teste SQL de concorrência faz commit logo após o INSERT; a sobreposição não é garantida, ao contrário do que o comentário diz | Baixo | `supabase/tests/test_veiculo_ocupado.py:71` | Segurar a 1ª transação aberta até a 2ª bloquear (via `pg_locks`) |
| Local em 3.11, CI em 3.12; cobertura não medida | Baixo | `ci.yml` backend | Alinhar versão; `pytest-cov` com piso (opcional) |

## Etapa 2 — Confiança no cliente

Frontend: não há regra de negócio, permissão ou limite aplicado só no cliente. Botões vêm de `acoes_permitidas` (`cartao-requisicao.tsx:206`, `admin/usuarios/detalhe/page.tsx:42`, `admin/veiculos/page.tsx:104`, `admin/convites/page.tsx:92`); `role` só aparece como rótulo ou filtro enviado à API; os campos de km e localização vêm de `campos_acao`; `MAX_FOTOS_UI` e `maxLength` são UX (o limite real está no banco e no bucket).

Chaves no navegador:

| Chave | Classificação | Local |
|---|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | Pública por design | `frontend/src/lib/supabase.ts:9` |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Pública por design (RLS nega acesso direto) | `supabase.ts:10` |
| `NEXT_PUBLIC_SITE_URL` | Pública | `app/layout.tsx:12` |

`service_role`, n8n, SMTP e chaves de IA não aparecem em `frontend/`; chegam só ao serviço `backend` por lista explícita no `docker-compose.yml`. Não foram achados segredos reais no histórico (varredura por padrões; gitleaks não rodado).

RLS: 15 tabelas em `public`, todas com RLS ligado. Não há `USING (true)` nem `WITH CHECK (true)`. Em português: as policies `*_authenticated_sem_leitura` e `*_anon_sem_leitura` negam qualquer leitura direta pela API; `*_sem_insert/update/delete` negam qualquer escrita direta; `*_sem_acesso_api` bloqueiam por completo as tabelas de checklist, fotos, acessos, envios de e-mail, notificações e localizações; as policies RESTRICTIVE em `storage.objects` bloqueiam os buckets `checklists` e `avatares` (privados). Quem lê e escreve é o Flask com `service_role`, via RPC `SECURITY DEFINER` com `search_path` fixo e `EXECUTE` só para `service_role`. `auditoria` é append-only.

| Achado | Risco | Arquivo:linha | Correção sugerida |
|---|---|---|---|
| Login direto ao Supabase Auth: sem rate limit no Flask; limite real depende do painel (não verificado) | Médio | `frontend/src/lib/auth.tsx`, `supabase.ts` | Confirmar rate limit e política de senha no painel; considerar CAPTCHA |
| Proteção contra senha vazada desligada | Médio | Supabase Auth (advisor) | Ligar no painel |
| Upload da foto do checklist direto ao Storage pelo SDK, fora do padrão "SDK só para login" (usa URL assinada emitida pelo backend; confirmar que o backend revalida o conteúdo) | Baixo | `components/formulario-checklist.tsx:9,123` | Confirmar que é intencional e documentar a exceção |
| `ia_configuracoes` e `ia_uso` com RLS sem policy (advisor INFO; nega por padrão e há `revoke`) | Baixo | `migrations/20261108130000_ia_fundacao.sql:127-171` | Nova migration com policy deny no padrão das outras (não editar a antiga) |
| Rate limit em memória | Médio | `backend/app/extensions.py:47` | Ver etapa 3 |
| `CF-Connecting-IP` confiado; a garantia é do nginx e do firewall da VPS | Baixo | `extensions.py:34` | Manter firewall restrito ao Cloudflare |
| CSP com `unsafe-inline` (exigência do export estático) | Baixo | `frontend/security-headers.conf:7` | Evoluir para hash ou nonce |
| `motorista` não exige MFA (CLAUDE.md cita só admin e autorizador); registra saída/chegada e envia localização | Baixo | `auth/decoradores.py:87` | Decisão de produto |
| `PERMITIR_AUTOAUTORIZACAO` existe como flag | Baixo | `docker-compose.yml:23` | Manter `false` em produção |
| `.mcp.json` versionado expõe o `project_ref` (identificador, não segredo) | Baixo | `.mcp.json` | Opcional: ignorar no git |
| Histórico git: varredura por padrões, sem gitleaks local | Baixo | — | Confirmar `--log-opts=--all` no gitleaks do CI |
| Backend: JWT valida assinatura, exp, iat, sub, aud, iss; role vem de `perfis`; IDOR coberto; CORS restrito; container não-root e read-only | OK | `auth/jwt.py`, `auth/decoradores.py` | Nenhuma |
| Não verificado: `.env` real (não aberto), painel do Supabase Auth, `grants`/policies individuais no remoto, 48 migrations lidas por varredura e não integralmente, `supabase/pendentes/` | — | — | — |

## Etapa 3 — Race condition

Deploy: `gunicorn` com 1 worker e 8 threads (`backend/Dockerfile:21-23`). Nenhum risco alto: os caminhos críticos usam o banco.

| Recurso | Cenário | Proteção existente | Risco | Arquivo:linha | Correção sugerida |
|---|---|---|---|---|---|
| Veículo ocupado | 2 criações no mesmo veículo | Índice único parcial `ux_requisicoes_veiculo_aberta`; 23505 vira `VeiculoOcupado` | OK | `services/requisicoes.py:518`; `migrations/20261107120300` | Nenhuma |
| Transições de status | autorizar x cancelar, 2x autorizar | `UPDATE ... WHERE status=<anterior>` mais trigger | OK | `supabase_repo.py:923-934` | Nenhuma |
| Transição do motorista | 2 registros | RPC com `FOR UPDATE`, `UNIQUE (requisicao_id, momento)` | OK | `20261112120200_motorista_registra_transicao.sql:257` | Nenhuma |
| Motorista em duas viagens | Mesmo motorista em 2 requisições abertas | Nenhuma restrição própria no banco; decisão do dono do produto: o motorista é associado ao carro e segue a mesma regra do veículo (uma requisição aberta por veículo) | Baixo (decidido: sem restrição extra) | `20261111120100_motorista_recurso.sql:171` | Nenhuma migration. Reavaliar só se um motorista puder ser atribuído a veículos diferentes ao mesmo tempo |
| Aceitar convite 2x | 2 aceites | RPC `consumir_convite` com advisory e `FOR UPDATE` | OK | `20261107121500_correcoes_revisao_5.sql:58` | Nenhuma |
| Último admin ativo / auto-rebaixamento | 2 admins se desativando | Advisory exclusivo no trigger; testes reais | OK | `20261107121400_correcoes_revisao_4.sql:117` | Nenhuma (advisory global serializa toda escrita em `perfis`/`veiculos`) |
| Reenvio de convite | 2 reenvios | `CONTROLE` em memória; e-mail sai antes de revogar/gravar | Médio se escalar workers | `services/convites.py:51-94, 213-216, 252-267` | RPC única `reenviar_convite` atômica; e-mail depois |
| Cota de IA | N chamadas juntas | Lê cota, chama provedor, grava depois (check-then-act) | Médio (custo) | `ia/servico.py:162, 283-301` | RPC `reservar_uso_ia` com advisory por usuário, ou documentar como cota "soft" |
| Rate limit | Brute force | `Flask-Limiter` `memory://` | Médio se escalar | `extensions.py:47` | Redis ou guarda de CI para `--workers 1` |
| Circuit breaker de IA | Falhas concorrentes | Lock em memória (documentado) | Baixo | `ia/circuito.py:16` | Aceito |
| Limite de upload (`LimiteEmissao`) | N URLs assinadas | Dict em memória; `contar_objetos` check-then-act | Baixo | `services/checklist.py:58-100` | Tabela de emissões ou limpeza de órfãos |
| Cota de relatórios por e-mail | N reservas | RPC com advisory; teste de concorrência real | OK | `20261109130000_envios_email_cota_usuario.sql:69` | Nenhuma |
| Checklist duplo | 2 registros | `UNIQUE uq_checklists_requisicao_momento` | OK | `supabase_repo.py:900` | Nenhuma |
| Foto de perfil | 2 trocas | Advisory por usuário e `uq_perfil_fotos_ativa` | OK | `20261107122410_perfil_foto_tabela_e_rpc.sql:106` | Nenhuma |
| Notificações | Evento 2x | `ux_notificacoes_dedupe` e `ON CONFLICT`; `SKIP LOCKED` | OK | `20261110120000_notificacoes.sql:184, 599` | Nenhuma |
| Expurgo de localização | Execução 2x | `DELETE` idempotente por data; quem chama não localizado (não verificado) | Baixo | `20261114120000_localizacao_retencao_12_meses.sql:75` | Opcional: advisory |

Não verificado: os fakes de `backend/tests` (não abertos) e os testes do trigger de ator em perfis sob carga.

## Etapa 4 — Idempotência

- Webhooks recebidos: **não existem**. Todas as rotas são autenticadas por JWT; `nginx.conf` só encaminha `/api/`.
- Gateway de pagamento: **não aplicável**.
- Webhook enviado ao n8n: HMAC com timestamp, token, `X-Entrega-Id`, timeout de 3 s, https obrigatório, anti-SSRF, sem PII em log.

| Achado | Risco | Arquivo:linha | Correção sugerida |
|---|---|---|---|
| Payload sem ID estável do evento; `X-Entrega-Id` é por entrega, então retentativa após timeout pode duplicar a ação no n8n | Médio | `notificacoes/n8n.py:98-106, 189-196` | `evento_id` no corpo e no header; n8n deduplica por ele |
| Fila do n8n só em memória (fila cheia descarta o mais antigo; reinício perde) | Médio | `n8n.py:9, 130, 149` | Outbox no banco, como `notificacoes` |
| Falha definitiva do n8n só incrementa contador e vai a log; `n8n_falha` sem emissor | Baixo | `n8n.py:203`; `services/notificacoes.py:105` | Chamar `registrar_falha_sistema` |
| Envio de relatório por e-mail sem dedupe; duplo clique gera 2 e-mails e 2 cotas; pendentes se perdem no reinício | Médio | `services/relatorios.py:761-802, 812-841` | Dedupe no RPC por janela de ~30 s e reaper de `enviando` travado |
| Reenvio de convite: e-mail antes do banco | Médio | `services/convites.py:213-216` | RPC atômica |
| UNIQUE do checklist coberto pela etapa 3 (existe); RPC `registrar_checklist` e `reservar_envio_email` não lidas por completo | Baixo | — | Não verificado |
| Criar requisição, saída, chegada, cancelar: idempotentes por máquina de estados; repetição legítima vê 409 | Baixo | `services/requisicoes.py:510-549` | Opcional: devolver a existente quando for o mesmo ator |
| Aceitar convite: segundo uso do mesmo usuário recebe "inválido" | Baixo | `services/convites.py:305` | Opcional |
| E-mail de notificação é "pelo menos uma vez" (documentado); corpo genérico | Baixo | `notificacoes/despachante.py:14` | Aceito |
| Foto: URL assinada nova por chamada; órfãos limitados | Baixo | `services/checklist.py:173-191` | Limpeza de órfãos |
| IA: sem retry no código; retries internos do SDK não verificados (`ia/provedores.py` não analisado a fundo) | Baixo | `ia/servico.py:165` | Fixar `max_retries` |
| Bearer do n8n e chave do HMAC são o mesmo segredo | Baixo | `n8n.py:207-216` | Separar os segredos |

## Etapa 5 — Separação de responsabilidades

Rotas Flask limpas (nenhuma regra de negócio ou SQL); services falam só com o repositório; frontend sem regra de negócio. O ponto fraco é o tamanho de poucos arquivos.

| Arquivo | Linhas | Mistura | Risco | Correção sugerida |
|---|---|---|---|---|
| `backend/app/repositorios/supabase_repo.py` | 1388 | Só banco, mas 87 métodos de ~12 domínios numa classe; `ia/repositorio.py` importa `_exec` privado dele | Alto | Dividir por agregado (plano abaixo) |
| `backend/app/services/relatorios.py` | 858 | Catálogo, parsing, montagem de CSV/XLSX e orquestração | Médio | Separar catálogo, parsing, exportação e serviço |
| `backend/app/services/requisicoes.py` | 653 | Regra de negócio; usa só o repositório | Médio | Separar transições e montagem de `acoes_permitidas` |
| `frontend/src/lib/tipos.ts` | 599 | Só tipos e rótulos | Baixo | Dividir por domínio com barrel |
| `frontend/src/components/dashboards/paineis.tsx` | 494 | Tela + fetch, 9 painéis num arquivo | Médio | Um arquivo por painel |
| `backend/app/repositorios/base.py` | 459 | Contrato espelhando o repositório "deus" | Médio | Um Protocol por agregado |
| `frontend/src/components/cartao-requisicao.tsx` | 365 | Tela + chamadas à API; confirmar que `camposDe`/`pendente` vêm da API | Médio | Extrair subcomponentes |
| `backend/app/ia/servico.py` | 327 | Regra + provedor externo | Médio | Adaptador próprio do provedor (não confirmado por leitura integral) |
| `frontend/src/components/formulario-checklist.tsx` | 305 | JSX, validação de arquivo e upload direto ao Storage | Médio | Separar upload em hook |

**Candidato nº 1: `supabase_repo.py`.** Plano em ordem segura, um commit por módulo e suíte verde em cada passo:
1. Registrar o baseline dos testes (incluindo concorrência de veículo ocupado).
2. Extrair `_postgrest.py` (`_exec`, `TIMEOUT_POSTGREST`, tradução de erro) e reexportar os nomes antigos; migrar `ia/repositorio.py`.
3. Extrair leituras puras: `dashboards_repo`, `relatorios_repo`, `notificacoes_repo`.
4. Extrair `perfis_repo`, `convites_repo`, `veiculos_repo`, `auditoria_repo`.
5. Por último `requisicoes_repo` (carrega a tradução do índice de veículo ocupado), rodando o teste de concorrência a cada commit.
6. Dividir `base.py` e atualizar os fakes.
O `supabase_repo.py` vira fachada com a mesma assinatura pública.

## Qual correção rodar primeiro

**Comece pelo item 1 do top 5: teste de contrato Flask + Postgres real no CI (Supabase local), junto com a guarda técnica nos testes `RUN_INTEGRATION`.**

Por quê: é a rede de segurança das demais correções. O item 5 (refatorar `supabase_repo.py`), o item 4 (RPCs atômicas de convite, relatório e IA) e o item 2 mexem justamente na camada que hoje só é testada com fakes. Sem esse teste, uma regressão entre o repositório real e o banco passa verde. Além disso, a mudança é só de teste e CI, sem migration e sem alterar comportamento, então o risco de fazê-la é mínimo.

Ordem sugerida depois: (2) travar a invariante `--workers 1` com guarda no CI e (3) revisar rate limit e senha vazada no painel do Supabase, que são rápidos; em seguida (4) e (5); por fim o refactor do `supabase_repo.py`.

Observação de deploy (CLAUDE.md): este relatório só adiciona o `AUDITORIA.md`, então o servidor não precisa de `--build`.
