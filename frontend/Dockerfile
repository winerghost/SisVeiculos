# --- build do export estático (output: 'export') ---
# Digest = índice multi-arch obtido do registry em 2026-11-07; atualizar junto com a tag.
FROM node:22-alpine3.22@sha256:cd7807368cf24826297cbad5dca1a44972ccfd770647db52a8c7589eb4599ac8 AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY . .
# NEXT_PUBLIC_* ficam embutidos no build: mudar exige --build.
ARG NEXT_PUBLIC_SUPABASE_URL
ARG NEXT_PUBLIC_SUPABASE_ANON_KEY
# URL pública https do site (og:image/og:url absolutas). Obrigatória e validada abaixo.
ARG NEXT_PUBLIC_SITE_URL
ENV NEXT_PUBLIC_SUPABASE_URL=$NEXT_PUBLIC_SUPABASE_URL \
    NEXT_PUBLIC_SUPABASE_ANON_KEY=$NEXT_PUBLIC_SUPABASE_ANON_KEY \
    NEXT_PUBLIC_SITE_URL=$NEXT_PUBLIC_SITE_URL \
    NEXT_TELEMETRY_DISABLED=1
# Falha o build se NEXT_PUBLIC_SITE_URL faltar, não for https://host (sem caminho) ou apontar para
# localhost/loopback: a imagem de produção nunca sai com og:image local. Remove uma barra final.
RUN SITE="${NEXT_PUBLIC_SITE_URL%/}" \
    && case "$SITE" in https://*) ;; *) echo "NEXT_PUBLIC_SITE_URL invalida (esperado https://SEU-DOMINIO, sem barra final)" >&2; exit 1 ;; esac \
    && case "${SITE#https://}" in ""|*/*|*[[:space:]]*|localhost|localhost:*|127.*|"[::1]"*) echo "NEXT_PUBLIC_SITE_URL invalida (host publico, sem caminho e fora de localhost)" >&2; exit 1 ;; esac \
    && NEXT_PUBLIC_SITE_URL="$SITE" npm run build

# CSP com o host exato do Supabase (sem curinga): gerado no build a partir da URL pública.
RUN ORIGIN="$(printf '%s' "$NEXT_PUBLIC_SUPABASE_URL" | sed -E 's#^(https://[^/]+).*#\1#')" \
    && case "$ORIGIN" in https://*) ;; *) echo "NEXT_PUBLIC_SUPABASE_URL invalida (esperado https://...)" >&2; exit 1 ;; esac \
    && sed "s#__SUPABASE_ORIGIN__#${ORIGIN}#g" security-headers.conf > /tmp/security-headers.conf

# --- runtime: nginx não-root (uid 101), porta 8080 ---
FROM nginxinc/nginx-unprivileged:1.28.1-alpine@sha256:4655ddff4704d6b6c85f5a5862b5d0840941fcc95a4f4668f04b1d6f85858e7c
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /tmp/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=build /app/out /usr/share/nginx/html
EXPOSE 8080
