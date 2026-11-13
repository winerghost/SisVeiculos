# Auditoria de segurança e teste geral — 2026-11-12

Plano: `docs/plano-auditoria-2026-11-12.md`. Três revisores (backend/auth, banco, frontend/infra) em leitura, mais execução de testes e varreduras. Nada foi corrigido nesta rodada; nenhuma migration aplicada.

## Veredito
**Nenhum achado Crítico ou Alto.** Os controles novos (vigilante, motorista, relatório com colunas e observação) estão corretos: vigilante não autoriza, nega nem cancela (API e banco); RPCs com `revoke` e `search_path` vazio; observação opt-in, fora da prévia e da IA; anti-IDOR do motorista.

## Testes executados
| Verificação | Resultado |
|---|---|
| pytest backend | 1847 passaram, 14 ignorados (integração/contrato sem banco) |
| ruff / black | ok |
| tsc / eslint / prettier | ok |
| next build (export estático) | ok |
| Playwright e2e | 148 passaram |
| pip-audit | sem vulnerabilidades |
| npm audit --omit=dev | 0 vulnerabilidades |
| Segredos (regex em arquivos versionados) | nada encontrado |
| NÃO executados | testes SQL e de contrato (exigem banco descartável), gitleaks e Trivy (ausentes localmente), teste dinâmico, painel Supabase, HSTS/firewall em produção |

## Achados novos
| Sev. | Local | Resumo | Correção sugerida |
|---|---|---|---|
| Média | `services/requisicoes.py:446-473` | Vigilante vê nome, trajeto e unidade de toda requisição autorizada/em uso (LGPD) | Reduzir ao mínimo da portaria (decisão de produto) |
| Média | `auth/decoradores.py:91` | Vigilante e motorista só com senha (sem MFA) | Aceitar e documentar, ou estender MFA |
| Média | `20261117120200_vigilante_requisicoes.sql:198-204`, `20261118120000_vigilante_checklist.sql:111-112` | Vigilante registra saída/chegada mesmo com motorista designado e dispensa AG034/35/36 (localização) | Decidir: é intencional? Se sim, documentar; senão exigir coordenadas |
| Média | `20261117120100_vigilante_km_flag.sql`, `20261118120100_...` | `configuracoes_validar` repete a lista de 11 chaves; flag nova fora dela muda sem auditoria | Derivar a lista ou teste que garanta cobertura |
| Baixa | `services/validacao.py:105` | `nome_solicitante` vem do cliente, sem conferir com `perfis.nome` | Preencher no servidor |
| Baixa | `notificacoes/n8n.py:121-144` | Anti-SSRF textual; sobra rebinding de DNS | Tornar `N8N_ALLOWED_HOSTS` obrigatório em produção |
| Baixa | `20261113120400_...whitelist.sql` | `acao_administrativa`/`acesso_negado` aceitam detalhes livres | Lista branca de chaves |
| Baixa | `20261119120000_relatorios_observacao.sql` | `rel_*` devolve observação só com booleano; `reservar_envio_email` não bloqueia observação por e-mail | Defesa em profundidade no banco |
| Baixa | `20261117120200_vigilante_requisicoes.sql:262-268` | Banco aceita KM do vigilante com `vigilante_km` desligada (só o backend ignora) | Rejeitar no gatilho |
| Baixa | migrations motorista/foto/saudações | `p_ator` vem do backend; banco não prova autoria (risco aceito, pytest é a barreira) | Manter testes de permissão |
| Baixa | `frontend/public/sw.js:97-110`, `lib/auth.tsx:80-83` | HTML de navegação em cache; logout não limpa `assets-*` | Limpar todos os caches no logout |
| Baixa | `ci.yml:34`, `:273-301` | gitleaks pode exigir licença; resultado do Trivy não verificado | Conferir os jobs |

## Achados anteriores ainda abertos (risco aceito ou manual)
CSP com `unsafe-inline` e sessão em localStorage (F1); rate limit em memória com 1 worker (A-05); `CF-Connecting-IP` só confiável com firewall da VPS limitado ao Cloudflare (A-04, não verificado); painel Supabase: senha vazada, signup, TOTP, senha mínima (DB-01/DB-02, não verificado).

## Ordem sugerida de correção
1. Decisões do usuário: vigilante x localização/motorista designado; dados visíveis ao vigilante; MFA para vigilante.
2. Servidor define `nome_solicitante`; `N8N_ALLOWED_HOSTS` obrigatório; limpar caches no logout.
3. Migrations novas (nunca editar): rejeitar KM do vigilante com flag desligada; lista de chaves de `configuracoes_validar`; lista branca de detalhes da auditoria.
4. Rodar testes SQL/contrato em banco descartável, gitleaks e Trivy; conferir painel Supabase, HSTS e firewall da VPS.

## Deploy
Esta rodada só adicionou documentos (não exige `--build`). As migrations 20261117–20261119 seguem para `supabase db push`; `backend/` e `frontend/` já alterados antes exigem `docker compose up -d --build`.
