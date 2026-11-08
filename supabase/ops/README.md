# supabase/ops: ferramentas operacionais (NÃO são migrations)

Esta pasta **não** é lida pelo `supabase db push`. Os arquivos são colados à mão no SQL Editor do Supabase (papel `postgres`).

| Arquivo | Para quê |
|---|---|
| `limpar_dados_de_teste.sql` | Apaga **uma vez** os dados de teste (requisições, checklists, fotos de perfil, acessos de login e auditoria) antes do uso real. Por padrão é um **ensaio** que não apaga nada. |
| `lacrar_producao.sql` | Grava o lacre (`comment on database ... 'sisveiculos:lacrado'`). Com o lacre, a limpeza se recusa a rodar. |

Passo a passo para quem não é técnico: [`docs/limpeza-pre-lancamento.md`](../../docs/limpeza-pre-lancamento.md).

Resumo:
1. Faça **backup** (Database > Backups só tem backup diário em planos pagos; sem backup faça um pg_dump/export antes: sem backup "não há desfazer" vale de verdade) e deixe o sistema **parado** (`docker compose stop backend` na VPS, ou fora de horário). O script e o ensaio travam as tabelas; 15 s de espera e desfaz com segurança.
2. Rode `limpar_dados_de_teste.sql` sem mudar nada: aparece o erro `ENSAIO: nada foi apagado` com as contagens e o número a copiar. Copie/fotografe a mensagem.
3. Para apagar, mude `v_executar := true`, `v_confirmacao := 'APAGAR DADOS DE TESTE ofwvoztarroglekfwohs'` e `v_confere_total := <número do ensaio>` (total das 6 tabelas do escopo). Depois apague o snippet salvo no Studio e **religue o backend logo em seguida** (`docker compose start backend`): desativar contas e logar exigem o backend no ar; ele fica parado só durante ensaio e execução. Parâmetro `NULL` é recusado.
4. Esvazie os buckets `avatares` e `checklists` no painel (o SQL não apaga arquivos; até lá ficam arquivos com dado pessoal) e **desative** (não remova) os usuários de teste no sistema.
5. No dia do lançamento, rode `lacrar_producao.sql`.

Parâmetros do topo de `limpar_dados_de_teste.sql`: `v_executar`, `v_confirmacao`, `v_confere_total`, `v_ref_esperado`, `v_apagar_auditoria`, `v_lacrar`.

Mantém: `perfis`, `veiculos`, `configuracoes`, `convites`, `auth.*`, `storage.*`. Não toca em `auth.sessions` / `auth.audit_log_entries` (IP, navegador, e-mail: LGPD) nem em outros buckets de Storage; confira se existe outro bucket além dos dois. Na execução real fica 1 linha em `auditoria` (o registro da limpeza); com `v_apagar_auditoria := true` somem também os eventos `admin_semeado`/`perfil_criado` dos perfis reais (use `false` para preservá-los).

Grave o **lacre** assim que houver uso real. Exceção única pré-lançamento ao "sem DELETE físico / auditoria append-only": dados fictícios, gatilhos religados e verificados.

**Lacre:** substitui o comentário padrão do banco (`default administrative connection database`) ou um comentário vazio; qualquer outro comentário é preservado (o lacre recusa). Limites: protege contra erro e esquecimento, **não contra má-fé** (o dono desfaz com um `COMMENT`: `comment on database postgres is 'default administrative connection database';`, decisão consciente do dono), e o comentário pode se perder em restore de backup ou recriação do projeto.

**Avisos:** NUNCA rode depois de haver uso real. O banco não conhece o ref do projeto, então a defesa é a frase de confirmação (que inclui o ref: confira o projeto no navegador) mais o total de linhas do escopo copiado do ensaio. Testes: `supabase/tests/test_ops_limpeza.py` (só banco local descartável).
