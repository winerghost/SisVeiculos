# AUDITORIA DE SEGURANÇA — SisVeiculos (AGU)

Data: 2026-11-09. Somente leitura, por 5 agentes revisores (auth/backend, banco, frontend, infra/supply chain, integrações/LGPD) + verificações diretas do coordenador. Nenhum arquivo do projeto foi alterado, nenhuma migration rodada, nenhum segredo impresso.

## Veredito

**Nenhum achado Crítico ou Alto.** Controles centrais corretos: JWT (ES256/RS256/EdDSA, exp/iat/aud/iss, JWKS https), role sempre de `perfis`, MFA imposto no servidor, anti-IDOR, convites (256 bits, só hash, uso único), RLS deny-by-default nas 16 tabelas, 111 funções SECURITY DEFINER com `search_path`, `auditoria` append-only por trigger, buckets privados com URL assinada curta, containers endurecidos, actions fixadas por SHA, sem segredos no working tree nem nos 11 commits, `pip-audit` e `npm audit --omit=dev` limpos.

## Verificações do coordenador
- **A-01 rebaixado para Baixo**: `supabase_repo.py:385` devolve tudo desligado sem linha (falha aberta no código), mas o banco impede: `configuracoes` tem `check (id)` (linha única) e triggers `sem_delete`/`sem_truncate` (migration 20261107120700). Só um acesso privilegiado ao banco removeria a linha. Endurecer o código mesmo assim (defesa em profundidade).
- `service_role` ausente em `frontend/out` (grep). Sem source maps.
- Índice único parcial `ux_requisicoes_veiculo_aberta` existe (migration 20261107120300). Trigger de último admin (AG009) existe e foi reescrito em 6 migrations; falta só teste de concorrência (A-07).
- URLs de leitura: checklist 300 s, fotos 120 s (faixa 60-300 s aprovada). OK.

## Achados consolidados

| ID | Sev. | Local | Resumo |
|---|---|---|---|
| S-01 | Média | `services/relatorios.py:793-812` | Relatório com nomes/trajetos pode ser enviado por e-mail a qualquer staff, inclusive Gmail (LGPD) |
| S-02 | Média | `services/relatorios.py:793-806` | Relatório `auditoria` (só admin) pode ser enviado a autorizador |
| S-03 | Média | `docs/lgpd-localizacao.md:26-44` | Expurgo de 12 meses das coordenadas é manual; sem `pg_cron`/job |
| DB-01 | Média | Painel Supabase | Proteção contra senha vazada desligada |
| DB-02 | Média | `supabase/config.toml` | `enable_signup=true`, senha mín. 6, TOTP off: contraria o CLAUDE.md se aplicado ao hosted (não verificado no painel) |
| I-02 | Média | `backend/requirements.txt` | Transitivas sem lock/hashes |
| I-03 | Média | `backend/Dockerfile:1` | Imagem base por tag, não digest |
| I-01 | Média | `ci.yml:233-241` | `npm audit` sem `--omit=dev`, após o build |
| I-05 | Média | `ci.yml:34` | gitleaks-action pode exigir `GITLEAKS_LICENSE` |
| F1 | Média (aceito) | `security-headers.conf:6`, `lib/supabase.ts:14` | CSP `unsafe-inline` + sessão em localStorage |
| A-01 | Baixa | `supabase_repo.py:385`, `decoradores.py:88` | MFA falha aberta sem linha em `configuracoes` (mitigado pelo banco) |
| A-02 | Baixa | `auth/jwt.py:55-67` | Lock retido durante fetch do JWKS; cooldown gasto em falha |
| A-03 | Baixa | `auth/jwt.py:103-105` | Falha do JWKS vira 401 (desloga todos); deveria ser 503 |
| A-04 | Baixa (não verif.) | firewall VPS | IP real só é confiável se a origem aceitar só Cloudflare |
| I-07 | Baixa | `extensions.py:34` | Backend confia em `CF-Connecting-IP` sem checar origem (nginx sobrescreve) |
| A-05 | Baixa | `extensions.py:47` | Rate limit/controles em memória, 1 worker |
| A-06 | Baixa | `rotas/admin.py:29-35` | `autenticar()` grava acesso antes do role admin |
| A-07 | Baixa (não verif.) | `admin_usuarios.py:251-286` | Último admin: corrida no app; confirmar trigger + teste de concorrência |
| S-04 | Baixa | `notificacoes/n8n.py:83-101` | Anti-SSRF só textual, sem resolver DNS |
| S-05 | Baixa | `n8n.py:139,230` | Sem `N8N_HMAC_SECRET`, HMAC usa o próprio token |
| S-06 | Baixa | `armazenamento.py:189-197` | Fotos de checklist: só magic bytes, sem recodificar |
| S-07 | Baixa | `frontend/src/lib/imagem.ts` | EXIF/GPS removido só no cliente |
| S-08 | Baixa | `email.py:171`, `config.py:172` | `EMAIL_SECURITY=none` aceito em produção |
| S-09 | Baixa | `services/convites.py:174` | Token de convite na query (aceitável) |
| DB-03 | Baixa | `ia_configuracoes`, `ia_uso`, `saudacoes` | RLS sem policy explícita `using (false)` |
| DB-04 | Baixa | `supabase/ops/limpar_dados_de_teste.sql:68` | Padrão apaga a auditoria |
| I-12 | Baixa | `backend/Dockerfile:23` | Access log com IP (dado pessoal) |
| I-06 | Baixa | `ci.yml:243` | Job trivy sem `timeout-minutes` |
| I-04 | Baixa | `ci.yml:233,240` | `playwright install` sem versão fixa |
| F2 / F3 | Baixa | `sw.js`, `auth.tsx:53-54` | Caches sobrevivem ao logout; `signOut` com scope local |

Informativos sem ação: F4-F6, S-10, DB-05, I-08 a I-11, A-08 a A-13.

## Plano de ação

**Fase 0 — antes de produção (você, no painel Supabase; sem código)**
1. Ativar proteção contra senhas vazadas (DB-01).
2. Confirmar signups desligados, MFA/TOTP ativo, senha mínima >= 12, Redirect URLs de produção e expiração do JWT (DB-02, A-08).
3. Firewall do VPS/CloudPanel: 443 só para faixas do Cloudflare ou Authenticated Origin Pulls (A-04/I-07).
4. Confirmar com a AGU: base legal e prazo da localização (S-03), IP em logs (I-12), política de e-mail.

**Fase 1 — LGPD (alto valor, esforço baixo/médio)**
5. S-01/S-02: bloquear `incluir_nomes`/`incluir_trajeto` no envio por e-mail, exigir destinatário institucional e, para relatórios restritos a admin, destinatário admin. Teste pytest para cada caso.
6. S-03: agendar `expurgar_localizacoes_vencidas` (pg_cron ou job CI) + alerta se faltar a linha de auditoria. Nova migration se pg_cron.
7. S-06/S-07: recodificar fotos de checklist no servidor (reaproveitar `imagem_perfil.py`), removendo EXIF/GPS.

**Fase 2 — Robustez do backend**
8. A-01: `ler_configuracoes` sem linha -> erro 503 (fail-closed). Teste.
9. A-03/A-02: JWKS fora do lock; falha de infra -> 503; cooldown só em sucesso.
10. A-07: teste de concorrência do último admin contra o trigger real.
11. S-04/S-05/S-08: allowlist de host e resolução de DNS no n8n; `N8N_HMAC_SECRET` e `EMAIL_SECURITY` obrigatórios em `validar_producao`.
12. A-06: checar role antes de registrar acesso.

**Fase 3 — Supply chain e CI**
13. I-02 lock com hashes (`pip-compile --generate-hashes`, `--require-hashes`); I-03 digest na imagem base; I-01 `--omit=dev` e mover o passo; I-05 licença gitleaks; I-06 timeout; I-04 fixar Playwright.
14. Aproveitar o achado nº 1 da AUDITORIA.md: job `banco` também com `backend/app/repositorios/**`.

**Fase 4 — Defesa em profundidade**
15. DB-03: nova migration com policy `using (false)` nas 3 tabelas (nunca editar migration existente).
16. DB-04: padrão `v_apagar_auditoria := false` em `ops/`; separar `config.toml` de dev e de produção (DB-05).
17. F1: hashes/nonce na CSP para remover `unsafe-inline`; F2/F3: limpar caches e `signOut` global.
18. A-05: Redis/banco para rate limit se escalar além de 1 worker.

## Status pós-correção (2026-11-09)

Executado por 7 agentes de correção (2 rodadas) e validado por 5 de QA/reauditoria. Resultado dos testes no estado final:
- Backend: **1774 passaram, 14 pulados** (integrações desligadas), ruff limpo, sem teste instável em 3 execuções.
- Frontend: lint e build OK, **Playwright 136/136** (inclui 6 testes novos de logout).
- Banco (Supabase local): as 2 migrations novas aplicam e reaplicam sem erro. A suíte SQL tem 26 falhas no working tree contra 27 no HEAD: **nenhuma é regressão** (22 reais já existem no HEAD; o resto é instável por ordem/hora/portas do Docker no Windows).
- Infra: build das 2 imagens OK (lock com hashes + digest), backend em produção falha fechado sem `N8N_HMAC_SECRET`, com HMAC igual ao token ou curto, e com `EMAIL_SECURITY=none`; containers não-root/read-only.

| Status | Achados |
|---|---|
| Fechado | S-01, S-02, S-05, S-06, S-07, S-08, A-01, A-02, A-03, A-06, I-01 a I-06, I-07, I-12, DB-03, DB-04, F2, F3, N-01, N-02, N-03, N-06, N-08, N-09 |
| Parcial | S-03 (agenda via pg_cron; se a extensão faltar só emite NOTICE, N-04), S-04 e N-05 (rebinding de DNS residual; `N8N_ALLOWED_HOSTS` recomendado, não obrigatório), A-07 (teste de serviço + teste SQL de concorrência existente passam; falta teste Flask contra o trigger real), N-07 (teste escrito, só roda contra Supabase local) |
| Aberto, ação manual | DB-01, DB-02 (painel Supabase), A-04 (firewall VPS/Cloudflare), N-04 (conferir `cron.job` após o push) |
| Aberto, risco aceito | F1 (CSP `unsafe-inline`), A-05 (rate limit em memória, 1 worker), S-09 |

### Rodada 3 (investigação das falhas SQL, N-04, N-07, A-07)
- **Falhas SQL pré-existentes: nenhum bug real de banco, nenhuma migration nova.** Eram testes acoplados à ordem ou desatualizados: `test_notificacoes.py::*migration_e_idempotente*` reaplicava a migration `20261110` e devolvia o gatilho `trg_auditoria_notificar` à versão sem avisos de motorista (novo helper `reaplicando_migration()` em `supabase/tests/dbhelpers.py`, com rollback); `reservar_envio_email` passou a deduplicar envios idênticos em 30 s (migration `20261115120000`) e os testes de cota repetiam chamadas idênticas; `ck_auditoria_acao` tem 24 ações; `perfis` tem 3 FKs RESTRICT; `localizacoes_viagem` faltava no TRUNCATE. Suíte SQL completa: 1 a 3 falhas por execução, só de ambiente (Docker/Windows), todas passam ao repetir.
- **N-04 fechado:** o job `expurgar-localizacoes` existe em `cron.job` no stack local (`23 3 1 * *`); o CI agora falha se faltar (passo no job `banco`); consultas de pós-deploy em `docs/deploy-vps.md` e `docs/deploy-banco.md`.
- **N-07 fechado:** testado no Storage local v1.89.13: a segunda PUT com `x-upsert: true` (`true`, `TRUE`, `1`) é recusada e o objeto não é trocado. O status é HTTP 400 com `statusCode: "409"` (`Duplicate`/`KeyAlreadyExists`) no corpo, não 409; o teste de integração foi ajustado para checar o motivo, não só o código.
- **A-07 fechado:** teste SQL de concorrência existente + novo teste com 4 admins em anel desativando-se juntos (invariante: ao menos 1 ativo). O advisory lock do gatilho é o que serializa a corrida.

Desvios: `password_requirements = "letters_digits"` no `config.toml` (a forte quebraria o job de contrato do CI); item (c) de S-02 trocado por "auditoria só por e-mail institucional e só para admin"; cache do front limpa só `paginas-*`.

Achado fora do escopo, a investigar: na suíte SQL, 9 testes de `test_notificacoes_motorista.py` falham também no HEAD (o banco não gera avisos de motorista; possível divergência entre o gatilho `trg_auditoria_notificar` e a migration `20261113120000`), e 10 de `test_relatorios*.py` (`reservar_envio_email`, pode ser estado acumulado). Comparar `pg_get_triggerdef` com a migration e rodar esses arquivos isolados em banco recém-resetado.

### Checklist de deploy
1. Commitar (incluir os não rastreados: `backend/requirements.lock.txt`, `backend/app/services/imagem_checklist.py`, as 2 migrations, os testes novos). Sem o lock, o `COPY` do Dockerfile quebra o build.
2. `supabase db push` pelo pipeline (nunca pelo servidor); depois `select * from cron.job` e habilitar `pg_cron` no painel se faltar.
3. No `.env` da VPS, antes do deploy: `N8N_HMAC_SECRET` (>= 32 caracteres, distinto do token; mesmo valor no n8n) se o n8n estiver ligado; `EMAIL_DOMINIOS_RELATORIO` (ex.: `agu.gov.br`; vazio = relatório com dados pessoais não vai por e-mail); `EMAIL_SECURITY=starttls|ssl` se o e-mail estiver ligado. Opcionais: `N8N_ALLOWED_HOSTS`, `TRUSTED_PROXY_CIDRS`.
4. No servidor: `git pull` e `docker compose up -d --build` (mudou `backend/`, `frontend/`, Dockerfile, lock e `docker-compose.yml`).
5. Painel Supabase: proteção contra senha vazada, signups desligados, MFA/TOTP, senha mínima, Redirect URLs. Firewall: 443 só para o Cloudflare.
6. Pós-deploy: `docker compose ps` com backend `healthy` e logs sem `RuntimeError`.

## Limites desta auditoria
Não verificados: configurações de Auth e grants/policies do Supabase hospedado (o MCP não tem `execute_sql`); HSTS e firewall em produção; resultado real do Trivy; workflow do n8n externo; `GITLEAKS_LICENSE`; CVEs de transitivas e de `supabase/tests/requirements.txt`; pentest dinâmico (nenhum teste ativo foi feito).

## Deploy
Este arquivo é só documentação: não exige `--build` nem mudança no servidor. As correções do plano, quando feitas, exigirão `docker compose up -d --build` (backend/frontend) e `supabase db push` pelo pipeline (migrations).
