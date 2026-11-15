# Limpeza dos dados de teste antes do lançamento

Guia para quem **não é técnico**. Serve para apagar, **uma única vez**, os dados fictícios usados nos testes em produção e deixar o sistema limpo para os dados reais.

Versão em PDF para imprimir: [`docs/limpeza-geral-producao.pdf`](limpeza-geral-producao.pdf) (gerada de `limpeza-geral-producao.html`).

São duas ferramentas, sempre nesta ordem:

1. **SQL** (`supabase/ops/limpar_dados_de_teste.sql`), colado no SQL Editor do Supabase: apaga as **linhas do banco**.
2. **Storage** (`python -m app.ops.limpar_storage`), rodado no container do backend na VPS: apaga os **arquivos** (fotos). O SQL não mexe em arquivos.

As duas começam em **ensaio** (mostram contagens e não apagam nada) e só apagam com uma frase de confirmação que inclui o ref do projeto, `ofwvoztarroglekfwohs`.

> **ATENÇÃO**
> - Use **somente antes do uso real**. **NUNCA** rode depois que o sistema tiver dados verdadeiros.
> - Isto é uma **EXCEÇÃO ÚNICA, pré-lançamento**, à regra "sem DELETE físico / auditoria append-only", aplicada sobre **dados fictícios**, com os gatilhos de proteção religados e conferidos no fim. Não é função do sistema nem migration.
> - Depois da execução real **não há desfazer pelo script**. Por isso: **backup** (passo 1) e **ensaio** antes de apagar.

## Escolha o modo

Antes de começar, decida **um** dos dois modos. Ele vale para o SQL **e** para o Storage.

| | **Modo A: manter usuários** (padrão) | **Modo B: só admins** |
|---|---|---|
| Para quê | As contas criadas nos testes já são as pessoas reais | As contas de teste devem sumir; ficam só os admins |
| Contas (`perfis` + Auth) | Todas ficam | Ficam **só os admins ativos**; as demais são apagadas (perfil e conta do Auth) |
| Convites | Ficam | **Todos** são apagados |
| Fotos de perfil, push, passkeys | Ficam | Ficam só as dos admins ativos |
| No SQL | `v_manter_usuarios := true` (já vem assim) | `v_manter_usuarios := false` |
| Frase do SQL | termina em `E AUDITORIA` (padrão) | termina em `E USUARIOS` |
| No Storage | `--executar` | `--executar --apagar-usuarios` |

No modo B o script **recusa** se não houver ao menos 1 admin ativo (LD008). No modo B, **mantenha a auditoria apagada** (o padrão): se você preservar a auditoria, ela aponta para as contas que seriam apagadas e o script recusa (LD009).

Use o **mesmo modo** no ensaio e na execução: o número do ensaio muda conforme o modo.

## O que é apagado e o que fica

| Sempre apagado (dado operacional dos testes) | Sempre mantido (cadastro e configuração) |
|---|---|
| Requisições (`requisicoes`) e arquivadas (`requisicoes_arquivadas`) | Veículos (`veiculos`), inclusive o km atual |
| Checklists e fotos de checklist (`checklists`, `checklist_fotos`) | Configurações (`configuracoes`) |
| Notificações (`notificacoes`) | Configurações da IA (`ia_configuracoes`) |
| Localizações de viagem (`localizacoes_viagem`) | Saudações (`saudacoes`) |
| Envios de relatório por e-mail (`envios_email`) | |
| Uso de IA (`ia_uso`) | |
| Acessos de login (`acessos_login`) | |
| Desafios e reautenticações de passkey (`desafios_webauthn`, `reautenticacoes_webauthn`) | |
| **Auditoria** (padrão; ver abaixo) | |

Depende do modo (tabela acima): `perfis`, contas do Auth (`auth.users`), `convites`, `perfil_fotos`, `push_inscricoes`, `credenciais_webauthn`.

Toda tabela do banco está classificada (21 no total). Se aparecer uma tabela que o script não conhece, ele **recusa** (LD004): não force, avise quem mantém o sistema.

**Auditoria:** agora é **apagada por padrão** (`v_apagar_auditoria := true`). Depois da execução real sobra **1 linha**: o registro da própria limpeza (só contagens, sem dado pessoal). Com isso some também o histórico de como as contas reais nasceram (`admin_semeado`, `perfil_criado`). Para **manter** a auditoria inteira, mude para `v_apagar_auditoria := false`; a frase de confirmação muda junto (ver passo 5).

**Arquivos (Storage):**
- Bucket `checklists`: sempre esvaziado.
- Bucket `avatares` (fotos de perfil): no modo A fica só a **foto atual** de cada pessoa; somem as fotos já trocadas ou removidas, os arquivos órfãos (sem registro em `perfil_fotos`) e os envios temporários não concluídos. No modo B fica só a foto atual dos admins ativos.
- Qualquer outro bucket: só aparece na contagem, **nunca** é apagado.

**Fora do escopo:** `pg_cron`/jobs agendados, as funções de expurgo e os gatilhos de TRUNCATE não são tocados.

## Passo a passo

### 1. Backup
No painel do Supabase, **Database > Backups**: o backup diário só existe nos planos **pagos**. Confira se há um backup recente. **Sem backup, faça antes uma cópia (pg_dump/export do banco) e guarde-a.**

Confira que está no projeto certo: o endereço do painel deve ter `ofwvoztarroglekfwohs` (`supabase.com/dashboard/project/ofwvoztarroglekfwohs`). Se for outro, pare.

### 2. Aplique as migrations pendentes
Pelo pipeline (**nunca** pelo servidor), rode `supabase db push`. Precisam estar aplicadas, entre outras:
- `20261127120000_km_percorrido.sql`
- `20261128120000_perfis_telefone.sql`
- `20261128120100_convites_telefone.sql`
- `20261128120200_convites_telefone_expurgo.sql`
- `20261129120000_ops_banco_lacrado.sql` (função que o Storage usa para conferir o lacre)

Sem elas o script recusa com `ESTRUTURA INESPERADA` (LD004).

Sem a `20261129120000`, o ensaio do Storage mostra `AVISO: não foi possível ler o lacre do banco` e a execução **recusa**.

Depois do push, atualize a VPS (`git pull` e `docker compose up -d --build`) para que o backend tenha a ferramenta de Storage.

### 3. Pare o backend
Na VPS, na pasta do projeto: `docker compose stop backend`.

O script trava as tabelas por instantes (o ensaio também). Se alguém estiver usando o sistema, ele espera 15 s e **desfaz tudo com segurança** (erro `lock timeout`); é só tentar de novo. O backend fica parado **só durante o ensaio e a execução do SQL**.

### 4. Ensaio do SQL
1. No painel: **SQL Editor** > **New query**.
2. Abra `supabase/ops/limpar_dados_de_teste.sql`, copie **tudo** e cole.
3. Se for o **modo B**, mude só `v_manter_usuarios := false`. No modo A não mude nada.
4. Clique em **Run**.
5. Aparece uma mensagem de **erro** começando por `ENSAIO: nada foi apagado`. É o esperado. Nela há:
   - em destaque: `*** COPIE ESTE NUMERO PARA v_confere_total: N ***`;
   - quantas linhas seriam apagadas em cada tabela e quantas ficam;
   - quantas contas seriam apagadas e quantas mantidas;
   - quantos arquivos há em cada bucket (só leitura).
6. **Copie ou fotografe essa mensagem.**

Confira: os números fazem sentido para os seus testes? No modo B, o número de contas mantidas é o de admins que você espera? Se algo parecer errado, **não continue**.

### 5. Execução do SQL
No topo do arquivo (na mesma consulta, mantendo o `v_manter_usuarios` do ensaio), mude **somente** estas linhas (o número é o do ensaio):

```
v_executar         boolean := true;
v_confirmacao      text    := '<frase da tabela abaixo>';
v_confere_total    int     := N;   -- o número copiado do ensaio
```

| Modo | Auditoria | Frase exata |
|---|---|---|
| A (manter usuários) | apagar (padrão) | `APAGAR DADOS DE TESTE ofwvoztarroglekfwohs E AUDITORIA` |
| A (manter usuários) | manter (`v_apagar_auditoria := false`) | `APAGAR DADOS DE TESTE ofwvoztarroglekfwohs` |
| B (só admins) | apagar (padrão) | `APAGAR DADOS DE TESTE ofwvoztarroglekfwohs E AUDITORIA E USUARIOS` |
| B (só admins) | manter (`v_apagar_auditoria := false`) | `APAGAR DADOS DE TESTE ofwvoztarroglekfwohs E USUARIOS` (normalmente recusa com LD009) |

Ou seja: a frase diz o que será apagado. Se a frase não bater com os parâmetros, o script recusa (LD003).

Clique em **Run**. Se o Studio mostrar aviso sobre `DELETE`, confirme.

O número do ensaio só vale naquele momento: qualquer linha nova nas tabelas do escopo o invalida (LD007). Por isso o backend deve estar parado.

Deu certo quando não aparece erro e o resultado final mostra:
- **0** em todas as tabelas "sempre apagadas";
- `auditoria` com **1** (ou as linhas de antes **+1**, se você manteve a auditoria);
- as tabelas mantidas com as mesmas quantidades do ensaio (no modo B, `perfis` com o número de admins ativos e `convites` com 0).

**Copie/fotografe o resultado.** Depois **apague o snippet salvo no Studio** (menu do snippet > Delete).

Opcional: `v_lacrar := true` já grava o lacre (passo 10) ao fim da execução. Use só se esta for a limpeza final e o lançamento for imediato.

### 6. Religue o backend
`docker compose start backend`. A ferramenta de Storage roda **dentro** do backend, então ele precisa estar no ar. Espere ficar saudável (`docker compose ps` mostra `healthy`).

### 7. Ensaio do Storage
Na VPS, na pasta do projeto:

- Modo A: `docker compose exec backend python -m app.ops.limpar_storage`
- Modo B: `docker compose exec backend python -m app.ops.limpar_storage --apagar-usuarios`

Mostra só **contagens** por bucket (quantos arquivos seriam apagados e quantos ficam). Não apaga nada. Nunca mostra segredos, links ou nomes de arquivo. Confira se os números fazem sentido.

### 8. Execução do Storage
Repita o comando do mesmo modo com `--executar`:

- Modo A: `docker compose exec backend python -m app.ops.limpar_storage --executar`
- Modo B: `docker compose exec backend python -m app.ops.limpar_storage --executar --apagar-usuarios`

Ele pede a frase: digite exatamente `APAGAR ARQUIVOS ofwvoztarroglekfwohs` e tecle Enter. Frase errada: recusa e não apaga nada. Se o banco já estiver lacrado, ele também recusa.

Rode o Storage **depois** do SQL (como aqui): assim as fotos a apagar são calculadas com o banco já limpo.

### 9. Conferência
- Entre no sistema com o administrador real. A lista de requisições deve estar vazia e o veículo, livre.
- No modo A, confira que as contas reais continuam entrando. No modo B, só os admins existem; cadastre (convide) os usuários reais a partir daqui.
- Rode de novo o ensaio do Storage (passo 7): `checklists` deve mostrar 0 a apagar.
- Normal: `acessos_login`, `auditoria` e `notificacoes` ganham linhas novas por causa do seu login e do uso. Se rodar o SQL de novo como ensaio, os números não serão zero por isso.

### 10. Lacre no lançamento
O lacre faz o banco **recusar** a limpeza (SQL e Storage) daí em diante.
1. SQL Editor > nova consulta, cole `supabase/ops/lacrar_producao.sql`.
2. Mude `v_confirmacao` para `'LACRAR PRODUCAO ofwvoztarroglekfwohs'` e clique em **Run**.
3. O resultado mostra `sisveiculos:lacrado` e o comentário anterior substituído. Só o comentário padrão (`default administrative connection database`) ou vazio é substituído; qualquer outro, inclusive `sisveiculos:descartavel`, faz o script recusar.
4. Apague o snippet salvo no Studio.

**Limites do lacre:** protege contra **erro e esquecimento**, não contra má-fé (o dono do banco desfaz com um `COMMENT`). O comentário pode **se perder** em restore de backup ou recriação do projeto; depois de um restore, confira:
`select shobj_description(oid, 'pg_database') from pg_database where datname = current_database();`

### Como desfazer o lacre (só se precisar limpar de novo antes do lançamento)
Decisão consciente do dono do banco, só se ainda **não houver nenhum dado real**. No SQL Editor:

```
comment on database postgres is 'default administrative connection database';
```

Depois recomece pelo passo 1.

## Se der erro
- O SQL é uma transação única: ou apaga tudo, ou nada. Copie a mensagem inteira e envie para quem mantém o sistema. As mensagens trazem só contagens, nunca dados pessoais.
- `RECUSADO ... LACRADO` (LD002): o banco está lacrado, não rode.
- `RECUSADO ... v_confirmacao` (LD003): a frase não está exata ou não bate com os parâmetros (veja a tabela do passo 5).
- `RECUSADO ... parametro nulo` (LD003): algum parâmetro ficou `NULL`; volte ao valor original.
- `RECUSADO ... v_confere_total` (LD007): o número não bate com o total atual. Refaça o ensaio e copie o número novo.
- `RECUSADO ... comentario proprio` (LD006): o banco tem um comentário que o lacre não sobrescreve (ou é o banco de testes). Use `v_lacrar := false` e avise.
- `RECUSADO: nao ha nenhum admin ativo` (LD008): cadastre ou reative um admin antes. Não continue sem isso.
- `RECUSADO: linhas que ficam apontam para contas que seriam apagadas` (LD009): no modo B você manteve a auditoria (ou algo que fica aponta para uma conta de teste). Volte `v_apagar_auditoria := true` (e a frase com ` E AUDITORIA E USUARIOS`) ou use o modo A.
- `SEM PERMISSAO` / `ESTRUTURA INESPERADA` (LD004): falta migration (rode o `db push`), o usuário não é dono das tabelas ou existe tabela que o script não conhece. Não force.
- `VERIFICACAO FALHOU` (LD005): o script percebeu algo estranho (gatilhos, contagens) e desfez tudo.
- `lock timeout`: alguém estava usando o sistema; pare o backend e tente de novo.
- Storage `RECUSADO ... Nada foi apagado.`: frase errada, banco lacrado ou lacre não pôde ser conferido (falta a migration `20261129120000`, passo 2) ou variáveis do backend ausentes.
- Storage `FALHA ...`: pode ter apagado uma parte. O que já foi apagado não volta, mas é seguro rodar de novo (ensaio primeiro); ele só apaga o que ainda sobrou.
