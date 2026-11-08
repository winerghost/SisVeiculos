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

## Como acionar o expurgo hoje (manual)

Não há agendamento. O SQL Editor do Supabase executa como dono (`postgres`); o caminho auditável para
automação é a RPC abaixo, chamada com `service_role` (backend ou pipeline):

```sql
select public.expurgar_localizacoes_vencidas();
```

Via API REST do Supabase: `POST /rest/v1/rpc/expurgar_localizacoes_vencidas` com a chave `service_role`
(somente em ambiente servidor; nunca no front).

## O que falta para agendar

- A extensão `pg_cron` está disponível no projeto, mas **não instalada** e nenhuma migration a habilita.
  Opções: (a) habilitar `pg_cron` no painel e agendar, por exemplo mensalmente,
  `select cron.schedule('expurgar-localizacoes', '23 3 1 * *', 'select public.expurgar_localizacoes_vencidas()');`
  ou (b) job no pipeline/CI chamando a RPC com `service_role`.
- Definir quem monitora a linha de auditoria `localizacao_expurgada` (falha de agendamento não gera alerta).
- Ratificar a base legal e o prazo com a AGU.
