#!/usr/bin/env bash
# Verificacoes de seguranca ANTES do primeiro commit/push.
# Uso: bash scripts/verificar-antes-do-commit.sh   (a partir de qualquer pasta)
#
# Somente leitura: NAO executa git add/commit/push (so `git add -n`, simulacao).
# Nunca imprime valores de segredos: so arquivo:linha e o tipo do padrao.
# Dependencias: bash, git, grep.
set -u

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ" || exit 2

ERROS=0
AVISOS=0
falha() { echo "  [FALHA] $*"; ERROS=$((ERROS + 1)); }
aviso() { echo "  [AVISO] $*"; AVISOS=$((AVISOS + 1)); }
ok() { echo "  [OK] $*"; }

command -v git >/dev/null 2>&1 || { echo "git nao encontrado."; exit 2; }
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || [ "$(cd "$(git rev-parse --show-toplevel)" && pwd)" != "$RAIZ" ]; then
  echo "Esta pasta ainda nao e um repositorio git proprio."
  echo "Rode 'git init' na raiz do projeto (este script nao faz isso) e execute de novo."
  exit 2
fi

echo "== 1. .env protegido =="
if grep -qxE '\.env' .gitignore 2>/dev/null; then
  ok ".env listado no .gitignore"
else
  falha ".env nao esta listado no .gitignore"
fi
if [ -f .env ]; then
  if git check-ignore -q .env; then
    ok ".env existe e e ignorado pelo git (check-ignore)"
  else
    falha ".env existe mas NAO e ignorado pelo git"
  fi
else
  aviso ".env nao existe aqui (normal fora da maquina de execucao)"
fi
if git ls-files -co --exclude-standard | grep -qE '(^|/)\.env($|\.)' ; then
  git ls-files -co --exclude-standard | grep -E '(^|/)\.env($|\.)' | grep -vE '(^|/)\.env\.example$' \
    | while IFS= read -r f; do echo "  [FALHA] arquivo de ambiente seria versionado: $f"; done
  if git ls-files -co --exclude-standard | grep -E '(^|/)\.env($|\.)' | grep -qvE '(^|/)\.env\.example$'; then
    ERROS=$((ERROS + 1))
  fi
fi

echo "== 2. Padroes de segredo nos arquivos versionaveis =="
# tipo|regex (ERE). Os valores casados nunca sao impressos.
PADROES=(
  'JWT (eyJ longo)|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}'
  'chave sb_secret_|sb_secret_[A-Za-z0-9_-]{10,}'
  'chave sk_live_|sk_live_[A-Za-z0-9]{8,}'
  'chave privada|-----BEGIN [A-Z ]*PRIVATE KEY-----'
  'token GitHub|gh[pousr]_[A-Za-z0-9]{30,}'
  'chave AWS|AKIA[0-9A-Z]{16}'
)
# Senha/segredo literal em arquivos compose (valor que nao comeca com ${ ).
REGEX_COMPOSE='^[[:space:]]*-?[[:space:]]*[A-Za-z_]*(PASSWORD|SECRET|TOKEN|_KEY)[A-Za-z_]*[[:space:]]*[:=][[:space:]]*["'"'"']?[^$"'"'"'[:space:]#]'

ACHOU=0
while IFS= read -r -d '' arq; do
  [ -f "$arq" ] || continue
  [ "$arq" = "scripts/verificar-antes-do-commit.sh" ] && continue
  for item in "${PADROES[@]}"; do
    tipo="${item%%|*}"
    regex="${item#*|}"
    while IFS=: read -r linha _; do
      [ -n "$linha" ] || continue
      echo "  [FALHA] $arq:$linha  ($tipo)"
      ACHOU=1
    done < <(grep -nIE -e "$regex" -- "$arq" 2>/dev/null | cut -d: -f1 | sed 's/$/:/')
  done
  case "$(basename "$arq")" in
    docker-compose*.yml | docker-compose*.yaml | compose*.yml | compose*.yaml)
      while IFS=: read -r linha _; do
        [ -n "$linha" ] || continue
        echo "  [FALHA] $arq:$linha  (senha/segredo literal no compose)"
        ACHOU=1
      done < <(grep -nIE -e "$REGEX_COMPOSE" -- "$arq" 2>/dev/null | cut -d: -f1 | sed 's/$/:/')
      ;;
  esac
done < <(git ls-files -co --exclude-standard -z)
if [ "$ACHOU" -eq 0 ]; then
  ok "nenhum padrao de segredo encontrado"
else
  ERROS=$((ERROS + 1))
  echo "  Revise os pontos acima (nao imprimimos os valores). Se for exemplo/fixture, remova ou ajuste."
fi

echo "== 3. docs/security-audit/ nao versionado =="
if [ -d docs/security-audit ]; then
  if git check-ignore -q docs/security-audit/ 2>/dev/null; then
    ok "docs/security-audit/ esta ignorado"
  else
    falha "docs/security-audit/ existe e NAO esta ignorado"
  fi
else
  grep -qE '^docs/security-audit/?$' .gitignore 2>/dev/null \
    && ok "docs/security-audit/ listado no .gitignore" \
    || aviso "docs/security-audit/ nao esta no .gitignore"
fi
if git ls-files -co --exclude-standard | grep -q '^docs/security-audit/'; then
  falha "ha arquivos de docs/security-audit/ entre os versionaveis"
fi

echo "== 4. Arquivos vazios ou soltos na raiz =="
PERMITIDOS_RAIZ=" README.md CLAUDE.md .gitignore .dockerignore .env.example docker-compose.yml "
PROBLEMA=0
while IFS= read -r -d '' arq; do
  [ -f "$arq" ] || continue
  base="$(basename "$arq")"
  if [ ! -s "$arq" ]; then
    case "$base" in
      __init__.py | .gitkeep) ;;
      *) aviso "arquivo vazio: $arq"; PROBLEMA=1 ;;
    esac
  fi
  case "$arq" in
    */*) ;;
    *)
      case "$PERMITIDOS_RAIZ" in
        *" $arq "*) ;;
        *) aviso "arquivo na raiz fora da lista esperada: $arq"; PROBLEMA=1 ;;
      esac
      ;;
  esac
done < <(git ls-files -co --exclude-standard -z)
[ "$PROBLEMA" -eq 0 ] && ok "sem arquivos vazios ou soltos na raiz"

echo "== 5. O que seria adicionado (simulacao: git add -n .) =="
SIM="$(git add -n . 2>&1)"
TOTAL="$(printf '%s\n' "$SIM" | grep -c .)"
echo "  Total de arquivos: $TOTAL"
echo "  Por pasta de primeiro nivel:"
printf '%s\n' "$SIM" | sed -E "s/^add '([^'\/]*)(\/.*)?'$/\1/" | sort | uniq -c | sort -rn | head -20 | sed 's/^/    /'
echo "  Primeiros 40 itens:"
printf '%s\n' "$SIM" | head -40 | sed 's/^/    /'

echo
echo "Resumo: $ERROS falha(s), $AVISOS aviso(s)."
if [ "$ERROS" -gt 0 ]; then
  echo "NAO faca commit/push antes de corrigir as falhas."
  exit 1
fi
echo "Sem falhas. Revise os avisos e a lista acima antes do commit."
exit 0
