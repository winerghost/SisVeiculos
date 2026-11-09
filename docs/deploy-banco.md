# Deploy do banco (Supabase)

Rodar sempre pelo pipeline ou pela maquina do operador. Nunca pela VPS.

## Checklist (nesta ordem)

1. Instalar o Supabase CLI e autenticar: `supabase login`.
2. Vincular ao projeto: `supabase link --project-ref <ref>`.
3. Simular: `supabase db push --dry-run` e revisar as migrations pendentes.
4. Aplicar: `supabase db push`.
5. No painel do Supabase Auth, desligar o signup publico e o login anonimo (sem autocadastro).
6. Criar ou convidar a conta do admin no Auth e confirmar o e-mail dela. O seed so promove conta existente e confirmada.
7. Rodar o seed DEPOIS de todas as migrations, com as variaveis no ambiente (nunca como argumento):
   `SEED_ADMIN_EMAIL=... DATABASE_URL=... sh scripts/seed-admin.sh`
   - `DATABASE_URL` deve ser a conexao direta, nao o pooler transacional.
   - O script e idempotente e falha (codigo != 0) se nao houver admin ativo ao final.
   - Nao depender do bloco `DO` da migration 9: ele roda uma vez no `db push` e fica registrado como aplicado mesmo com resultado `sem_email` ou `conta_auth_inexistente`.
8. Verificar os avisos de seguranca e performance (`get_advisors`, tipos `security` e `performance`) e corrigir via nova migration.

## Lembretes para o backend

- Tratar o erro `40P01` (deadlock) com retry limitado.
- Esperas por lock: a migration 16 define `lock_timeout = '5s'` nas roles `service_role`, `authenticated` e `anon` (o PostgREST aplica a configuracao da role a cada requisicao; o backend nao usa `SET LOCAL`). Ao expirar, o erro e `55P03`: fazer retry limitado (como em `40P01`), com backoff curto.
- `consumir_convite` so roda depois do aceite do convite pelo usuario.

## lock_timeout por role (migration 16)

- Verificar apos o `db push`: `select rolname, rolconfig from pg_roles where rolname in ('service_role','authenticated','anon','authenticator','postgres');` (as tres primeiras com `lock_timeout=5s`; `authenticator` e `postgres` sem o valor desta migration; configuracoes proprias do ambiente podem existir).
- A migration 16 envia `NOTIFY pgrst, 'reload config'` e a 17 reenvia `reload config` e `reload schema`. Se o PostgREST nao reler, repetir manualmente o NOTIFY pelo painel/operador.
- `55P03` pode ocorrer em qualquer escrita (nao so em perfis): o backend faz retry limitado e, esgotado, responde 503 com codigo `ocupado`.
- `anon`: o `statement_timeout` de 3 s da role pode vencer o `lock_timeout` de 5 s, entao para `anon` o `lock_timeout` e apenas defesa nominal (o erro visto sera `57014`, nao `55P03`).

### Smoke pos-deploy

1. No `psql` (conexao direta, como operador), abrir transacao e segurar o advisory: `begin; select pg_advisory_xact_lock(hashtextextended('sisveiculos.perfis_admin', 0));` (deixar a sessao aberta).
2. Em outro terminal, fazer uma escrita pela API (ex.: alterar o nome de um perfil como admin). Esperado: cada tentativa leva ~5 s e, esgotados os retries, a resposta e 503 com `ocupado`.
3. No `psql`, `rollback;` para soltar o lock e repetir a escrita: deve voltar a funcionar (200).
- Nao afeta o pipeline: `db push` e o seed rodam como `postgres` (sem lock_timeout). Se uma migration futura precisar de role `service_role` com DDL longo, usar `SET LOCAL lock_timeout` na propria migration.
- Alternativa se algum dia o PostgREST deixar de respeitar a config por role: funcao `db-pre-request` (`security definer`, `search_path = ''`, `revoke ... from public, anon, authenticated`) que faz `set_config('lock_timeout','5s',true)`; hoje nao e necessaria.

## Quilometragem (migration 18)

- Colunas `requisicoes.km_saida` e `km_chegada` (inteiros >= 0, nulos). O recurso continua desligado por padrao (`configuracoes.quilometragem`); a migration nao liga nada.
- Fronteira banco x backend:
  - Banco (sempre): `km_chegada >= km_saida`, nao negativo, `km_chegada` exige `km_saida`, km so existe com saida/chegada real, km imutavel apos gravado e so entra junto da transicao.
  - Banco (recurso ligado, lido na hora da escrita): `em_uso` exige `km_saida`; `concluida` exige `km_saida` e `km_chegada`. Falta => `AG012`.
  - Backend (recurso desligado): ignora os campos de km na entrada e recusa os endpoints do recurso. O banco nao descarta nem recusa km valido enviado com o recurso desligado.
- Erros para o backend traduzir: `AG012` (km obrigatorio), `AG004` (km ja gravado ou fora de transicao), `23514` com a constraint `ck_requisicoes_km_chegada_maior_igual_saida` (chegada menor que saida), `ck_requisicoes_km_*_nao_negativo`, `ck_requisicoes_km_chegada_exige_saida`, `ck_requisicoes_km_coerente_com_real`.
- Auditoria: a transicao para `em_uso`/`concluida` grava `km_saida`/`km_chegada` em `auditoria.detalhes` (sem dado pessoal; append-only intacto).
- Ligar o recurso com viagem `em_uso` sem `km_saida`: a chegada deve enviar `km_saida` e `km_chegada` juntos.
- Contrato do backend (B5): na chegada, se `km_saida` ja existe, o backend deve ler o valor gravado e nao enviar outro diferente. `AG004` na transicao para `concluida` significa que o `km_saida` enviado difere do gravado; reenviar o mesmo valor ou omitir e aceito. O backend traduz `AG004` em mensagem clara.
- Testes: `supabase/tests/test_quilometragem.py`. Rodar SO em banco local descartavel (`supabase db reset`), sem paralelismo. Uma fixture autouse le o estado de `quilometragem`/`abastecimento_manutencao`, desliga ambos (abastecimento antes, por `ck_configuracoes_abastecimento_exige_km`/`AG010`) e restaura no teardown (quilometragem antes). Cada alternancia gera linha `configuracao_alterada` em `auditoria` (append-only, nao limpavel). A fixture pula se o DSN nao parecer local; `TEST_DATABASE_ALLOW_REMOTE=1` forca (so para banco descartavel):
  `cd supabase/tests && TEST_DATABASE_URL=... pytest test_quilometragem.py -m db_local`
- Smoke pos-`db push`: `select column_name from information_schema.columns where table_name='requisicoes' and column_name like 'km_%';` (2 linhas) e `select tgenabled from pg_trigger where tgname='trg_requisicoes_validar_km';` (`A`).

## Checklist (migrations 19 a 24)

Recurso desligado por padrao (`configuracoes.checklist`); as migrations nao ligam nada. Desligar nunca apaga dados.

- 19 `checklist_tabelas`: enum `nivel_combustivel`, tabelas `checklists` (unico por `requisicao_id` + `momento` = `saida`/`chegada`) e `checklist_fotos` (ate 6, `posicao` 1..6 unica; MIME jpeg/png/webp; 1 byte a 5 MB). `caminho` e coluna GERADA (`<requisicao_id>/<momento>/<id da foto>.<ext>`): nao e informavel e nao aponta para outra requisicao. UPDATE/DELETE/TRUNCATE bloqueados por gatilho (`AG003`). RLS ligado com negacao explicita; `anon`/`authenticated` sem privilegio; `service_role` so SELECT.
- 20 `checklist_regras_e_auditoria`: janela de registro (`saida` so com requisicao `autorizada`; `chegada` so com `em_uso`; fora disso `AG014`), ator autorizador/admin ativo (`AG002`), fotos so na transacao que cria o checklist (`AG014`), auditoria `checklist_registrado` (so ids, momento, `avarias` sim/nao e contagem de fotos; sem descricao nem nomes) e a funcao de escrita `registrar_checklist`.
- 21 `checklist_storage`: bucket privado `checklists` (5 MB; jpeg/png/webp) e politica RESTRICTIVE em `storage.objects` que nega tudo a `anon`/`authenticated` no bucket. Exige o schema `storage` do Supabase.
- 22 `checklist_obrigatorio_requisicoes`: gatilho `trg_requisicoes_validar_pos_km_checklist` (ENABLE ALWAYS), depois de `trg_requisicoes_validar` e `trg_requisicoes_validar_km`. Recurso ligado: `em_uso` exige checklist `saida`; `concluida` exige `chegada`; falta => `AG013`. Desligado: o banco nao exige nada.

### Contrato do backend

- Escrever SEMPRE por RPC `public.registrar_checklist(p_requisicao_id, p_momento, p_combustivel, p_avarias, p_descricao_avarias, p_registrado_por, p_fotos)` com `service_role`. `p_fotos` = `[{"id": "<uuid gerado pelo backend>", "mime_type": "...", "tamanho_bytes": n}]`; a posicao e a ordem do array. Checklist e fotos entram na mesma transacao (ou nada entra).
- Fluxo de upload: (1) backend gera o uuid de cada foto e calcula o caminho `<requisicao_id>/<momento>/<uuid>.<ext>` (`jpg`, `png` ou `webp` conforme o MIME); (2) pede ao Storage a URL assinada de UPLOAD desse caminho e a entrega ao cliente; (3) o cliente envia o arquivo; (4) o backend chama `registrar_checklist`. O banco NAO confere se o objeto existe no Storage nem se tamanho/MIME declarados batem com o arquivo. O bucket impoe o teto de 5 MB e valida apenas o Content-Type DECLARADO no upload (nao inspeciona o conteudo). Portanto, ANTES de chamar a RPC, o backend deve conferir no Storage que o objeto existe, o tamanho real e os magic bytes (jpeg/png/webp), sem confiar na extensao nem no Content-Type; o `tamanho_bytes`/`mime_type` enviados a RPC devem vir dessa conferencia. Leitura: URL assinada de curta duracao, gerada so depois de checar permissao.
- Recurso desligado: o banco nao recusa checklist valido; o backend ignora os campos e recusa os endpoints (como em quilometragem).
- Erros a traduzir: `AG013` (checklist obrigatorio na saida/chegada), `AG014` (fora da janela / foto fora da criacao / fotos nao e lista), `AG002` (ator), `23505` com `uq_checklists_requisicao_momento` (ja existe checklist do momento) ou `uq_checklist_fotos_posicao`/PK (foto repetida), `23514` com `ck_checklists_avarias_descricao`, `ck_checklists_descricao_tamanho`, `ck_checklist_fotos_posicao` (mais de 6 fotos), `ck_checklist_fotos_mime`, `ck_checklist_fotos_tamanho`, `22P02` (combustivel fora do enum), `23503` (requisicao inexistente).
- `descricao_avarias` e texto livre e pode conter dado pessoal digitado: nao logar, nao enviar ao n8n, exibir so a autorizador/admin.
- Regra de borda: recurso ligado com viagem ja `em_uso` sem checklist de saida: a conclusao exige so o de chegada; o de saida nao pode mais ser registrado (`AG014`).
- Checklist e imutavel: nao ha edicao nem exclusao; correcao futura seria uma nova acao (nao implementada).
- Locks: o registro faz `FOR SHARE` so na linha da requisicao; sem lock em `perfis`/`veiculos`/`configuracoes` (contrato da migration 15 preservado). `55P03`/`40P01`: retry limitado, como no restante.

### Operacao

- `supabase db push` aplica 19-24 (pipeline, nunca pela VPS).
- 23 `checklist_correcoes_revisao`: (M1) o gatilho `checklists_validar` valida descricao (>500, avarias sem texto, sem avarias com texto), momento e campos nulos ANTES do INSERT, com mensagem fixa e sem DETAIL (o `Failing row contains (...)` do Postgres incluiria `descricao_avarias` no log e na resposta do PostgREST). SQLSTATE e nome da constraint nao mudam (`23514` + `ck_checklists_*`, `23502` para nulo); a validacao de conteudo agora vem antes de `AG014`/`AG002`. (B1) `ck_checklists_avarias_descricao` recriada com `~ '\S'` (so espaco em branco nao conta como descricao). (B9) sem EXECUTE para `service_role` nas funcoes-gatilho.
- M3 (permissao da migration 21): `db push --dry-run` so lista migrations pendentes e NAO revela o risco de "must be owner of table objects" ao criar a politica em `storage.objects`. Antes de producao, aplicar a 21 DE VERDADE (`db push`) num projeto ou branch Supabase de teste e fazer o smoke negativo: com um JWT `authenticated` (qualquer usuario), `list` e `upload` no bucket `checklists` devem falhar. Plano B se a politica for recusada: criar a politica RESTRICTIVE pelo painel (SQL editor com a role do painel) e registrar por nova migration que apenas VERIFICA a existencia de `checklists_bucket_sem_acesso_api` (falhando se ausente), sem tentar cria-la.
- B5 (auditoria): toda migration futura que recriar `ck_auditoria_acao`/`ck_auditoria_tabela` deve repetir TODOS os valores atuais (15 acoes e 6 tabelas hoje, incluindo `checklist_registrado`/`checklists`), senao linhas novas de outros recursos passam a falhar. Os testes `test_b3_1_*` conferem isso.
- B4 (limite aceito): o ator (`registrado_por`) e lido de `perfis` sem lock; um admin que desative/rebaixe o ator no mesmo instante pode nao ser visto por essa transacao. Aceito (contrato da migration 15: sem lock em `perfis`).
- B6 (pendencias LGPD das fotos): remocao de EXIF (GPS, data, dispositivo) antes de guardar ou ao servir; politica de retencao/expurgo das fotos e dos checklists; CSP do nginx com `img-src` e `connect-src` incluindo a origem do Storage (mudanca em `frontend/`, exige `--build`); nunca logar URLs assinadas (upload ou leitura) nem `descricao_avarias`.
- 24 `checklist_correcoes_revisao_2`: (B-a) `checklists_validar` recusa `id` nulo explicito com `23502` e mensagem fixa (o NOT NULL da PK montaria `Failing row contains` com a descricao). Recusar, e nao `coalesce(..., gen_random_uuid())`, evita mascarar entrada invalida e alterar a chave que alimenta a variavel de sessao das fotos. (B-b) os erros do gatilho preenchem `schema`, `table`, `column` e `constraint`: o backend identifica o campo pela mensagem fixa ou por `diag.column_name` (23502) / `diag.constraint_name` (23514), nunca por DETAIL. Revokes e assercao de EXECUTE reafirmados.
- Pre-check ANTES do `db push` que aplica a 23 (ou antes de tudo, em banco que ja tenha checklists gravados); deve retornar 0; se nao, tratar caso a caso, pois a tabela e imutavel: `select count(*) from checklists where descricao_avarias is not null and descricao_avarias !~ '\S';`
- Limite aceito (B-d): `\S` considera U+200B (espaco de largura zero) e similares como caractere nao-espaco; uma descricao so com eles passa como "texto". Espacos Unicode como NBSP tambem dependem do locale do banco, e fazem parte do mesmo limite aceito (nao ha ganho de seguranca em tratar).
- Configuracao de log do servidor (VERIFICAR, nao garantir): apos o `db push`, rodar `show log_parameter_max_length_on_error; show log_parameter_max_length; show log_statement; show log_min_duration_statement;` no smoke. Valores esperados: `log_parameter_max_length_on_error=0`; `log_parameter_max_length=-1` (so vale se a instrucao for logada); `log_statement=none`; `log_min_duration_statement=-1`. Se algum estiver fora do padrao (os parametros do RPC, que carregam a descricao, iriam ao log; com `log_min_duration_statement` ativo, o JSON da RPC com `descricao_avarias` vai ao log de instrucoes lentas, e esse e o caso que importa), abrir chamado/configurar o projeto. No Supabase hospedado isto e verificacao, nao garantia.
- Pendencia da fase de requisicoes (B-h): constraints de `requisicoes` (`observacao`, `trajeto`, `nome_solicitante`) geram o mesmo `DETAIL: Failing row contains (...)`. O backend e os logs nunca devem repassar details do Postgres (regra do backend); no banco, o log precisa do filtro/parametro acima ou de gatilho com mensagem fixa como o do checklist.
- Nota (B-e): quem tem INSERT em `checklist_fotos` (so o dono/`postgres`, nunca a API) consegue forjar a variavel de sessao do gatilho; limite aceito, pois o dono ja tem controle total.
- Smoke pos-push: `select id, public, file_size_limit, allowed_mime_types from storage.buckets where id='checklists';` (`public=false`, 5242880); `select tgname, tgenabled from pg_trigger where tgname like 'trg_checklist%' or tgname='trg_requisicoes_validar_pos_km_checklist';` (todos `A`); `select policyname, permissive from pg_policies where schemaname='storage' and policyname='checklists_bucket_sem_acesso_api';` (`RESTRICTIVE`).
- Testes: `supabase/tests/test_checklist.py`, SO em banco local descartavel (`supabase start` + `supabase db reset`), sem paralelismo. A fixture autouse desliga `abastecimento_manutencao`, `quilometragem` e `checklist` antes de cada teste e restaura no teardown; cada alternancia gera `configuracao_alterada` em `auditoria` (append-only) e checklists/objetos criados nao sao apagaveis. Pula se o DSN nao parecer local (`TEST_DATABASE_ALLOW_REMOTE=1` forca) e os testes de storage pulam sem o schema `storage`:
  `cd supabase/tests && TEST_DATABASE_URL=... pytest test_checklist.py -m db_local`

## Foto de perfil (migrations 25a, 25b e 25c)

Recurso SEMPRE LIGADO (sem flag em `configuracoes`); a foto e opcional e ha no maximo uma ativa por usuario. As migrations 25a (`20261107122410`) e 25b (`20261107122420`) JA ESTAO aplicadas em producao e nao podem ser editadas; a 25c (`20261107122450`) e nova e so vale depois de revisada e aplicada pelo pipeline. Ficam entre a 24 e as pendentes do Abastecimento (que devem ser renumeradas ao reativar; ver o README da pasta `pendentes`).

- 25a `perfil_foto_tabela_e_rpc`: tabela SEPARADA `perfil_fotos` (id, usuario_id FK `perfis` RESTRICT, mime jpeg/png/webp, tamanho_bytes 1..1 MB, `caminho` GERADO `<usuario_id>/<id>.<ext>`, criado_em, `removida_em`). Indice unico parcial `uq_perfil_fotos_ativa (usuario_id) where removida_em is null`. Imutavel: UPDATE so aceita `removida_em` NULL -> valor (uma vez); DELETE/TRUNCATE => `AG003` (ENABLE ALWAYS). RLS com negacao explicita; `anon`/`authenticated` sem privilegio; `service_role` so SELECT. Validacao no gatilho BEFORE INSERT com mensagem fixa e sem DETAIL (`23502`, `23514` com `ck_perfil_fotos_mime`/`_tamanho`, `AG017`). Auditoria `foto_perfil_alterada`/`foto_perfil_removida` (tabela `perfil_fotos`; ator = usuario; so mime e tamanho; sem caminho/URL/nome) por constraint trigger DEFERRED. `ck_auditoria_acao` (17 acoes) e `ck_auditoria_tabela` (7 tabelas) recriados repetindo todos os valores.
- 25c `perfil_foto_remocao_admin` (`20261107122450`, PENDENTE de aprovacao/aplicacao): coluna `perfil_fotos.removida_por` (FK `perfis` RESTRICT; NULL = dono/troca; nao informavel no INSERT; so muda na remocao, uma vez), RPC `remover_foto_perfil_por(p_usuario, p_ator)` e auditoria com o ator correto. As RPCs antigas `trocar_foto_perfil` e `remover_foto_perfil` ficam inalteradas. Nao recria `ck_auditoria_*` (continuam 17 acoes e 7 tabelas).
- 25b `perfil_foto_storage`: bucket privado `avatares` (1 MB; jpeg/png/webp) e politica RESTRICTIVE `avatares_bucket_sem_acesso_api` em `storage.objects` negando tudo a `anon`/`authenticated`. Mesmo risco de permissao da migration 21 ("must be owner of table objects"); mesmo plano B.

### Contrato do backend
- Escrever SEMPRE por RPC com `service_role`: `trocar_foto_perfil(p_usuario, p_id, p_mime, p_tamanho)` e `remover_foto_perfil(p_usuario)`, ambas devolvendo `(id_anterior, caminho_anterior)` (NULL sem foto anterior). `p_usuario` vem do JWT validado, NUNCA do cliente: o banco confia no ator informado pelo `service_role`, como nas outras RPCs. `p_id` e um uuid gerado pelo backend, que calcula `<usuario_id>/<p_id>.<ext>` (`jpg`/`png`/`webp`) para pedir a URL assinada de upload; o banco recalcula o mesmo caminho.
- Fluxo: (1) gerar `p_id` e URL de upload; (2) cliente envia; (3) backend confere no Storage que o objeto existe, tamanho real e magic bytes (o bucket so valida o Content-Type declarado), remove EXIF/GPS; (4) chama `trocar_foto_perfil`; (5) DEPOIS do commit, apaga do Storage o `caminho_anterior`. Se a RPC falhar, apagar o objeto novo. O banco nao confere o objeto. Falha no passo 5 deixa objeto orfao (limpeza por rotina futura).
- Fluxo ADOTADO (backend recodifica no servidor, decisao do usuario): (1) cliente envia a imagem ORIGINAL ao staging `avatares/tmp/<usuario_id>/<uuid>`; (2) backend le o staging, valida e RECODIFICA com Pillow (descarta EXIF/GPS e metadados; so jpeg/png/webp, <= 1 MB) e grava o resultado em `avatares/<usuario_id>/<p_id>.<ext>` com `upsert=false`; (3) chama `trocar_foto_perfil`; (4) apaga o staging e, depois do commit, o `caminho_anterior`. Convencao do bucket: `tmp/<usuario_id>/<uuid>` = nao confiavel e temporario; `<usuario_id>/<id>.<ext>` = so imagem recodificada, igual a `perfil_fotos.caminho`. A politica RESTRICTIVE ja nega anon/authenticated em todo o bucket (inclusive `tmp/`): nenhum ajuste de banco. ATENCAO: o limite de 1 MB e a lista de mimes do bucket valem tambem para o staging (foto original maior que 1 MB e recusada pelo Storage); decidir antes de implementar o upload.
- Remocao: o PROPRIO usuario e o ADMIN podem remover. Usar `remover_foto_perfil_por(p_usuario, p_ator)` (25c); `p_ator` = id do JWT validado, nunca do cliente.
- `remover_foto_perfil_por(p_usuario uuid, p_ator uuid)` -> `(id_anterior, caminho_anterior)`; EXECUTE so `service_role`. `p_ator = p_usuario`: dono (ativo ou nao). `p_ator <> p_usuario`: exige ADMIN ATIVO, senao `AG022` (mensagem fixa, sem DETAIL). Alvo inexistente `AG017`; argumento nulo `23502`; sem foto ativa devolve `(NULL, NULL)` sem erro nem auditoria (idempotente). Auditoria `foto_perfil_removida`: `ator_id` = `p_ator`, `registro_id` = id da foto (dono por join `perfil_fotos.id`), `detalhes` = `{mime, tamanho_bytes, por_admin}` (sem PII, caminho ou URL; linhas anteriores a 25c nao tem `por_admin`: ausente = false). Troca/remocao pelas RPCs antigas continuam com ator = dono.
- Erros a traduzir: `AG022` (remocao nao autorizada), `AG017` (usuario inexistente ou inativo; `remover_foto_perfil` so recusa inexistente: remover dado pessoal de conta desativada e permitido), `23514` + `diag.constraint_name` (`ck_perfil_fotos_mime`, `ck_perfil_fotos_tamanho`), `23502` + `diag.column_name`, `23505` (`uq_perfil_fotos_ativa` so ocorre fora das RPCs; PK repetida se `p_id` repetir), `55P03`/`40P01` (retry limitado). Nunca repassar `message_detail`.
- Leitura em lote (nenhuma view publica): `select usuario_id, id, caminho from perfil_fotos where usuario_id in (...) and removida_em is null`; o indice parcial cobre exatamente esse predicado. URL assinada de leitura curta, gerada depois de checar permissao; nunca logar URLs assinadas.
- Troca gera duas linhas de auditoria (`foto_perfil_removida` da antiga + `foto_perfil_alterada` da nova); remover repetido nao audita de novo.
- Codigos `AG`: `AG017` (foto) e `AG022` (remocao nao autorizada, 25c); `AG015`/`AG016` reservados ao Abastecimento; `AG018` livre; `AG019`/`AG020` acessos de login; `AG021` livre (reserva do trabalho de acessos); `AG023` em diante livres.
- Isolamento e retry: as RPCs rodam em READ COMMITTED (padrao); o conflito esperado e so a espera pelo advisory do usuario. O backend deve repetir de forma limitada em `55P03` (lock_timeout 5 s), `40P01` (deadlock) e `40001` (serializacao), sem repetir apos `AG017`/`AG022`/`23514`.

### Locks
- As RPCs tomam `pg_advisory_xact_lock(hashtextextended('sisveiculos.avatar:' || p_usuario, 0))`: chave propria por usuario, serializa trocas simultaneas do mesmo usuario (a segunda marca como removida a foto da primeira). Nao escrevem em `perfis` e NUNCA tomam `sisveiculos.perfis_admin` na mesma transacao (nao ha ciclo; o backend tambem nao deve misturar as duas coisas). `perfis` e lido sem lock (contrato da migration 15); a FK so toma FOR KEY SHARE. `lock_timeout` de 5 s (migration 16): `55P03` => retry.

### Operacao
- Smoke pos-push da 25c: `select proname from pg_proc where proname='remover_foto_perfil_por';` (1 linha) e `select has_function_privilege('anon','public.remover_foto_perfil_por(uuid,uuid)','execute');` (`false`). A 25c adiciona coluna nullable (sem rewrite de tabela), mas `ADD COLUMN ... REFERENCES perfis` toma SHARE ROW EXCLUSIVE em `perfis` por um instante; a migration define `set local lock_timeout = '5s'` (o pipeline roda como postgres e nao herda o lock_timeout do service_role) e falha em vez de enfileirar escritas se houver transacao longa em `perfis`. Nao toca `auditoria`.
- `supabase db push` aplica a 25c (25a/25b ja aplicadas) (pipeline, nunca pela VPS). A 25a recria CHECKs de `auditoria` (ACCESS EXCLUSIVE breve; valida as linhas existentes). Toda migration futura que os recriar deve repetir as 17 acoes e 7 tabelas atuais (22 e 10 com as pendentes do Abastecimento).
- Smoke pos-push: `select id, public, file_size_limit from storage.buckets where id='avatares';` (`false`, 1048576); `select tgname, tgenabled from pg_trigger where tgrelid='public.perfil_fotos'::regclass and not tgisinternal;` (6 gatilhos, todos `A`); `select policyname, permissive from pg_policies where schemaname='storage' and policyname='avatares_bucket_sem_acesso_api';` (`RESTRICTIVE`).
- Limites aceitos: o ator das RPCs e o informado pelo backend; usuario desativado no mesmo instante da troca pode nao ser visto (sem lock em `perfis`). Idem (B4): o papel do ator em `remover_foto_perfil_por` e lido de `perfis` sem lock (tomar `perfis_admin` criaria o ciclo vedado pela migration 15), mas a checagem de admin ativo e repetida logo apos o advisory do usuario; a janela residual e so entre esse segundo SELECT e o commit, e o efeito e uma remocao logica auditada com o ator. Ordem das checagens: argumentos nulos (`23502`), ator (`AG022`), alvo (`AG017`, so inexistente), lock, ator de novo (`AG022`).
- Ator na auditoria: gravado em `removida_por` (viaja na linha), e nao em variavel de sessao, porque o gatilho de auditoria e DEFERRED e leria o valor do commit, errando o ator de eventos anteriores na mesma transacao.
- LGPD, finalidade e retencao: a foto serve so para identificar visualmente o servidor no sistema; coletar o minimo (uma imagem opcional, recodificada sem EXIF/GPS no servidor); so o dono e o admin removem; a remocao e logica na tabela (a linha, sem PII alem de ids/mime/tamanho, fica e a auditoria e append-only) e o objeto e apagado do Storage pelo backend. Foto de conta DESATIVADA: o backend NAO serve URL de leitura; o dono ou o admin ainda podem remove-la. PENDENTE (adiado pelo usuario): rotina de expurgo de objetos orfaos (staging `tmp/` esquecido, objeto final de RPC que falhou, `caminho_anterior` nao apagado) e prazo de retencao das fotos removidas; nao ha funcao de listagem de orfaos.
- Testes: `supabase/tests/test_perfil_foto.py` (nao depende de flags), SO em banco local descartavel, sem paralelismo: `cd supabase/tests && TEST_DATABASE_URL=... TEST_DATABASE_CONFIRM=descartavel pytest test_perfil_foto.py -m db_local` (ver 'Testes de banco').

## Testes de banco: CI e execucao local

Os testes de `supabase/tests` (RLS, grants, funcoes SECURITY DEFINER, km, checklist, concorrencia) rodam no job `banco` do CI (`.github/workflows/ci.yml`), em PR para `main` e push em `main`, e apenas quando mudam `supabase/**` ou o proprio workflow. O job sobe um Supabase LOCAL efemero, sem secrets e sem conexao a projeto remoto, e para os containers ao final (`supabase stop --no-backup`, `if: always()`).

- Por que `supabase start` e nao um Postgres de service container: as migrations dependem de `auth.users`, do schema `storage` (bucket e politica em `storage.objects`) e dos roles/grants do Supabase. A imagem oficial e o gotrue/storage-api criam isso de forma identica a producao; reproduzir com script de bootstrap seria fragil e menos fiel. Sobem so `db`, `gotrue` e `storage-api` (o resto e excluido com `-x`), com CLI e acao fixados (versao e SHA em `ci.yml`).
- Dependencias fixadas em `supabase/tests/requirements.txt` (psycopg[binary], pytest), instaladas em venv.

Local (Docker necessario; banco descartavel, pois auditoria e append-only e os testes nao limpam):

```
supabase start -x realtime,imgproxy,kong,mailpit,postgrest,postgres-meta,studio,edge-runtime,logflare,vector,supavisor
supabase db reset --local --no-seed
# o reset recria o banco e APAGA o marcador: crie-o depois do reset e antes do pytest (so em banco descartavel)
psql "$TEST_DATABASE_URL" -c "comment on database postgres is 'sisveiculos:descartavel'"   # sem psql: docker exec -i supabase_db_<project_id> psql -U postgres -c "..."
python -m venv .venv-banco && .venv-banco/bin/pip install -r supabase/tests/requirements.txt   # Windows: .venv-banco\Scripts
TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \
  TEST_DATABASE_CONFIRM=descartavel \
  .venv-banco/bin/python -m pytest supabase/tests -c supabase/tests/pytest.ini --rootdir supabase/tests -m db_local -p no:xdist
supabase stop --no-backup
```

- Sem paralelismo. GUARDA DE BANCO DESCARTAVEL (`supabase/tests/dbhelpers.py`): os testes gravam e nunca apagam, entao so rodam se TODAS as condicoes valerem: (1) `TEST_DATABASE_URL`; (2) `TEST_DATABASE_CONFIRM=descartavel`; (3) host local EXPLICITO (localhost, 127.0.0.1, ::1 ou socket `/...`; DSN com `hostaddr`, `service`, `passfile` ou lista de hosts e recusado, e host vazio so vale sem `PGHOST`/`PGHOSTADDR`/`PGSERVICE` no ambiente); (4) `current_database()` com nome de teste (`postgres` so em host local; remoto exige token test/teste/tmp/dev/local/descartavel separado por `_` ou `-`); (5) MARCADOR POSITIVO no banco (`comment on database <nome> is 'sisveiculos:descartavel'`, comando acima; o CI o cria no passo "Marcar banco como descartavel", depois do `db reset`). Qualquer falha (inclusive banco inacessivel) PULA os testes com um aviso do pytest que diz o motivo sem repassar detalhes do erro (o texto do skip em cada arquivo pode ainda dizer "TEST_DATABASE_URL nao definida"; vale o aviso). `TEST_DATABASE_ALLOW_REMOTE=1` e a excecao explicita para o host (item 3) e NAO dispensa os demais itens; nunca use em banco real. Risco residual: um tunel/port-forward local apontando para banco real passa nos itens 3 e 4; so a confirmacao e o marcador (que NUNCA deve ser criado em banco real) protegem. O job `banco` do CI exporta `TEST_DATABASE_CONFIRM: descartavel` (Supabase local efemero).
- Windows: se a porta 54322 for recusada ("forbidden by its access permissions"), ela esta numa faixa reservada pelo Hyper-V/WSL; mude `[db] port` (e `[api]`) em um `config.toml` de copia local, nao no do repositorio, e ajuste a URL.
- Testes `db_virgem` (banco sem admin ativo) ficam fora deste job; rodar em banco recem-resetado, separadamente.
- TRUNCATE com FK: `requisicoes` (e `perfis`, `veiculos`) sao referenciadas por FK (ex.: `checklists`). O TRUNCATE simples e recusado pelo proprio Postgres (SQLSTATE `0A000`) ANTES do gatilho; o gatilho `AG003` (ENABLE ALWAYS) so dispara quando o TRUNCATE chega a executar, p.ex. `truncate ... cascade` ou truncando as tabelas referenciadoras na mesma instrucao. Em todos os casos o TRUNCATE continua bloqueado; os testes provam `AG003` nesses dois caminhos e aceitam `AG003` ou `0A000` no TRUNCATE simples.
- Estabilidade (Windows/WSL): a VM do Docker Desktop pode congelar ~50 s sob carga e o relogio do container fica defasado/e corrigido depois. Isso ja causou falha espuria em testes de concorrencia (timeouts curtos de barreira/join) e uma vez uma violacao de `chegada_real_em > saida_real_em` por o relogio recuar entre duas transacoes. Os timeouts dos testes de concorrencia foram ampliados (barreira 60 s, join 90 s); se uma falha isolada desse tipo reaparecer localmente, rode de novo antes de investigar o banco.
- Como reconhecer o relogio defasado do Docker: falha isolada com violacao de `ck_requisicoes_chegada_apos_saida` / `chegada_real_em > saida_real_em` (o DETAIL mostra horarios que recuam) ou timeout de barreira/join nos testes de concorrencia, em testes que passam ao repetir. Correcao: `wsl --shutdown` e reiniciar o Docker Desktop (ou so reiniciar o Docker Desktop), subir o Supabase de novo, RECRIAR o marcador do banco e rodar `pytest --lf` para repetir so as falhas. O `ci.yml` roda em Linux (runner GitHub) e nao e afetado por isso.
- Testes de "migration idempotente" nunca podem deixar a reaplicacao no banco: reaplicar uma migration antiga sobrescreve definicoes mais novas (ex.: o gatilho `trg_auditoria_notificar` perdia os avisos de motorista e quebrava `test_notificacoes_motorista.py`). Use `h.reaplicando_migration()` (`dbhelpers.py`), que termina sempre em ROLLBACK.
- Dedupe de `reservar_envio_email` (migration 20261115120000): envio pendente/enviando IDENTICO nos ultimos 30 s e devolvido sem consumir cota. Testes de cota precisam variar `filtros` a cada chamada; a dedupe tem teste proprio em `test_rpcs_atomicas.py`.

### Conferir o job de expurgo de localizacoes (N-04)

A migration `20261116120100_agenda_expurgo_localizacoes.sql` agenda o expurgo mensal via `pg_cron`; se a extensao faltar, so emite NOTICE e nao agenda nada. Depois de todo `supabase db push` (e no stack local), confira:

```
select jobid, jobname, schedule, command, active from cron.job where jobname = 'expurgar-localizacoes';
-- esperado: 1 linha, schedule '23 3 1 * *', command 'select public.expurgar_localizacoes_vencidas()', active = t
-- local sem psql: docker exec supabase_db_<project_id> psql -U postgres -c "<consulta acima>"
```

Zero linhas: habilite `pg_cron` no painel do Supabase (Database > Extensions) e reaplique o agendamento por NOVA migration (nunca editando a existente). Ultimas execucoes: `select status, start_time, return_message from cron.job_run_details order by start_time desc limit 5;`.

## Abastecimento e manutencao (migrations 25 a 28) — PENDENTE (versao 1.1)

> **Estas migrations NAO estao em `supabase/migrations/`.** Ficam em `supabase/pendentes/abastecimento_manutencao/` (junto do teste e de um README com o passo a passo para reativar) ate existirem backend, frontend e revisao de seguranca. Nao foram aplicadas em producao. As migrations ativas vao ate a 25c (`20261107122450`, quando aprovada; antes dela ficam as de acessos de login 122430/122440); renumerar estas para depois dela ao reativar.

Recurso desligado por padrao (`configuracoes.abastecimento_manutencao`); as migrations nao ligam nada. Desligar nunca apaga dados. A dependencia da Quilometragem ja existe desde a migration 8 (`ck_configuracoes_abastecimento_exige_km` + `AG010`) e NAO foi recriada: ligar abastecimento sem quilometragem, ou desligar a quilometragem com abastecimento ligado, falha com `AG010`.

- 25 `abastecimento_manutencao_tabelas`: enums `tipo_combustivel` (gasolina, etanol, diesel, gnv, outro), `tipo_manutencao` (revisao, troca_oleo, pneus, freios, outro) e `motivo_anulacao` (erro_de_digitacao, duplicado, registro_indevido); tabelas `abastecimentos` (sem texto livre), `manutencoes` (`descricao` livre, max 200) e `abastecimentos_anulacoes`/`manutencoes_anulacoes` (1 por registro, PK = FK). Registros e anulacoes sao imutaveis (UPDATE/DELETE/TRUNCATE => `AG003`, ENABLE ALWAYS). RLS com negacao explicita; `anon`/`authenticated` sem privilegio; `service_role` so SELECT.
- 26 `abastecimento_manutencao_regras`: odometro, ator, requisicao, views de registro vigente e erros sem conteudo da linha.
- 27 `abastecimento_manutencao_auditoria_e_rpc`: recria `ck_auditoria_acao` (20 acoes) e `ck_auditoria_tabela` (9 tabelas), gatilhos de auditoria e as RPCs.
- 28 `revisao_alerta_km`: tabela `revisao_regras`, RPC `definir_regra_revisao` e view `revisao_situacao`.

### Decisoes
- Correcao = ANULACAO auditada (nao retificacao): o registro original nunca muda; "vigente" = sem anulacao. Anular so remove um ponto do odometro, nunca quebra a sequencia; retificacao encadeada exigiria limites por posicao.
- Odometro (`AG015`): km novo >= maior km entre os registros vigentes do veiculo, nas DUAS tabelas, por ordem de REGISTRO (nao pela data informada). Vale sempre (invariante do dado), ligado ou desligado o recurso. Nao confronta `km_saida`/`km_chegada` de requisicoes (tabela imutavel e sem anulacao: um erro la travaria o veiculo).
- Locks (contrato da migration 15 preservado): advisory de transacao `pg_advisory_xact_lock(hashtextextended('sisveiculos.odometro:' || veiculo_id, 0))`, chave propria por veiculo, tomado no gatilho BEFORE INSERT depois de leituras sem lock e antes de qualquer lock de linha. Sem `FOR SHARE` em `perfis`/`veiculos`/`configuracoes`. Essa transacao nunca toma `sisveiculos.perfis_admin`: nao ha ciclo; o backend nao deve misturar registro de odometro com escrita administrativa na mesma transacao. `lock_timeout` de 5 s (migration 16) vale: `55P03` => retry limitado.
- Limite do alerta: `revisao_regras` (1 linha por veiculo: `intervalo_km` 1..1000000, `aviso_km` 0..intervalo, padrao 1000), so admin ativo via `definir_regra_revisao`, auditada. Base DERIVADA: maior km das manutencoes vigentes do tipo `revisao`; km atual = maior km vigente. `revisao_situacao.situacao`: `sem_referencia` (nenhuma revisao registrada), `em_dia`, `proxima` (km_atual >= proxima - aviso), `vencida` (km_atual >= proxima). Veiculo sem regra nao aparece. O backend so le a view.

### Contrato do backend
- Escrever SEMPRE por RPC com `service_role` (sem INSERT direto): `registrar_abastecimento(p_veiculo_id, p_requisicao_id, p_abastecido_em, p_litros, p_valor_total, p_km, p_combustivel, p_registrado_por)`, `registrar_manutencao(p_veiculo_id, p_tipo, p_realizada_em, p_km, p_valor, p_descricao, p_registrado_por)`, `anular_abastecimento(p_id, p_motivo, p_anulado_por)`, `anular_manutencao(...)`, `definir_regra_revisao(p_veiculo_id, p_intervalo_km, p_aviso_km, p_definido_por)`. Opcionais vao como `null`. O ator vem do JWT validado, nunca do body. Ler pelas views `abastecimentos_vigentes`, `manutencoes_vigentes`, `odometro_registros_vigentes`, `revisao_situacao` (as tabelas tem tambem os anulados).
- Recurso desligado: o banco nao recusa nada do recurso; o backend ignora os campos e recusa os endpoints (como em quilometragem e checklist).
- Erros a traduzir: `AG002` (ator), `AG015` (km menor que o ultimo), `AG016` (requisicao de outro veiculo), `AG010` (dependencia de flag), `23514` + `diag.constraint_name` (`ck_abastecimentos_litros_positivo`, `_valor_nao_negativo`, `_km_nao_negativo`, `_data_nao_futura`; `ck_manutencoes_descricao`, `_km_nao_negativo`, `_valor_nao_negativo`, `_data_nao_futura`; `ck_revisao_regras_intervalo`/`_aviso`), `23502` + `diag.column_name` (campo obrigatorio de manutencao), `23503` (veiculo, requisicao ou registro inexistente), `23505` com `pk_*_anulacoes` (ja anulado), `22P02` (enum), `22003` (litros/valor fora da precisao), `55P03`/`40P01` (retry). Data futura: tolerancia de 5 minutos.
- `manutencoes.descricao` e texto livre: o gatilho recusa com mensagem fixa, sem `DETAIL`/`Failing row`; nunca logar, nem enviar a auditoria/n8n; exibir so a autorizador/admin. Nunca repassar `message_detail` do Postgres.
- Auditoria (sem PII): `abastecimento_registrado` e `manutencao_registrada` (so ids, tipo/combustivel e km), `*_anulado/a` (motivo em `para`), `revisao_regra_definida` (ids e valores da regra). Toda migration futura que recriar `ck_auditoria_acao`/`ck_auditoria_tabela` deve repetir os 20 valores e as 9 tabelas atuais.
- Funcoes-gatilho e privadas (`privado.exigir_*`) sem EXECUTE para ninguem da API; a migration falha se sobrar.

### Operacao
- `supabase db push` aplica 25-28 (pipeline, nunca pela VPS). A 27 recria CHECKs de `auditoria` (ACCESS EXCLUSIVE breve; valida as linhas existentes).
- Smoke pos-push: `select tgname, tgenabled from pg_trigger where tgname like 'trg_abastecimentos%' or tgname like 'trg_manutencoes%' or tgname like 'trg_revisao_regras%';` (todos `A`, exceto `trg_revisao_regras_atualizado_em`); `select count(*) from pg_views where schemaname='public' and viewname in ('abastecimentos_vigentes','manutencoes_vigentes','odometro_registros_vigentes','revisao_situacao');` (4); rodar `get_advisors` (security) e conferir as views `security_invoker`.
- Testes: `supabase/tests/test_abastecimento_manutencao.py`, SO em banco local descartavel, sem paralelismo. A fixture autouse desliga `abastecimento_manutencao`, `quilometragem` e `checklist` antes de cada teste (nessa ordem) e restaura no teardown (km, abastecimento, checklist); cada alternancia gera `configuracao_alterada` em `auditoria` (append-only). Pula se o DSN nao parecer local (`TEST_DATABASE_ALLOW_REMOTE=1` forca):
  `cd supabase/tests && TEST_DATABASE_URL=... pytest test_abastecimento_manutencao.py -m db_local`

## Segredos

`SEED_ADMIN_EMAIL` e `DATABASE_URL` vivem apenas nos secrets do pipeline ou no ambiente do operador. Nao commitar nem registrar em log.
