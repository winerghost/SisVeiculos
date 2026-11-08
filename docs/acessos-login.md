# Histórico de acessos (logins bem-sucedidos)

Migrations: `supabase/migrations/20261107122430_acessos_login.sql` (base, já aplicada) e `20261107122440_acessos_login_endurecimento.sql` (endurecimento pós-revisão de segurança). Testes: `supabase/tests/test_acessos_login.py`.

## Finalidade e base legal
- Finalidade: o administrador consulta quem entrou no sistema e quando (segurança e transparência do uso de veículos oficiais).
- Base legal: **a confirmar com o encarregado (LGPD)**. Hipóteses a avaliar: cumprimento de obrigação legal/regulatória ou execução de política pública, e legítimo interesse de segurança. Confirmar também a política de segurança da AGU antes de produção.

## Minimização (feita no próprio banco)
| Dado | O que é guardado | O que o banco recusa |
|---|---|---|
| IP | `ip_mascarado` (cidr): IPv4 entre `/8` e `/24`, IPv6 entre `/16` e `/48` (CHECK da tabela e RPC usam a mesma função) | IP completo, prefixo mais fino, prefixo vazio (`0.0.0.0/0`, `::/0`, `/1`...), texto inválido |
| Navegador | `ua_familia` exatamente `Família/Sistema` de **lista fechada** (CHECK e RPC usam a mesma função `acessos_login_ua_valida`). Famílias: Chrome, Firefox, Safari, Edge, Opera, Samsung Internet, Mobile Safari, Outro. Sistemas: Windows, macOS, Linux, Android, iOS, ChromeOS, Desconhecido | user-agent bruto, versões, qualquer texto livre (ex.: nome de pessoa) |
| Sessão | `session_id` (claim do JWT) só para idempotência (UNIQUE) | nunca é devolvido por completo; a consulta devolve `sessao_ref` (8 primeiros hex do sha256), **só rótulo visual**: colide a partir de ~65 mil linhas; não usar como chave nem para correlação |
| Outros | `metodo` (`password`/`totp`/`outro`), `aal` (`aal1`/`aal2`), `ocorrido_em`, `registrado_em` | — |

O dado mascarado **continua sendo dado pessoal** (IP /24 + horário + conta identificam o titular indiretamente): trate-o com a mesma cautela de LGPD.

Mensagens de erro do banco são fixas e não repetem IP, user-agent ou valores recebidos. Não há dados pessoais em logs; o backend também não deve logar o conteúdo, só o código do erro.

## O que NÃO é registrado
- Falhas de login (senha errada, MFA errado) e renovação de token (refresh). Falhas ficam nos logs do Auth do Supabase.
- Consulta de falhas: painel do Supabase > Logs > Auth (filtrar por `status` 400/422 e `path` `/token`). Esses logs têm retenção própria do plano e contêm IP completo, fora do controle desta tabela.

## Retenção e expurgo
- Retenção: 12 meses. `purgar_acessos_login(p_meses default 12)` exige `p_meses` entre **12** e 1200 (senão `AG020`) e devolve a contagem. O **piso de 6 meses** continua no gatilho como defesa em profundidade (nunca apaga linha com menos de 6 meses, mesmo com a variável forjada).
- A tabela é imutável (UPDATE/TRUNCATE sempre bloqueados, `AG003`). DELETE só dentro do expurgo: variável de sessão local `sisveiculos.expurgo_acessos`, ligada via `set_config(..., true)` apenas dentro da função e desligada ao fim.
- Papéis da API não forjam: não têm DELETE na tabela (nem SELECT: `service_role` só acessa via RPCs `SECURITY DEFINER`). Limite herdado: SQL livre como `postgres` pode forjar a variável, mas o piso de 6 meses vale.
- O expurgo não grava em `auditoria` (que promete "sem dados pessoais"). O backend deve logar apenas a contagem.
- **Não há caminho técnico por titular para eliminação antes de 12 meses.** É decisão de desenho do encarregado; não implementar sem nova decisão dele. Pedidos de titulares devem ser tratados pelo encarregado.

### Agendamento do expurgo
- Opção A (preferida se disponível): `pg_cron`. A migration agenda `purgar-acessos-login` (diário, 03:17 UTC) **somente se** a extensão já existir; senão apenas emite um aviso e não falha. Não cria a extensão. Habilitar: painel Supabase > Database > Extensions > `pg_cron`; depois, como `postgres` no SQL Editor: `select cron.schedule('purgar-acessos-login', '17 3 * * *', 'select public.purgar_acessos_login(12)');`. Verificar: `select * from cron.job;` e `cron.job_run_details`.
- Opção B: job interno agendado do próprio backend (que já é o único detentor legítimo da `service_role`), chamando a RPC uma vez ao dia e logando só a contagem.
- **Não** rodar a RPC pelo pipeline de CI/CD com a `service_role`: isso levaria a chave a um segundo lugar e viola "`service_role` só no backend".
- **Pendência datada:** o primeiro registro só passa de 12 meses em nov/2027. Definir e ativar A ou B **antes de nov/2027**; sem agendamento a retenção promete 12 meses mas não é cumprida.

## Uso permitido e transparência
- **Vedado** usar este log para avaliação de desempenho, controle de jornada ou produtividade de servidores. A finalidade é segurança e transparência do acesso.
- **Aviso aos titulares:** informar aos usuários (aviso de privacidade/tela de login) que o sistema registra data/hora, IP mascarado e navegador/sistema de cada acesso, a finalidade, os 12 meses de retenção e quem consulta (admin). Texto a validar com o encarregado antes de produção.

## Contrato das RPCs (EXECUTE só `service_role`; `purgar` também o dono `postgres`)
1. `registrar_acesso_login(p_usuario uuid, p_session uuid, p_ocorrido_em timestamptz, p_metodo text, p_aal text, p_ip text, p_ua text) -> boolean`
   - `true` inseriu; `false` a sessão já estava registrada (idempotente, `ON CONFLICT (session_id) DO NOTHING`).
   - `p_ip` é texto CIDR já mascarado (`203.0.113.0/24`, `2001:db8:1::/48`; v4 /8../24, v6 /16../48); o backend mascara. `p_ip`, `p_ua`, `p_metodo`, `p_aal` podem ser nulos; `p_ua` nulo ou da lista fechada.
   - **Sessão do Auth obrigatória:** deve existir `auth.sessions (id = p_session, user_id = p_usuario)`; senão `AG020` (mesma mensagem para inexistente e de outro usuário). O `ocorrido_em` gravado é **sempre** o `created_at` dessa sessão (o backend não escolhe a data); `p_ocorrido_em` é só entrada validada (janela abaixo) e só é gravado se o `created_at` do Auth vier nulo. Reenvio de sessão já registrada **do mesmo usuário** retorna `false` antes dessas checagens; sessão já registrada de outro usuário cai na checagem do Auth e recebe a mesma mensagem `AG020` (não revela que o `session_id` existe no histórico). `p_ip` precisa conter `.`, `:` ou `/` (recusa atalhos como `10`).
   - **Limite aceito:** o `ocorrido_em` vem de `auth.sessions.created_at` e pode ser anterior a 7 dias (sessões longas): o registro ocorre na primeira requisição vista pelo backend, e uma sessão com mais de 12 meses seria expurgada logo em seguida.
   - A migration falha se o dono não tiver SELECT e BYPASSRLS em `auth.sessions` ou se faltarem as colunas `id`, `user_id`, `created_at`.
   - Erros: `AG019` usuário inexistente ou inativo; `AG020` dado inválido (obrigatório ausente, método/aal, IP não mascarado, `ua_familia` fora da lista, sessão do Auth inválida, `p_ocorrido_em` além de +5 min no futuro ou mais de 7 dias no passado para sessão nova).
   - **Acoplamento ao schema interno do Auth:** a função lê `auth.sessions (id, user_id, created_at)`, que não é API pública do GoTrue. Uma atualização do Auth que mude essas colunas quebra o registro (erro de coluna); como o backend é best-effort o login não cai, mas o histórico para de gravar. Os testes criam a linha em `auth.sessions` e acusam a regressão ao subir a nova versão do Auth local; reexecutá-los a cada upgrade do Supabase.
   - Best-effort: o backend captura o erro e não derruba o login. Não toma `perfis_admin` nem escreve em `perfis`.
2. `listar_acessos_login(p_usuario uuid default null, p_de timestamptz default null, p_ate timestamptz default null, p_limite int default 50, p_offset int default 0) -> jsonb`
   - `{"total", "limite", "offset", "itens": [{id, usuario_id, nome, ocorrido_em, metodo, aal, ip_mascarado, ua_familia, sessao_ref}]}`.
   - `p_de` inclusivo, `p_ate` exclusivo; ordem `ocorrido_em desc, id desc`; limite ajustado a 1..100, offset negativo ajustado a 0; **offset acima de 100000 => `AG020`**.
3. `ultimo_acesso_por_usuario(p_usuarios uuid[]) -> table(usuario_id uuid, ultimo_acesso_em timestamptz)`: até 100 ids (`AG020` acima); usuários sem acesso não aparecem.
4. `purgar_acessos_login(p_meses int default 12) -> integer`: `p_meses` 12..1200, senão `AG020`.

Sem SELECT direto: `anon`, `authenticated` e `service_role` não têm nenhum privilégio na tabela; só as RPCs acima (e as funções internas `acessos_login_ua_valida`/`acessos_login_ip_valido`, sem EXECUTE para a API).

Quem consulta: somente o admin, via backend (que valida role). A consulta deve ser auditada pelo backend com `registrar_auditoria` (ação `acao_administrativa`), sem incluir os dados exibidos. O usuário comum não consulta.

## Testes
`cd supabase/tests && TEST_DATABASE_URL=... TEST_DATABASE_CONFIRM=descartavel pytest test_acessos_login.py -m db_local` (somente banco local descartável; o expurgo apaga linhas antigas de qualquer origem).
