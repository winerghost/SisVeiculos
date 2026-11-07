# Deploy na VPS (Hostinger + CloudPanel)

Roteiro operacional. Regras fixas: na VPS só se roda `git pull` e `docker compose`; nunca se
editam arquivos do repositório lá; o `.env` fica fora do git; migrations nunca passam pela VPS.

Cadeia: Cloudflare (SSL Full strict) -> proxy reverso do CloudPanel -> `127.0.0.1:8080`
(nginx do frontend) -> `/api` para o gunicorn (rede interna do compose). Só o frontend publica
porta, e apenas em `127.0.0.1`.

## 1. Primeira instalação

1. Pré-requisitos: Docker e Docker Compose plugin; site criado no CloudPanel como proxy reverso
   para `http://127.0.0.1:8080`; Cloudflare em SSL Full (strict); migrations já aplicadas e
   admin semeado (ver [deploy-banco.md](deploy-banco.md)).
2. Clonar o repositório:
   `git clone <url-do-repositorio> sisveiculos && cd sisveiculos`
3. Criar o `.env` uma única vez, preenchendo direto no arquivo (nunca cole chaves em chat):
   `cp .env.example .env && chmod 600 .env && nano .env`
4. Subir: `docker compose up -d --build`

### Variáveis do `.env` da VPS

| Variável | Observação |
| --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | Pública. Vira build arg e é embutida no frontend. Deve ser `https://...`; o build falha se não for. O host também define a CSP. |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Pública (anon key). Build arg. Mudar exige `--build`. |
| `SUPABASE_URL` | Backend. |
| `SUPABASE_SERVICE_ROLE_KEY` | SECRETA. Só no backend. |
| `SUPABASE_JWKS_URL` | Obrigatória. Sem ela o backend não fica saudável. |
| `SUPABASE_JWT_ISSUER` | Obrigatória (mesma observação). |
| `SUPABASE_JWT_AUDIENCE` | Obrigatória. |
| `CORS_ORIGINS` | Origem https do site (ex.: `https://SEU-DOMINIO`). Restrita, sem curinga. |
| `URL_ACEITE_CONVITE` | `https://SEU-DOMINIO/convite`. Sem ela, criar usuário responde 503 `convites_nao_configurados`. Precisa estar nas Redirect URLs do Supabase ([convites-supabase-auth.md](convites-supabase-auth.md)). |
| `PERMITIR_AUTOAUTORIZACAO` | Manter `false`. |
| `LOG_LEVEL` | Opcional. |
| `N8N_WEBHOOK_URL`, `N8N_WEBHOOK_TOKEN` | Opcionais; ligam só se os dois estiverem preenchidos. URL https pública; token com 32+ caracteres. Sem dados pessoais no payload. |

### Variáveis SOMENTE do pipeline (GitHub Secrets; NÃO vão para a VPS)

`SUPABASE_PROJECT_REF`, `SUPABASE_ACCESS_TOKEN`, `SUPABASE_DB_PASSWORD`, `SEED_ADMIN_EMAIL`,
`DATABASE_URL` (conexão direta, só no ambiente do operador/pipeline) e `GITLEAKS_LICENSE`
(só se o repositório for de organização). O compose repassa ao backend apenas a lista
explícita acima (sem `env_file`).

## 2. Verificação

```bash
docker compose ps                      # backend e frontend "healthy"
curl -sS http://127.0.0.1:8080/api/saude
curl -sSI http://127.0.0.1:8080/login | head -20
curl -sSI https://SEU-DOMINIO/login    # pelo Cloudflare/CloudPanel
docker compose logs --tail=50 backend frontend
```

Conferir:

- `/api/saude` responde 200; `/login` responde 200.
- Headers presentes: `Content-Security-Policy`, `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`,
  `Cross-Origin-Resource-Policy`; e `Strict-Transport-Security` vindo do Cloudflare (HSTS).
- Logs sem PII: linhas do gunicorn no formato `IP MÉTODO CAMINHO STATUS BYTES TEMPO`, sem query
  string; nenhum e-mail, nome, token de convite ou URL assinada nos logs.
- Porta publicada só em loopback: `docker compose ps` deve mostrar `127.0.0.1:8080->8080`.

## 3. Atualização de rotina

```bash
git pull
git diff --name-only HEAD@{1} HEAD     # o que mudou neste pull
```

Se `HEAD@{1}` não servir (ex.: primeiro pull), use `git diff --name-only <commit-anterior> HEAD`.

| Mudou | Comando |
| --- | --- |
| `backend/`, `frontend/`, qualquer Dockerfile, `requirements.txt`, `package*.json`, `next.config.*`, `SUPABASE_URL` ou `SUPABASE_ANON_KEY`/`NEXT_PUBLIC_*` | `docker compose up -d --build` |
| `frontend/nginx.conf` ou `frontend/security-headers.conf` (contam como `frontend/`) | `docker compose up -d --build` |
| Só `.env` (variáveis do backend) ou só `docker-compose.yml` | `docker compose up -d` (sem build) |
| `supabase/migrations/` | Pipeline `supabase db push`, nunca pela VPS (ver ordem abaixo) |
| Só docs, README, scripts, `.github/` | Nada a fazer no servidor |

Na dúvida entre os dois primeiros casos, use `--build`: é seguro, só mais lento.

### Ordem segura de release

1. Migrations primeiro: `supabase db push --dry-run`, revisar, `supabase db push` (pipeline ou
   máquina do operador). Migrations devem ser compatíveis com o código antigo ainda em
   execução (aditivas), pois há uma janela entre o push e o build.
2. Só depois, na VPS: `git pull` e `docker compose up -d --build`.
3. Rodar a verificação (seção 2) e o checklist (seção 5).

Nunca faça deploy de código que usa uma migration ainda não aplicada.

## 4. Rollback

Código:

```bash
git log --oneline -n 10                # achar a tag/commit estável anterior
git checkout <tag-ou-commit-anterior>
docker compose up -d --build
```

(Fica em HEAD destacado; para voltar à versão atual: `git checkout main && git pull` e novo
`--build`. Não edite arquivos na VPS.) Prefira, quando possível, reverter o commit no
repositório (`git revert`) e fazer `git pull`, mantendo a VPS em `main`.

Migrations NÃO têm rollback automático. Como proceder:

- Não edite migration já aplicada. Corrija com uma NOVA migration (aditiva) e `db push` pelo
  pipeline.
- Auditoria, checklists e fotos são imutáveis/append-only; não tente apagá-los.
- Se o código novo quebrou mas a migration é compatível com o código antigo, basta o rollback
  de código acima. Se a migration for incompatível, a saída é uma migration corretiva, não
  voltar o banco.
- Antes de uma migration arriscada, confirme que há backup recente (seção 6).

## 5. Checklist pós-deploy

1. Login de um usuário de teste funciona (e MFA, se estiver ligado).
2. Fluxo de requisição: solicitar, autorizar, registrar saída e chegada (mensagem clara ao
   tentar uma segunda requisição para veículo ocupado).
3. Convite: enviar convite de teste e conferir os dois formatos de link aceitos, o que cai em
   `/convite?convite=<token>` com `#access_token=` e o link de redefinição (conta já existente,
   template "Reset password"). Se cair no Site URL, revise as Redirect URLs.
4. Bucket `checklists` privado (`public=false`) e acesso de `authenticated` negado (smoke em
   [deploy-banco.md](deploy-banco.md)).
5. Recursos opcionais (Quilometragem, Abastecimento/manutenção, Checklist, MFA) ficam
   DESLIGADOS. Ligue só depois de tudo acima validado, um por vez, em Configurações. MFA: cada
   admin/autorizador cadastra o TOTP antes de ligar (ver convites-supabase-auth.md, seção 8).
6. Conferir `docker compose ps` e logs sem PII.

## 6. Pontos de operação

- Rate limit: em memória, por processo. O gunicorn roda com 1 worker + 8 threads de propósito;
  não aumente workers sem mover o contador para armazenamento compartilhado. Login passa pelo
  Supabase Auth: configure os Rate Limits lá e, se quiser, regras no Cloudflare.
- Firewall: restrinja 80/443 da VPS somente aos IPs do Cloudflare (ou use Authenticated Origin
  Pulls). A 8080 já escuta só em loopback. Detalhes: [deploy-ip-real.md](deploy-ip-real.md).
  Sub-rede do compose (`172.29.88.0/24`) deve ser igual no compose e no `nginx.conf`.
- Logs do proxy: no CloudPanel, sem `access_log` (ou com `$uri` no lugar de `$request`) em
  `/convite` e `/api/admin/usuarios`, e atenção ao `error_log`. No Cloudflare, mascare
  `ClientRequestURI`/`ClientRequestQuery` dessas rotas (token de convite e busca por nome/e-mail
  são dados sensíveis).
- Backup: ative e teste restauração dos backups do Supabase (banco) e defina retenção de fotos
  do Storage conforme a política (LGPD). A VPS é descartável: não guarda dados (sem volumes);
  guarde cópia segura do `.env` fora do servidor, em cofre de segredos.
- Rotação de chaves: ao trocar `SUPABASE_SERVICE_ROLE_KEY`, JWKS/chaves de JWT, token do n8n ou
  senha do banco, atualize o `.env` e rode `docker compose up -d` (sem build). Se trocar
  `NEXT_PUBLIC_SUPABASE_URL`/`ANON_KEY`, use `--build`. Revogue a chave antiga e, se houve
  suspeita de vazamento, avise a equipe de segurança.
- Revisar periodicamente as faixas de IP do Cloudflare em `frontend/nginx.conf` (exige `--build`).

## 7. Troubleshooting (erros já vistos)

- Backend `unhealthy` / `docker compose up` não sobe o frontend: costuma ser variável ausente,
  em especial `SUPABASE_JWKS_URL` e `SUPABASE_JWT_ISSUER` (também `SUPABASE_JWT_AUDIENCE`,
  `CORS_ORIGINS`). Veja `docker compose logs backend` (sem copiar valores para chats/tickets),
  corrija o `.env` e rode `docker compose up -d`. O frontend depende do backend saudável.
  Confirme com `docker compose config` que as variáveis não vieram vazias (cuidado: o comando
  imprime valores; não compartilhe a saída).
- Erro de escrita / `HOME`: o container é read-only e o backend usa `HOME=/tmp` com `/tmp` em
  tmpfs. Não remova `read_only`, o `tmpfs` nem o `HOME=/tmp`; se algo precisar gravar, deve ser
  em `/tmp`.
- Build do frontend falha sem `package.json` / `npm ci` não acha o lockfile: o contexto é
  `./frontend`; confira se o `git pull` trouxe `frontend/package.json` e `package-lock.json` e
  se o `.dockerignore` não os exclui. Também falha se `NEXT_PUBLIC_SUPABASE_URL` não for
  `https://...` ("NEXT_PUBLIC_SUPABASE_URL invalida").
- Testes do frontend (Playwright) falham com porta 3100 ocupada: um `serve` anterior ficou
  vivo. Encerre o processo que usa a 3100 e rode de novo.
- Convidado cai no login/home em vez de `/convite`: Redirect URLs do Supabase não casaram
  (ver convites-supabase-auth.md, seção 2). Convite expirado: usar "Reenviar".
- 502/504 no site: `docker compose ps`; backend caído ou reiniciando. Veja os logs.
- Rede em conflito com a sub-rede `172.29.88.0/24`: trocar nos dois arquivos
  (compose e `nginx.conf`) via commit no repositório, nunca na VPS.
