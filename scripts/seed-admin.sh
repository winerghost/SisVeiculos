#!/bin/sh
# Semeia o primeiro admin chamando privado.semear_primeiro_admin (idempotente).
# Rodar DEPOIS de todas as migrations, pelo pipeline/maquina do operador (nunca pela VPS).
#
# Entrada (somente variaveis de ambiente, nunca argumentos):
#   SEED_ADMIN_EMAIL  e-mail da conta ja criada/confirmada no Supabase Auth
#   DATABASE_URL      conexao DIRETA ao Postgres (nao o pooler transacional)
#
# Nada de e-mail ou URL e impresso ou logado. O e-mail entra no psql via \getenv
# (nao aparece na linha de comando/ps) e a URL via PGDATABASE.
set -eu

: "${SEED_ADMIN_EMAIL:?Defina SEED_ADMIN_EMAIL no ambiente.}"
: "${DATABASE_URL:?Defina DATABASE_URL no ambiente (conexao direta).}"

command -v psql >/dev/null 2>&1 || {
  echo "Erro: psql nao encontrado no PATH." >&2
  exit 1
}

# psql aceita URI completa em PGDATABASE; evita expor a URL em argv.
PGDATABASE="$DATABASE_URL"
export PGDATABASE
export SEED_ADMIN_EMAIL

# stderr do psql descartado: mensagens de conexao podem conter host/usuario.
if ! resultado=$(psql -X -q -t -A -v ON_ERROR_STOP=1 2>/dev/null <<'SQL'
\getenv e SEED_ADMIN_EMAIL
select privado.semear_primeiro_admin(:'e');
SQL
); then
  echo "Erro: falha ao executar o seed (conexao, permissao ou funcao ausente). Confira as migrations e a DATABASE_URL." >&2
  exit 1
fi

resultado=$(printf '%s' "$resultado" | tr -d '[:space:]')

case "$resultado" in
  criado)
    echo "Seed: primeiro admin criado."
    ;;
  ja_existe_admin)
    echo "Seed: ja existe admin ativo; nada a fazer."
    ;;
  sem_email)
    echo "Erro: e-mail do seed ausente ou vazio." >&2
    exit 1
    ;;
  conta_auth_inexistente)
    echo "Erro: nao ha conta no Supabase Auth para o e-mail informado. Crie/convide a conta antes." >&2
    exit 1
    ;;
  conta_auth_invalida)
    echo "Erro: a conta no Auth nao esta apta (nao confirmada, banida ou apagada). Confirme o e-mail da conta." >&2
    exit 1
    ;;
  perfil_ja_existe)
    echo "Erro: a conta ja possui perfil sem ser admin ativo. Resolva manualmente (nao ha promocao automatica)." >&2
    exit 1
    ;;
  *)
    echo "Erro: retorno inesperado do seed." >&2
    exit 1
    ;;
esac

# Verificacao final: precisa existir ao menos um admin ativo.
if ! total=$(psql -X -q -t -A -v ON_ERROR_STOP=1 2>/dev/null \
  -c "select count(*) from public.perfis where role = 'admin' and ativo"); then
  echo "Erro: nao foi possivel verificar a existencia de admin ativo." >&2
  exit 1
fi
total=$(printf '%s' "$total" | tr -d '[:space:]')

case "$total" in
  '' | *[!0-9]*)
    echo "Erro: resposta invalida na verificacao de admin ativo." >&2
    exit 1
    ;;
esac

if [ "$total" -lt 1 ]; then
  echo "Erro: nenhum admin ativo apos o seed." >&2
  exit 1
fi

echo "OK: ha $total admin(s) ativo(s)."
