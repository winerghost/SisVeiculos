#!/bin/sh
# Guarda da invariante "gunicorn com 1 worker" enquanto o rate limit usar memory://.
# Uso: scripts/verificar-workers.sh [raiz-do-repositorio]
# Com storage em memória, cada worker tem seu próprio contador: N workers multiplicam o
# limite efetivo (login, criação). Só é seguro subir workers após migrar o limiter para
# storage compartilhado (ex.: Redis) e atualizar este script.
set -eu

RAIZ="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
EXT="$RAIZ/backend/app/extensions.py"
ALVOS="$RAIZ/backend/Dockerfile $RAIZ/docker-compose.yml $RAIZ/backend/gunicorn.conf.py"
for f in "$RAIZ"/backend/*.sh "$RAIZ"/backend/entrypoint*; do
  [ -f "$f" ] && ALVOS="$ALVOS $f"
done

[ -f "$EXT" ] || { echo "ERRO: $EXT não encontrado." >&2; exit 2; }

if ! grep -Eq 'storage_uri[[:space:]]*=[[:space:]]*["'\'']memory://' "$EXT"; then
  echo "OK: o limiter não usa memory://; guarda de workers não se aplica."
  exit 0
fi

falhas=0
erro() {
  echo "ERRO: $1" >&2
  falhas=1
}

for f in $ALVOS; do
  [ -f "$f" ] || continue
  nome="${f#"$RAIZ"/}"
  # Remove comentários de linha inteira; normaliza aspas, vírgulas, colchetes, '=' e '\'.
  limpo=$(grep -Ev '^[[:space:]]*#' "$f" | tr '\n' ' ' | sed -E 's/[]["'"'"',=\\]/ /g')

  if printf '%s' "$limpo" | grep -Eq 'WEB_CONCURRENCY|GUNICORN_CMD_ARGS'; then
    erro "$nome define WEB_CONCURRENCY/GUNICORN_CMD_ARGS, que pode alterar o número de workers."
  fi

  # Valor logo após -w / --workers (ou -wN colado).
  valores=$(printf '%s' "$limpo" | grep -Eo '(^|[[:space:]])(-w[[:space:]]*|--workers[[:space:]]+)[^[:space:]]+' \
    | sed -E 's/^[[:space:]]*(-w|--workers)[[:space:]]*//' || true)
  for v in $valores; do
    if [ "$v" != "1" ]; then
      erro "$nome configura gunicorn com workers='$v' (esperado 1)."
    fi
  done

  # Formato de arquivo de configuração (gunicorn.conf.py): workers = N.
  conf=$(printf '%s' "$limpo" | grep -Eo '(^|[[:space:]])workers[[:space:]]+[0-9]+'     | sed -E 's/^[[:space:]]*workers[[:space:]]+//' || true)
  for v in $conf; do
    if [ "$v" != "1" ]; then
      erro "$nome configura workers = '$v' (esperado 1)."
    fi
  done
done

if [ "$falhas" -ne 0 ]; then
  cat >&2 <<MSG

Falha: o rate limit (backend/app/extensions.py) usa storage_uri="memory://", por processo.
Com mais de 1 worker o limite efetivo é multiplicado e o estado em memória diverge.
Mantenha 1 worker (use --threads para concorrência) ou migre o limiter para storage
compartilhado (Redis) e ajuste esta guarda no mesmo PR.
MSG
  exit 1
fi

echo "OK: gunicorn com 1 worker, compatível com o rate limit em memória."
