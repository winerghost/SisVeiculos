# supabase/ops: ferramentas operacionais (NÃO são migrations)

Esta pasta **não** é lida pelo `supabase db push`. Os arquivos são colados à mão no SQL Editor do Supabase (papel `postgres`).

| Arquivo | Para quê |
|---|---|
| `limpar_dados_de_teste.sql` | Apaga **uma vez** os dados de teste (requisições, checklists, notificações, localizações de viagem, envios de relatório por e-mail, uso de IA, fotos de perfil, acessos de login e auditoria: 10 tabelas) antes do uso real. Por padrão é um **ensaio** que não apaga nada. **Exige antes o `db push` da migration `20261114120000`** (o script recusa sem ela). |
| `lacrar_producao.sql` | Grava o lacre (`comment on database ... 'sisveiculos:lacrado'`). Com o lacre, a limpeza se recusa a rodar. |

Passo a passo para quem não é técnico: [`docs/limpeza-pre-lancamento.md`](../../docs/limpeza-pre-lancamento.md).

Resumo:
0. **Antes de tudo:** aplique as migrations pendentes pelo pipeline (`supabase db push`), em especial `20261114120000_localizacao_retencao_12_meses.sql`. Ela troca o gatilho de `localizacoes_viagem` e acrescenta `localizacao_expurgada` à auditoria; sem ela o script se recusa (`ESTRUTURA INESPERADA`, LD004). Não rode a limpeza antes do push.
1. Faça **backup** (Database > Backups só tem backup diário em planos pagos; sem backup faça um pg_dump/export antes: sem backup "não há desfazer" vale de verdade) e deixe o sistema **parado** (`docker compose stop backend` na VPS, ou fora de horário). O script e o ensaio travam as tabelas; 15 s de espera e desfaz com segurança.
2. Rode `limpar_dados_de_teste.sql` sem mudar nada: aparece o erro `ENSAIO: nada foi apagado` com as contagens e o número a copiar. Copie/fotografe a mensagem.
3. Para apagar, mude `v_executar := true`, `v_confirmacao := 'APAGAR DADOS DE TESTE ofwvoztarroglekfwohs'` e `v_confere_total := <número do ensaio>` (total das 10 tabelas do escopo). Depois apague o snippet salvo no Studio e **religue o backend logo em seguida** (`docker compose start backend`): desativar contas e logar exigem o backend no ar; ele fica parado só durante ensaio e execução. Parâmetro `NULL` é recusado.
4. Esvazie os buckets `avatares` e `checklists` no painel (o SQL não apaga arquivos; até lá ficam arquivos com dado pessoal) e **desative** (não remova) os usuários de teste no sistema.
5. No dia do lançamento, rode `lacrar_producao.sql`.

Parâmetros do topo de `limpar_dados_de_teste.sql`: `v_executar`, `v_confirmacao`, `v_confere_total`, `v_ref_esperado`, `v_apagar_auditoria`, `v_lacrar`.

**Apaga (filhas primeiro):** `checklist_fotos`, `checklists`, `notificacoes`, `localizacoes_viagem`, `requisicoes`, `perfil_fotos`, `acessos_login`, `envios_email`, `ia_uso` e `auditoria` (esta só com `v_apagar_auditoria := true`). Todas são dado operacional gerado pelos testes (o de `localizacoes_viagem` é sensível: coordenadas).

**Mantém:** `perfis`, `veiculos`, `configuracoes`, `ia_configuracoes` (cadastro e configuração), `convites`, `auth.*`, `storage.*`. As únicas tabelas do schema `public` são essas 15; não existem outras (dashboards são só funções).

**Como o gatilho é tratado:** `notificacoes` e `localizacoes_viagem` só aceitam DELETE no sistema com marcador do expurgo **e** piso de idade (30 dias / 12 meses); dado de teste é recente, então o marcador não serve. O script desliga o gatilho **nomeado** de cada tabela (`trg_notificacoes_proteger`, `trg_localizacoes_viagem_imutavel`, como nas outras) e o religa **no mesmo modo de antes** (`ENABLE ALWAYS`; `trg_envios_email_sem_delete` e `trg_ia_uso_imutavel` hoje estão em `ENABLE` padrão e voltam assim). Os gatilhos de TRUNCATE não são tocados e, ao fim, o estado de todos os gatilhos de `public` é comparado com o de antes.

Não toca em `auth.sessions` / `auth.audit_log_entries` (IP, navegador, e-mail: LGPD) nem em outros buckets de Storage; as migrations criam só esses dois buckets (`avatares` e `checklists`; não há bucket de staging), mas confira no painel se existe outro. O conteúdo de `localizacoes_viagem`, `notificacoes`, `envios_email` e `ia_uso` é apagado pelo SQL; o Auth (usuários, sessões, logs) segue fora do escopo. Na execução real fica 1 linha em `auditoria` (o registro da limpeza); com `v_apagar_auditoria := true` somem também os eventos `admin_semeado`/`perfil_criado` dos perfis reais (use `false` para preservá-los).

Grave o **lacre** assim que houver uso real. Exceção única pré-lançamento ao "sem DELETE físico / auditoria append-only": dados fictícios, gatilhos religados e verificados.

**Lacre:** substitui o comentário padrão do banco (`default administrative connection database`) ou um comentário vazio; qualquer outro comentário é preservado (o lacre recusa). Limites: protege contra erro e esquecimento, **não contra má-fé** (o dono desfaz com um `COMMENT`: `comment on database postgres is 'default administrative connection database';`, decisão consciente do dono), e o comentário pode se perder em restore de backup ou recriação do projeto.

**Avisos:** NUNCA rode depois de haver uso real. O banco não conhece o ref do projeto, então a defesa é a frase de confirmação (que inclui o ref: confira o projeto no navegador) mais o total de linhas do escopo copiado do ensaio. Testes: `supabase/tests/test_ops_limpeza.py` (só banco local descartável).
