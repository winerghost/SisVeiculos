# Convites: configuração no Supabase Auth

Não há autocadastro. O admin convida a conta por e-mail (`POST /api/admin/usuarios`) e o
backend usa o convite do Supabase Auth. Esta configuração é feita uma vez pelo operador no
painel do Supabase (não é migration nem passa pelo servidor).

## 1. Variável do backend

No `.env` da VPS defina `URL_ACEITE_CONVITE` com a URL https da página de aceite do frontend,
por exemplo `https://<dominio>/convite`. Sem ela o endpoint responde
`503 convites_nao_configurados`. O backend recusa a subida se a URL não for https.

## 2. Authentication > URL Configuration

- O redirect real do convite não é `URL_ACEITE_CONVITE` pura, e sim
  `<URL_ACEITE_CONVITE>?convite=<token>`. O Supabase compara o `redirectTo` inteiro, query
  incluída, com a lista de Redirect URLs. Uma entrada exata `https://<dominio>/convite` **não**
  casa com `https://<dominio>/convite?convite=<token>`.
- Entrada recomendada em **Redirect URLs** (restrita ao path e ao parâmetro):
  `https://<dominio>/convite\?convite=**`
  - Na sintaxe de curingas do Supabase, `?` é curinga de um caractere; por isso o `?` literal
    leva barra invertida (`\?`). `**` cobre o token (que pode ter `.`, `-` e `/`, separadores
    que o `*` simples não casa).
  - Se o painel rejeitar o escape ou o link ainda cair no Site URL (ver Verificação), use a
    alternativa `https://<dominio>/convite**`, ainda restrita ao host e ao prefixo `/convite`.
  - Motivo da restrição: a doc oficial do Supabase (Auth > Redirect URLs) recomenda o caminho
    exato em produção e desaconselha `**` amplo; a allow list impede redirect aberto para
    destinos de terceiros. Não use `https://<dominio>/**`, `https://**` nem curinga no host.
- **Sem casamento, o Supabase ignora o `redirectTo` e redireciona ao Site URL**; o token do
  convite (e o fragmento `#access_token`) se perde na prática, pois a página `/convite` não é
  aberta com `?convite=`. Sintoma: o convidado cai na home/login.
- Confira o **Site URL** (domínio de produção, https).
- Não use `http://` em produção.
- Fluxo de autenticação implícito, não PKCE: a página `/convite` lê a sessão do fragmento
  (`#access_token=...`). Com PKCE o retorno seria `?code=...` e a página não o trataria. Não
  mude o `flowType` do cliente Supabase da página de aceite para `pkce` sem alterar a página.

## 3. Authentication > Sign In / Providers

- **Allow new users to sign up**: DESLIGADO (signup público desativado; só o admin cria contas).
- **Allow anonymous sign-ins**: DESLIGADO.
- Provedor E-mail ligado (convite e senha). Outros provedores só com aprovação.

## 4. Authentication > Email Templates > Invite user

Use o template de convite e mantenha `{{ .ConfirmationURL }}` no link, que leva ao redirect
configurado. Texto em pt-BR, curto, sem dados pessoais além do necessário. O convite é de uso
único e tem prazo de validade (ajuste a expiração em Authentication > Sign In / Providers >
e-mail, conforme a política da AGU).

## 4.1 Template "Reset password" (caminho de conta existente)

Quando a conta já existe no Supabase Auth (ex.: confirmada, sem perfil), o backend reenvia o
acesso pelo fluxo de redefinição de senha. Em Authentication > Email Templates > **Reset
password**:

- Mantenha `{{ .ConfirmationURL }}` no link (leva ao mesmo redirect do convite).
- Texto em pt-BR, curto, sem dados pessoais além do necessário.
- Informe o prazo real do link: é o **OTP expiry** (seção 6), máximo 24 h.
- O redirect segue a regra da seção 2 (entrada de Redirect URLs com `?convite=`).

## 5. Verificação

1. Como admin, convide um e-mail de teste.
2. O link do e-mail deve abrir `URL_ACEITE_CONVITE` (com o token na query `convite`).
3. Tentar criar conta por signup direto na API do Supabase deve falhar.
4. Se a URL aberta não for `/convite?convite=<token>` com `#access_token=` no fragmento (caiu
   no Site URL), a entrada de Redirect URLs não casou: revise a seção 2.
5. Fluxo de recuperação: com uma conta já confirmada no Supabase Auth e sem perfil, o admin
   a convida; abra o e-mail (template "Reset password"), clique no link e conclua o aceite em
   `/convite` (definir senha e criar o perfil). Confirme que o link traz `#access_token`
   (fluxo implícito) e não `?code=` (PKCE).

## Deploy

Mudança só de `.env` e `docker-compose.yml`: `docker compose up -d` (sem `--build`).

## 6. Prazo do link do e-mail x validade do convite

O link do e-mail é gerado pelo Supabase e expira no **OTP expiry** (Authentication > Sign In /
Providers > e-mail), no máximo 24 h, conforme a configuração do projeto. Esse prazo é
independente da validade do convite no app e pode vencer antes dela.

- Alinhe os dois prazos (OTP expiry igual ou maior que a validade do convite, respeitado o
  teto de 24 h do Supabase) e informe o prazo real no texto do template.
- Link expirado: o admin usa **Reenviar** na tela de usuários. O backend reenvia o acesso
  mesmo se a conta já existir no Supabase Auth.

## 7. Logs: o token do convite não pode ir para logs

O token trafega em `/convite?convite=<token>` (query string).

- **nginx do frontend**: o `access_log` usa o formato `sem_query` (`$uri`, sem query string),
  definido em `frontend/nginx.conf`. IP real, status e tempo continuam registrados.
  Validado com curl em `/convite?convite=SEGREDO`: o valor não aparece nos logs do container.
- **CloudPanel (proxy reverso)**: o log de acesso do site pode registrar a query string.
  O operador deve, no vhost do site, desabilitar `access_log` para `location /convite` ou
  trocar o `log_format` para `$uri` em vez de `$request`.
- **Cloudflare**: logs (Logpush, Security Events, Analytics) podem incluir `ClientRequestURI`/
  `ClientRequestQuery`. Exclua ou mascare esse campo (ou a rota `/convite`) no Logpush e
  não crie regras/dashboards que exibam a query dessa rota.
- Revise periodicamente: qualquer novo log de borda ou proxy deve omitir a query de `/convite`.

### Busca de usuários (`/api/admin/usuarios?q=`)

A busca por nome ou e-mail também trafega em query string (dado pessoal, LGPD). A orientação
acima vale igualmente para `/api/admin/usuarios`:

- **CloudPanel**: sem `access_log` ou com `log_format` em `$uri` para `location /api/admin/usuarios`
  (e `/convite`); lembre que o `error_log` do proxy também pode registrar `request:` com query.
- **Cloudflare**: exclua/mascare `ClientRequestURI`/`ClientRequestQuery` no Logpush para
  `/api/admin/usuarios` e `/convite`, e não exiba a query dessas rotas em Security Events ou
  dashboards.
- **nginx do frontend**: o `access_log` já usa `$uri`. O `error_log` é `/dev/stderr crit`: em
  502/504 o nginx, nos níveis `error` e abaixo, escreve `request: "GET /api/admin/usuarios?q=..."`
  com a query. No nível `crit` essa linha não é gravada. Validado subindo a imagem com o `out/`
  existente e o backend inexistente: `GET /api/admin/usuarios?q=SEGREDO` devolveu 502 e
  `SEGREDO` não apareceu nos logs do container.
- **Decisão futura**: enviar a busca por `POST` (corpo) ou cabeçalho, evitando a query em
  qualquer log intermediário. Exige mudança de API e frontend; fica registrada como opção.

## 8. MFA: risco do cadastro de TOTP em sessão aal1

No Supabase Auth, quem autentica só com senha (aal1) consegue cadastrar um fator TOTP próprio.
Se admin ou autorizador ainda não tem fator, quem obtiver a senha dele pode cadastrar o TOTP
do atacante e passar a satisfazer o segundo fator.

Recomendação operacional:

1. Todo `admin` e `autorizador` cadastra e confirma o seu fator TOTP **antes** de ligar o MFA
   em Configurações.
2. Só depois o admin liga o recurso MFA.
3. Contas novas de admin/autorizador devem cadastrar o fator logo no primeiro acesso, e
   suspeita de senha vazada exige redefinição de senha e revisão dos fatores cadastrados.
