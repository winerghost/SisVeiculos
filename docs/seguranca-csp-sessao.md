# CSP, sessão em localStorage e plano de endurecimento

## Risco aceito (v1)

O SDK do Supabase guarda a sessão (JWT) em `localStorage`, legível por qualquer script que
rode na origem. A CSP em `frontend/security-headers.conf` usa `'unsafe-inline'` em
`script-src` e `style-src`, exigido pelos scripts inline de hidratação do export estático do
Next.js. Combinados, um XSS teria como ler o token. Mitigações vigentes: sem
`dangerouslySetInnerHTML`, saída sanitizada, `default-src 'self'`, `connect-src` restrito ao
host exato do Supabase, `object-src 'none'`, `frame-ancestors 'none'`, JWT de vida curta e
toda autorização revalidada no backend. O risco está aceito para a v1 e deve ser
reavaliado antes da produção, junto à política de segurança da AGU.

## Plano: CSP com hashes

1. Após `npm run build`, um passo no Dockerfile (estágio `build`) percorre `out/*.html` e
   `out/**/*.html`, calcula `sha256` (base64) de cada `<script>` inline e monta a lista.
2. A lista é injetada em `script-src` no lugar de `'unsafe-inline'` (junto ao
   `sed` que já substitui `__SUPABASE_ORIGIN__`). Como os scripts variam por página, usar
   um `add_header` por rota (ou a união dos hashes) e validar que nenhuma página quebra.
3. `style-src` exige tratar os estilos inline do Tailwind/Next separadamente; manter
   `'unsafe-inline'` em estilo até lá, pois o risco de exfiltração é bem menor que em script.
4. Validar com o Playwright (`npm run test:e2e` contra nginx) e checar o console por
   violações de CSP.

Não implementado agora: exige um script de pós-processamento e testes que só são
verificáveis com o build completo em Docker, e qualquer hash faltante derruba a hidratação.
Mudança futura em Dockerfile/security-headers.conf exige `--build`.
