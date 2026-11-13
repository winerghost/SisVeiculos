# Deploy na VPS (Hostinger + CloudPanel)

Roteiro operacional. Regras fixas: na VPS só se roda `git pull` e `docker compose`; nunca se
editam arquivos do repositório lá; o `.env` fica fora do git; migrations nunca passam pela VPS.

Cadeia: Cloudflare (SSL Full strict) -> proxy reverso do CloudPanel -> `127.0.0.1:<FRONTEND_PORT>`
(nginx do frontend; padrão 8080) -> `/api` para o gunicorn (rede interna do compose). Só o
frontend publica porta, e apenas em `127.0.0.1`. **O proxy do CloudPanel aponta SEMPRE para o
frontend**; o backend não publica porta no host (não existe `127.0.0.1:8000`).

### VPS com vários sistemas (checagens ANTES do primeiro `up`)

```bash
# 1) Porta do host livre? (escolha uma sem resposta e ponha em FRONTEND_PORT no .env)
ss -ltn | grep -E ':(8080|18080)\s' || echo "8080 e 18080 livres"
# 2) Sub-rede do compose (10.213.88.0/24) livre? Não pode haver rede/rota igual.
docker network ls -q | xargs -r docker network inspect --format '{{.Name}} {{range .IPAM.Config}}{{.Subnet}} {{end}}'
ip route | grep -F '10.213.88.' || echo "sub-rede livre"
# 3) Nome do projeto: o compose usa `name: sisveiculos` (containers sisveiculos-*), independente da pasta.
docker ps -a --format '{{.Names}}' | grep -E '^sisveiculos-' || echo "sem conflito de nome"
```

Se a porta 8080 estiver ocupada, defina outra em `FRONTEND_PORT` (ex.: `18080`) no `.env`; basta
`docker compose up -d` (sem `--build`) e usar `http://127.0.0.1:18080` no proxy do CloudPanel.
Se a sub-rede `10.213.88.0/24` colidir, veja o troubleshooting (precisa de commit no repositório).

## 1. Primeira instalação

1. Pré-requisitos: Docker e Docker Compose plugin; site criado no CloudPanel como proxy reverso
   para `http://127.0.0.1:<FRONTEND_PORT>` (frontend; padrão 8080); Cloudflare em SSL Full (strict); migrations já aplicadas e
   admin semeado (ver [deploy-banco.md](deploy-banco.md)).
2. Clonar o repositório:
   `git clone <url-do-repositorio> sisveiculos && cd sisveiculos`
3. Criar o `.env` uma única vez, preenchendo direto no arquivo (nunca cole chaves em chat):
   `cp .env.example .env && chmod 600 .env && nano .env`
   (inclua `NEXT_PUBLIC_SITE_URL` antes do primeiro build; veja a tabela abaixo)
4. Subir: `docker compose up -d --build`

### Variáveis do `.env` da VPS

| Variável | Observação |
| --- | --- |
| `FRONTEND_PORT` | Porta do host (somente 127.0.0.1) onde o frontend escuta; padrão 8080. É a porta do proxy reverso do CloudPanel. Escolha uma livre (ver checagens acima). Mudar só isto exige `docker compose up -d` (sem `--build`). |
| `NEXT_PUBLIC_SUPABASE_URL` | Pública. Vira build arg e é embutida no frontend. Deve ser `https://...`; o build falha se não for. O host também define a CSP. |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Pública (anon key). Build arg. Mudar exige `--build`. |
| `NEXT_PUBLIC_SITE_URL` | **Obrigatória**, pública, build arg. URL https do site, sem barra final (ex.: `https://SEU-DOMINIO`). Usada nas URLs absolutas de `og:image`/`og:url`. Defina **ANTES do primeiro build**; o build falha se estiver vazia, for `http://`, tiver caminho ou apontar para localhost. Mudar exige `--build`. |
| `SUPABASE_URL` | Backend. |
| `SUPABASE_SERVICE_ROLE_KEY` | SECRETA. Só no backend. |
| `SUPABASE_JWKS_URL` | Obrigatória. Sem ela o backend não fica saudável. |
| `SUPABASE_JWT_ISSUER` | Obrigatória (mesma observação). |
| `SUPABASE_JWT_AUDIENCE` | Obrigatória. |
| `CORS_ORIGINS` | Origem https do site (ex.: `https://SEU-DOMINIO`). Restrita, sem curinga. |
| `URL_ACEITE_CONVITE` | `https://SEU-DOMINIO/convite`. Sem ela, criar usuário responde 503 `convites_nao_configurados`. Precisa estar nas Redirect URLs do Supabase ([convites-supabase-auth.md](convites-supabase-auth.md)). |
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
curl -sS http://127.0.0.1:${FRONTEND_PORT:-8080}/api/saude
curl -sSI http://127.0.0.1:${FRONTEND_PORT:-8080}/login | head -20
curl -sSI https://SEU-DOMINIO/login    # pelo Cloudflare/CloudPanel
docker compose logs --tail=50 backend frontend
```

Conferir:

- `/api/saude` responde 200; `/login` responde 200.
- Headers presentes: `Content-Security-Policy`, `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`,
  `Cross-Origin-Resource-Policy`, `X-Robots-Tag: noindex, nofollow`; e
  `Strict-Transport-Security` vindo do Cloudflare (HSTS).
- Cada header aparece **uma vez**. O vhost do CloudPanel pode adicionar os seus; já foi observado
  duplicado em produção: remova do vhost as linhas `add_header` de `X-Frame-Options`,
  `Referrer-Policy` e `X-Content-Type-Options` (o nginx do frontend é a fonte única). Conferir:
  `curl -sSI https://SEU-DOMINIO/login | sort | uniq -d` não deve listar headers repetidos.
- Meta tags: `curl -sS https://SEU-DOMINIO/login | grep -o '<meta property="og:[^>]*>'` deve
  mostrar `og:url` e `og:image` com `https://SEU-DOMINIO/...` (nunca localhost).

## 2.1 Pré-visualização de links

`X-Robots-Tag: noindex, nofollow` mantém o sistema fora de buscadores, mas **não impede** a
pré-visualização em WhatsApp, Teams e Telegram (eles leem as meta tags `og:*`). Não crie
`robots.txt` com `Disallow`: o Twitterbot o respeita (o card do X não apareceria) e o `noindex`
nunca seria lido.

Testar com os user-agents dos crawlers, na raiz, em `/login` e na imagem. Esperado: `200` e, na
imagem, `Content-Type: image/png`:

```bash
for ua in "facebookexternalhit/1.1" "WhatsApp/2.23.20" "Twitterbot/1.0" \
          "TelegramBot (like TwitterBot)" "Slackbot-LinkExpanding 1.0" "LinkedInBot/1.0" \
          "Mozilla/5.0 (compatible; Teams)"; do
  for p in / /login /og/og-agu-v1.png; do
    printf '%s %s -> ' "$ua" "$p"
    curl -sS -o /dev/null -A "$ua" -w '%{http_code} %{content_type}\n' "https://SEU-DOMINIO$p"
  done
done
```

Se algum retornar 403/503 ou página de desafio, o Cloudflare está barrando o crawler. Verifique
Security > Bots (Bot Fight Mode / Super Bot Fight Mode) e Security > WAF/Events. Se bloquear, crie
uma regra de skip por user-agent (Security > WAF > Custom rules, ação *Skip* para as proteções
de bots/WAF) **apenas** para o caminho `/`, `/login` ou `/og/*` e os user-agents acima. Não
libere `/api` nem o resto do site.

Limpar caches de preview após trocar texto ou imagem:

- Facebook/Instagram: Sharing Debugger (developers.facebook.com/tools/debug), "Scrape Again".
- LinkedIn: Post Inspector (linkedin.com/post-inspector), "Inspect".
- Telegram: enviar o link ao bot `@WebpageBot` para renovar.
- WhatsApp: não há ferramenta; o cache só renova com o tempo ou com URL nova. Por isso a imagem
  é versionada (`/og/og-agu-v1.png`): ao trocar a arte, publique como `v2` e atualize a meta tag.

Cache: o nginx serve `/og/*` com `Cache-Control: public, max-age=31536000, immutable`, seguro
porque o nome é versionado (nunca sobrescreva `v1` com outra arte). `/icons/` não recebe cache
longo, pois os nomes não têm versão/hash.
- Logs sem PII: linhas do gunicorn no formato `IP MÉTODO CAMINHO STATUS BYTES TEMPO`, sem query
  string; nenhum e-mail, nome, token de convite ou URL assinada nos logs.
- Porta publicada só em loopback: `docker compose ps` deve mostrar `127.0.0.1:<FRONTEND_PORT>->8080`.

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
7. Expurgo de localizações (LGPD). Habilite a extensão `pg_cron` no painel do Supabase
   (Database > Extensions) ANTES do `supabase db push`; sem ela o agendamento não é criado.
   Depois do push, no SQL Editor do hosted, confirme que o job existe e está ativo:

   ```sql
   select jobname, schedule, active from cron.job where jobname = 'expurgar-localizacoes';
   ```

   Deve retornar uma linha com `active = true`. Depois que o recurso de localização estiver
   ligado, alerte (rotina mensal ou monitor) se esta consulta devolver `true`, isto é, nenhuma
   execução registrada nos últimos 35 dias:

   ```sql
   select not exists (
     select 1 from auditoria
     where acao = 'localizacao_expurgada'
       and ocorrido_em > now() - interval '35 days'
   ) as sem_expurgo_recente;
   ```

   Se os nomes das colunas de `auditoria` (`acao`, `ocorrido_em`) diferirem, ajuste conforme a
   migration `20261116120100_agenda_expurgo_localizacoes.sql`.

## 6. Pontos de operação

- Rate limit: em memória, por processo. O gunicorn roda com 1 worker + 8 threads de propósito;
  não aumente workers sem mover o contador para armazenamento compartilhado. Login passa pelo
  Supabase Auth: configure os Rate Limits lá e, se quiser, regras no Cloudflare.
- Firewall: restrinja 80/443 da VPS somente aos IPs do Cloudflare (ou use Authenticated Origin
  Pulls). A porta do frontend já escuta só em loopback. Detalhes: [deploy-ip-real.md](deploy-ip-real.md).
  Sub-rede do compose (`10.213.88.0/24`) deve ser igual no compose e no `nginx.conf`.
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
  `https://...` ("NEXT_PUBLIC_SUPABASE_URL invalida") ou se `NEXT_PUBLIC_SITE_URL` estiver ausente
  no `.env`, for `http://`, tiver caminho ou for localhost ("NEXT_PUBLIC_SITE_URL invalida").
- Testes do frontend (Playwright) falham com porta 3100 ocupada: um `serve` anterior ficou
  vivo. Encerre o processo que usa a 3100 e rode de novo.
- Convidado cai no login/home em vez de `/convite`: Redirect URLs do Supabase não casaram
  (ver convites-supabase-auth.md, seção 2). Convite expirado: usar "Reenviar".
- 502/504 no site: `docker compose ps`; backend caído ou reiniciando. Veja os logs.
- `Pool overlaps with other one on this address space` (sub-rede `10.213.88.0/24` já usada na
  VPS): trocar a faixa nos DOIS lugares (`docker-compose.yml` em `networks.default.ipam` e
  `set_real_ip_from` em `frontend/nginx.conf`, que precisam ser idênticos), via commit no
  repositório e novo `git pull` + `docker compose up -d --build` (o nginx.conf vai na imagem).
  Nunca edite na VPS.
- `port is already allocated` / `address already in use` na porta do frontend: outra aplicação usa
  essa porta; ponha uma livre em `FRONTEND_PORT` no `.env` e atualize o proxy do CloudPanel.

## 8. Cadeia de suprimentos (backend)

- **Imagem base** fixada por digest em `backend/Dockerfile` (`python:3.12.12-alpine3.22@sha256:...`).
  Atualizar: `docker buildx imagetools inspect python:<nova-tag>` e trocar tag e digest juntos.
- **Dependências com hash**: `backend/requirements.txt` guarda os pins diretos; `backend/requirements.lock.txt`
  é o lock com hashes (inclui transitivas) usado pela imagem (`pip install --require-hashes`).
  O CI instala o lock em venv limpo e roda `pip-audit --require-hashes --disable-pip` sobre ele.
- **Regerar o lock** (após mudar `requirements.txt`), na mesma base da imagem, a partir da raiz do repo:

  ```sh
  docker run --rm -v "$PWD/backend/requirements.txt:/in/requirements.txt:ro" \
    python:3.12.12-alpine3.22@sha256:848ba4413eb897e225159b8fc1b02094576cbae4aa73fc13142608ae2c8c0e32 \
    sh -c 'pip install -q pip-tools && pip-compile -q --generate-hashes --allow-unsafe --strip-extras \
      --no-header -o /dev/stdout /in/requirements.txt 2>/dev/null' > /tmp/lock.txt
  ```

  Reaplicar o cabeçalho de comentário do arquivo atual e revisar o diff.
- **Access log do gunicorn** não registra IP do cliente nem query string (LGPD); o IP mascarado fica na
  auditoria de acessos da aplicação.
- **gitleaks**: repositório em conta pessoal, sem licença. Se migrar para organização, criar o secret
  `GITLEAKS_LICENSE`.
- **Playwright**: versão e Chromium seguem o `frontend/package-lock.json` (`npm ci`).
