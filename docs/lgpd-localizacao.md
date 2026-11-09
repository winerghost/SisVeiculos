# LGPD: coordenadas de saída e chegada (`localizacoes_viagem`)

## Política de retenção

- Latitude, longitude e precisão da saída/chegada registradas pelo motorista (recurso opcional
  "localização") ficam guardadas por **12 meses** (contados de `registrado_em`) e depois são **apagadas
  definitivamente**.
- Só o `admin` consulta essas coordenadas (via backend), e cada leitura gera auditoria
  (`localizacao_lida`). A auditoria nunca contém coordenadas.
- **Base legal: a confirmar com a AGU** (encarregado/DPO e política de segurança) antes de produção. O prazo
  de 12 meses foi decisão do usuário do projeto e deve ser ratificado junto à base legal.
- Desligar o recurso não apaga dados; o expurgo por prazo vale independentemente do recurso estar ligado.

## Como funciona (migration `20261114120000_localizacao_retencao_12_meses.sql`)

- A tabela continua append-only: UPDATE, TRUNCATE e DELETE direto são recusados (`AG003`), inclusive para
  `service_role`, que só tem SELECT.
- A única exceção é `public.expurgar_localizacoes_vencidas()`: `SECURITY DEFINER`, `search_path` vazio,
  `EXECUTE` apenas para `service_role`. Ela liga um marcador transacional restrito ao expurgo, apaga as
  linhas com mais de 12 meses (o gatilho ainda reconfere o piso de 12 meses linha a linha) e desliga o
  marcador.
- Cada execução (mesmo com 0 linhas) grava uma linha em `auditoria` (`acao = 'localizacao_expurgada'`,
  tabela `requisicoes`, sem registro nem ator) com `detalhes = {recurso, apagadas, corte}`. Retorna a
  contagem apagada.

## Agendamento (migration `20261116120100_agenda_expurgo_localizacoes.sql`)

- A migration agenda o job `expurgar-localizacoes` no `pg_cron`: **dia 1 de cada mês, 03:23 UTC** (00:23 em
  Brasília), comando `select public.expurgar_localizacoes_vencidas()`. O job roda com o papel que agendou
  (`postgres`, dono da função `SECURITY DEFINER` com `search_path` vazio); ninguém da API (`anon`,
  `authenticated`) executa a função.
- É condicional: se a extensão estiver instalada, agenda; se estiver só disponível, tenta instalar; se não der,
  apenas emite um aviso (NOTICE) e a migration segue sem falhar. É idempotente (reaplicar atualiza o job).
- Cada execução, mesmo com 0 linhas, grava uma linha de auditoria `localizacao_expurgada` (só contagem e corte).

### O que o admin precisa fazer (ação manual no painel)

1. Painel Supabase > Database > Extensions > habilitar **pg_cron** (se a migration não conseguiu).
2. Se a extensão foi habilitada **depois** do `db push`, rode no SQL Editor:

```sql
select cron.schedule('expurgar-localizacoes', '23 3 1 * *', 'select public.expurgar_localizacoes_vencidas()');
```

3. Conferir: `select jobname, schedule, active from cron.job;` e, depois da primeira execução,
   `select * from cron.job_run_details order by start_time desc limit 5;`.
4. Monitoramento: a ausência da linha mensal `localizacao_expurgada` em `auditoria` indica job parado (ainda
   não há alerta automático; definir o responsável).
5. Alternativa sem `pg_cron`: job no pipeline chamando a RPC com `service_role` (abaixo).

## Como acionar o expurgo manualmente

O SQL Editor do Supabase executa como dono (`postgres`); o caminho auditável para automação externa é a RPC
abaixo, chamada com `service_role` (backend ou pipeline):

```sql
select public.expurgar_localizacoes_vencidas();
```

Via API REST do Supabase: `POST /rest/v1/rpc/expurgar_localizacoes_vencidas` com a chave `service_role`
(somente em ambiente servidor; nunca no front).

## Pendências (decisão da AGU, fora do código)

- **Base legal e prazo**: ratificar com o encarregado/DPO da AGU a base legal (LGPD art. 7º/11) e confirmar o
  prazo de 12 meses. Enquanto isso não ocorrer, o recurso de localização não deve ir para produção.
- Definir quem monitora a linha de auditoria `localizacao_expurgada`.
