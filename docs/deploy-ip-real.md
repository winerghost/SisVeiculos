# IP real do cliente, firewall e rate limit

## Cadeia de confiança

Cloudflare -> proxy do CloudPanel (host) -> `127.0.0.1:8080` (nginx do frontend) -> gunicorn.

- O nginx do frontend só aceita `CF-Connecting-IP` quando o par imediato está nas faixas
  oficiais do Cloudflare ou na sub-rede fixa do compose (`172.29.88.0/24`, onde chega o proxy
  local pelo gateway `172.29.88.1`). Depois disso ele **sobrescreve** `CF-Connecting-IP`, `X-Real-IP` e
  `X-Forwarded-For` com o IP validado, então o backend nunca recebe valor forjado pelo cliente.
- As faixas ficam em `frontend/nginx.conf` e vêm de https://www.cloudflare.com/ips-v4 e
  https://www.cloudflare.com/ips-v6. Revise de tempos em tempos; alterar exige `--build`.

## Sub-rede fixa do compose

`docker-compose.yml` fixa `networks.default.ipam` em `172.29.88.0/24` e o `nginx.conf`
confia apenas nessa faixa (`set_real_ip_from 172.29.88.0/24`), em vez de todo o
`172.16.0.0/12`. Os dois valores devem ser sempre iguais. Se a faixa colidir com outra rede
da VPS (`docker network ls` / `ip route`), troque nos dois arquivos e rode
`docker compose up -d --build` (recria a rede). Mudança só no compose exige `up -d`; no
`nginx.conf`, `--build`.

## Requisito na VPS (obrigatório)

Se alguém alcançar a origem sem passar pelo Cloudflare, pode enviar um `CF-Connecting-IP`
falso que o proxy local repassaria. Portanto:

1. Restrinja no firewall da VPS (Hostinger/ufw) as portas 80/443 **somente** aos IPs do
   Cloudflare; ou
2. Ative Authenticated Origin Pulls (mTLS) no Cloudflare e exija o certificado no CloudPanel.

Alternativa mais forte: ambos. A porta 8080 já escuta só em `127.0.0.1`.
No proxy do CloudPanel, não repasse cabeçalhos `CF-Connecting-IP` de origens fora do Cloudflare.

## Rate limit e gunicorn

O gunicorn roda com `--workers 1 --threads 8`. O rate limit do Flask é em memória e por
processo; com vários workers o limite efetivo seria multiplicado por N e inexato.

Trade-off: um único processo não usa mais de um núcleo para código Python (GIL). A carga é
quase toda I/O (chamadas ao Supabase), então 8 threads atendem bem a v1. Se for preciso
escalar, mover o contador para um armazenamento compartilhado (ex.: Redis) antes de aumentar
workers.

O limite de tentativas de **login** não é do Flask: o login passa pelo Supabase Auth
(front -> SDK), então a proteção contra força bruta depende dos limites configurados no
Supabase Auth (Authentication > Rate Limits) e, opcionalmente, de regras de rate limiting no
Cloudflare para o endpoint de autenticação.

## Log de acesso do gunicorn (LGPD)

O `--access-logfile -` padrão grava a linha de requisição completa (`%(r)s`), incluindo a
query string. Buscas como `?q=` em `/api/admin/usuarios` (nome ou e-mail) iriam para o log do
container. O `backend/Dockerfile` define então `--access-logformat`:

`%(h)s %(m)s %(U)s %(s)s %(b)s %(L)ss`  (IP, método, caminho sem query, status, bytes, tempo)

Sem `%(r)s`, `%(q)s`, user agent nem referer. Validado no gunicorn 26.2.0 (versão fixada):
`GET /api/admin/usuarios?q=maria@agu.gov.br` sai como `127.0.0.1 GET /api/admin/usuarios 200 2 0.000438s`.
Atenção: o `%(h)s` atrás do nginx é o IP do proxy; o IP real do cliente fica no nginx/Cloudflare.
Exige `docker compose up -d --build` (mudou `backend/`).

## CI: segredo do gitleaks

`GITLEAKS_LICENSE` (secret do GitHub) só é exigido quando o repositório pertence a uma
organização; em conta pessoal pode ficar sem valor.
