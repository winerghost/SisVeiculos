# SisVeiculos: Controle de Saída e Chegada de Veículos (AGU)

PWA para agendar, autorizar e registrar a saída e a chegada de veículos da AGU. Os dados de
servidores e de deslocamentos oficiais são sensíveis (LGPD). Idioma da UI e da documentação: pt-BR.

## Regra de ouro: o backend manda, o frontend só exibe

Toda regra de negócio e toda validação (campos, permissões, estados, conflito de horário) vivem
apenas no backend (Flask) e no banco. O frontend exibe dados, envia a intenção do usuário e
mostra a mensagem de erro recebida. Botões e ações aparecem conforme o que a API devolve
(ex.: `acoes_permitidas`), nunca por role calculada no cliente. Detalhes e demais regras:
[CLAUDE.md](CLAUDE.md).

## Stack

- Frontend: Next.js (App Router) + TypeScript + shadcn/ui (Tailwind), `output: 'export'`,
  servido por nginx-alpine (que também encaminha `/api` ao backend).
- Backend: Python 3.12 + Flask (factory + blueprints sob `/api`), gunicorn.
- Banco e autenticação: Supabase hospedado (Postgres + Auth), com RLS em todas as tabelas.
- Automação: n8n externo via webhook (opcional; só notifica, nunca decide autorização).
- Infra: Docker (imagens Alpine, não-root, read-only) e GitHub Actions (gitleaks, pip-audit,
  npm audit, Trivy, lint, testes).

## Estrutura de pastas

```
backend/      API Flask (app/, tests/, Dockerfile, requirements*.txt)
frontend/     Next.js (src/, e2e/, nginx.conf, security-headers.conf, Dockerfile)
supabase/     migrations/ e tests/ (testes de banco em pytest)
scripts/      seed-admin.sh e verificar-antes-do-commit.sh
docs/         documentação operacional e mockups
.github/      workflows de CI
docker-compose.yml, .env.example
```

## Como rodar localmente

Use sempre valores de desenvolvimento no `.env` (copiado de `.env.example`). Nunca cole chaves
em chat nem commite o `.env`.

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
ruff check . && black --check .
pytest
```

### Frontend

```bash
cd frontend
npm ci
npm run lint
npm run build
npx playwright install chromium     # uma vez
npm run test:e2e                    # sobe o export em http://127.0.0.1:3100
```

Se a porta 3100 estiver ocupada, encerre o processo que a usa antes de rodar os testes.

### Banco (testes em `supabase/tests`)

Rode SOMENTE contra um banco local DESCARTÁVEL. Os testes alteram configurações e geram linhas
de auditoria (append-only, não limpáveis).

```bash
supabase start
supabase db reset --local --no-seed
python -m venv .venv-banco && source .venv-banco/bin/activate
pip install -r supabase/tests/requirements.txt
TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \
  pytest supabase/tests -c supabase/tests/pytest.ini --rootdir supabase/tests \
  -m db_local -p no:xdist
```

Nunca aponte `TEST_DATABASE_URL` para o projeto hospedado. Migrations em produção só via
`supabase db push` pelo pipeline ou pela máquina do operador, nunca pela VPS.

## Mapa dos documentos (`docs/`)

| Documento | Assunto |
| --- | --- |
| [docs/deploy-vps.md](docs/deploy-vps.md) | Roteiro operacional da VPS (instalação, atualização, rollback, troubleshooting) |
| [docs/deploy-banco.md](docs/deploy-banco.md) | `supabase db push`, seed do admin, contratos de km e checklist, smokes |
| [docs/deploy-ip-real.md](docs/deploy-ip-real.md) | IP real via `CF-Connecting-IP`, firewall, rate limit, logs do gunicorn |
| [docs/convites-supabase-auth.md](docs/convites-supabase-auth.md) | Convites, Redirect URLs, templates, MFA e logs sem token |
| [docs/seguranca-csp-sessao.md](docs/seguranca-csp-sessao.md) | CSP, sessão em localStorage e plano de endurecimento |
| [docs/mockups/](docs/mockups/README.md) | Referência visual das telas (não é fonte de regras) |

## Antes do primeiro commit

```bash
bash scripts/verificar-antes-do-commit.sh
```

O script confere `.env` ignorado, busca padrões de segredo nos arquivos que seriam versionados
(sem imprimir valores) e mostra o resumo do que seria adicionado. Não executa `git add`,
`commit` nem `push`.

## LGPD e segurança

- Coletar o mínimo; sem dados pessoais em logs, no n8n ou em IA sem aprovação.
- `service_role` só no backend; no frontend, apenas URL e `anon key`.
- Confirmar a política de segurança da AGU antes de produção.
- `docs/security-audit/` descreve vulnerabilidades e NÃO é versionado (está no `.gitignore`).
  Não o publique nem em repositório privado até as correções serem aplicadas.
