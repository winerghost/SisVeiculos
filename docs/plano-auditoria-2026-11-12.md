# Plano de ação — auditoria de segurança e teste geral (2026-11-12)

Base: AUDITORIA-SEGURANCA.md (2026-11-09, sem Crítico/Alto) + mudanças desde então (Motorista, Vigilante, vigilante_km, relatório com colunas e observação). Foco: o que é novo e os achados abertos.

## Frentes (cada uma em um painel Herdr)
| Painel | Tipo | Escopo |
|---|---|---|
| aud-backend-auth | revisor-seguranca (leitura) | JWT, roles (vigilante/motorista), IDOR, rate limit, CORS, validação de entrada, relatórios (colunas/observação), logs sem PII |
| aud-banco-rls | revisor-seguranca (leitura) | migrations 20261111–20261119: RLS, grants, SECURITY DEFINER, enum, triggers, auditoria, concorrência |
| aud-frontend-infra | revisor-seguranca (leitura) | CSP/nginx, Dockerfiles, compose, CI, PWA/service worker (sem cache de dados autenticados), segredos, NEXT_PUBLIC_* |
| testes-backend | execução | ruff, black, pytest completo |
| testes-frontend | execução | tsc, eslint, prettier, build, Playwright e2e |
| scanners-deps | execução | pip-audit, npm audit, busca de segredos (gitleaks/trivy ausentes: varredura por regex) |

## Regras
- Auditoria somente leitura; nada é corrigido sem aprovação. Nenhum segredo é impresso. Sem `db push`.
- Saída: achados por severidade com arquivo:linha e correção; consolidados em AUDITORIA-2026-11-12.md.
- Limitação: testes SQL e de contrato exigem banco descartável (não disponível).
