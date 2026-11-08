# Limpeza dos dados de teste antes do lançamento

Guia para quem **não é técnico**. Serve para apagar, **uma única vez**, os dados fictícios usados nos testes em produção, deixando o sistema limpo antes do uso real.

> **ATENÇÃO**
> - Use **somente antes do uso real**. **NUNCA** rode depois que o sistema tiver dados verdadeiros: ele apaga requisições, checklists, fotos de perfil, acessos de login e a auditoria.
> - Isto é uma **EXCEÇÃO ÚNICA, pré-lançamento**, à regra "sem DELETE físico / auditoria append-only", aplicada sobre **dados fictícios**, com os gatilhos de proteção religados e verificados no fim. Não é uma função do sistema nem uma migration: é um arquivo colado no SQL Editor do Supabase.
> - Depois que a limpeza real termina, **não há desfazer pelo script**. Por isso: ensaio antes (passo 2) e **backup** (passo 1). ("Não há desfazer" vale só para o script; o backup do painel existe para isso.)

## O que é apagado e o que fica

| Apagado | Fica |
|---|---|
| Requisições (e seus checklists e fotos de checklist) | Usuários (`perfis`), que **não** podem ser apagados |
| Fotos de perfil (registros) | Veículos |
| Acessos de login | Configurações (recursos ligados/desligados) |
| Auditoria (se `v_apagar_auditoria := true`, o padrão) | Convites |
| | Contas do Supabase Auth |

**Auditoria:** depois da limpeza real, a auditoria mostra **1 linha** (ou **N+1** se você preservou a auditoria, com N as linhas de antes): o registro da própria limpeza (sem dados pessoais, com as contagens apagadas). Com `v_apagar_auditoria := true`, somem junto os eventos `admin_semeado` e `perfil_criado` dos perfis **reais** (o histórico de como as contas verdadeiras nasceram). Se quiser guardar esse histórico, use `v_apagar_auditoria := false`: a auditoria inteira é preservada (e ganha a linha da limpeza). Os registros de teste ficam misturados, mas nada se perde.

**Fora do escopo (continuam existindo):**
- Os **arquivos** das fotos (buckets `avatares` e `checklists`) **não** são apagados pelo SQL. Enquanto não forem esvaziados (passo 5), os arquivos órfãos continuam guardando **dado pessoal**.
- Dados pessoais do Supabase Auth que o script não toca (LGPD): `auth.sessions` e `auth.audit_log_entries` (IP, navegador, e-mail). Confira também se existe **algum outro bucket** de Storage além de `avatares` e `checklists`; se existir, esvazie-o também.

## Antes de começar

1. **Backup.** No painel: **Database > Backups** só tem backup diário nos planos **pagos**. Confira se há um backup recente. **Sem backup, faça antes uma cópia (pg_dump/export do banco) e guarde-a:** sem backup, "não há desfazer" vale de verdade.
2. **Sistema parado.** O script trava as tabelas por instantes (o ensaio também): rode **fora de horário** ou, na VPS, pare o backend: `docker compose stop backend`. Mantenha o backend parado **só durante o ensaio e a execução real**; religue-o logo depois (passo 4), pois os passos seguintes (Admin > Usuários, login) precisam do backend no ar. Se alguém estiver usando o sistema, o script espera 15 s e **desfaz tudo com segurança** (erro de `lock timeout`); é só tentar de novo.
3. Abra o painel do projeto certo. O endereço deve ter `ofwvoztarroglekfwohs` (`supabase.com/dashboard/project/ofwvoztarroglekfwohs`). Se for outro projeto, pare.

## Passo a passo

### 1. Backup e sistema parado
Faça os itens da seção anterior.

### 2. Rode o ensaio e leia as contagens
1. No painel: **SQL Editor** > **New query**.
2. Abra `supabase/ops/limpar_dados_de_teste.sql`, copie **tudo** e cole.
3. Clique em **Run** sem mudar nada.
4. Aparece uma mensagem de **erro** começando por `ENSAIO: nada foi apagado`. Isso é o esperado: é assim que o ensaio mostra o resultado e garante que nada foi apagado. Nela há:
   - em destaque: `*** COPIE ESTE NUMERO PARA v_confere_total: N ***`;
   - uma tabela com quantas linhas seriam apagadas em cada tabela, e o que é mantido. `N` é o **total** de linhas das 6 tabelas do escopo (requisições, checklists, fotos de checklist, fotos de perfil, acessos de login e auditoria).
5. **Copie ou fotografe essa mensagem** antes de seguir.

### 3. Confira
- As quantidades fazem sentido para os seus testes?
- Na linha `MANTIDAS`, os números de usuários, veículos, configurações e convites estão como você espera?
- Em "Arquivos nos buckets" veja quantos arquivos terá de apagar no passo 5.

Se algo parecer errado, **não continue**.

### 4. Apague de verdade
No topo do arquivo (na mesma consulta), mude **somente** estas três linhas (o número é o do ensaio):

```
v_executar            boolean := true;
v_confirmacao         text    := 'APAGAR DADOS DE TESTE ofwvoztarroglekfwohs';
v_confere_total    int     := N;   -- o número copiado do ensaio
```

Clique em **Run**. O Studio **provavelmente** mostrará um aviso sobre `DELETE` (não validado: depende da versão do painel); se aparecer, confirme.

A dupla confirmação (frase + número **total**) existe para que um texto salvo no Studio, rodado por engano depois, **não apague nada**: o número só vale para o momento do ensaio (qualquer linha nova em qualquer das 6 tabelas o invalida). Mesmo assim, depois de rodar, **APAGUE o snippet salvo no Studio** (menu do snippet > Delete). Se o campo ficar vazio (`NULL`), o script recusa.

Deu certo quando: não aparece erro e o resultado final é uma tabela em que `requisicoes`, `checklists`, `checklist_fotos`, `perfil_fotos` e `acessos_login` mostram **0**; `auditoria` mostra **1** (o registro da limpeza; **N+1** se você preservou a auditoria); `perfis`, `veiculos`, `configuracoes` e `convites` mostram as mesmas quantidades do ensaio; e a coluna `apagadas_agora` mostra quantas linhas foram apagadas em cada tabela. **Copie/fotografe esse resultado.**

**Agora religue o backend** (`docker compose start backend`): os passos 6 e 7 precisam dele no ar. Só depois de conferir o resultado acima.

Outras opções (no topo do arquivo):
- `v_apagar_auditoria := false` mantém a auditoria.
- `v_lacrar := true` já grava o lacre (passo 8). **Grave o lacre assim que houver uso real**, e apague sempre o snippet salvo. **Sugerido quando esta for a limpeza final e o lançamento for imediato.** O ensaio, com `v_lacrar := true`, também prova que o seu usuário consegue gravar o lacre (e desfaz).

### 5. Esvazie os buckets de arquivos
No painel: **Storage** > bucket **avatares** > selecione tudo > apague. Repita no bucket **checklists** (e em qualquer outro bucket que exista). Não apague os buckets em si, só os arquivos. **Enquanto isso não for feito, os arquivos antigos (com dado pessoal) continuam lá.**

### 6. Desative as contas de teste
Os usuários de teste continuam existindo (o sistema nunca apaga usuários, para preservar o histórico). Para que não entrem mais:
- No sistema: **Admin > Usuários** e **desative** cada conta de teste. O perfil fica desativado e não consegue mais entrar.
- Se quiser, no painel **Authentication > Users** você pode **banir** a conta.
- **NÃO remova** usuários do Auth: o perfil referencia a conta do Auth e **a remoção falha** (e, se algum dia funcionasse, perderia o histórico). Só desative no sistema e/ou bana no Auth.
- Nunca desative nem bana a conta do administrador verdadeiro.

### 7. Faça um login e confira que está limpo
Entre no sistema com o administrador real. A lista de requisições deve estar vazia e o veículo, livre. Para conferir, compare com o **RESULTADO FINAL copiado na execução real** (passo 4). Atenção: `acessos_login` terá linhas novas por causa do seu login, e `auditoria` por causa da desativação das contas e do próprio login; isso é normal. Se rodar o arquivo de novo como ensaio, os números do escopo serão esses, não zero.

### 8. Lacre no lançamento
O lacre faz o banco **recusar** esta limpeza daí em diante.
1. SQL Editor > nova consulta, cole `supabase/ops/lacrar_producao.sql`.
2. Mude `v_confirmacao` para `'LACRAR PRODUCAO ofwvoztarroglekfwohs'` e clique em **Run**.
3. O resultado mostra `sisveiculos:lacrado` e o comentário anterior que foi substituído. Em produção o comentário do banco é o texto padrão `default administrative connection database`; ele (ou um comentário vazio) é substituído pelo lacre. Qualquer **outro** comentário não é sobrescrito: o script recusa e mostra qual é. O marcador `sisveiculos:descartavel` (banco de teste) também nunca é sobrescrito.
4. Apague o snippet salvo no Studio.

**Limites do lacre:** protege contra **erro e esquecimento**, não contra má-fé: quem é dono do banco desfaz com um simples `COMMENT`. Além disso, o comentário do banco pode **se perder** em um restore de backup ou na recriação do projeto; depois de um restore, confira se o lacre continua lá (`select shobj_description(oid, 'pg_database') from pg_database where datname = current_database();`).

### Como desfazer o lacre (só se precisar limpar de novo antes do lançamento)
Desfazer o lacre é uma **decisão consciente do dono do banco**: sem ele, a limpeza volta a poder apagar dados. Só faça se ainda **não houver nenhum dado real**. No SQL Editor rode:

```
comment on database postgres is 'default administrative connection database';
```

Depois disso a limpeza volta a funcionar (comece de novo pelo ensaio) e o lacre pode ser regravado no dia do lançamento.

## Se der erro
- A limpeza é uma transação: ou apaga tudo, ou nada. Qualquer erro desfaz tudo. Copie a mensagem inteira e envie para quem mantém o sistema.
- `RECUSADO ... LACRADO` (LD002): o banco está lacrado, não rode.
- `RECUSADO ... v_confirmacao` (LD003): a frase não está exata (maiúsculas, espaços, ref).
- `RECUSADO ... v_confere_total` (LD007): o número não bate com o total atual de linhas do escopo. Refaça o ensaio e copie o número novo (`-1` nunca vale; `0` só vale se o total for 0).
- `RECUSADO ... parametro nulo` (LD003): algum campo do topo ficou `NULL`/vazio de valor; volte ao valor original.
- `RECUSADO ... comentario proprio` (LD006): o banco tem um comentário que o lacre não sobrescreve (ou é o banco de testes). Use `v_lacrar := false` e fale com quem mantém o sistema.
- `SEM PERMISSAO` / `ESTRUTURA INESPERADA` (LD004): o usuário não é dono das tabelas, faltam migrations, ou o banco tem algo que o script não conhece. Não force; envie a mensagem.
- `VERIFICACAO FALHOU` (LD005): o próprio script percebeu algo estranho (gatilhos, contagens) e desfez tudo. Envie a mensagem.
- `lock timeout`: alguém estava usando o sistema; pare o backend e tente de novo.
