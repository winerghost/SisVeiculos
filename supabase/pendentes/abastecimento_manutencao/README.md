# Abastecimento e manutenção — PENDENTE (versão 1.1)

Estas migrations (25 a 28) e o teste SQL ficam **fora** de `supabase/migrations/` de propósito:
o recurso ainda não tem backend nem frontend. Se estivessem em `supabase/migrations/`, o
`supabase db push` as aplicaria em produção sem nada que as use, e o job `banco` do CI
rodaria um teste que só faz sentido com as 28 migrations.

Nada aqui foi aplicado em produção. Estado: escritas e testadas num Postgres descartável
(144 testes), **ainda não revisadas pelo `revisor-seguranca`**.

## Conteúdo

- `migrations/20261107122500_abastecimento_manutencao_tabelas.sql`: tabelas imutáveis, anulações, RLS.
- `migrations/20261107122600_abastecimento_manutencao_regras.sql`: views vigentes, gatilhos, odômetro (AG015/AG016).
- `migrations/20261107122700_abastecimento_manutencao_auditoria_e_rpc.sql`: auditoria (recria os CHECKs com todos os valores) e RPCs.
- `migrations/20261107122800_revisao_alerta_km.sql`: regra e alerta de revisão por km.
- `tests/test_abastecimento_manutencao.py`: testes SQL.

A documentação do desenho está em `docs/deploy-banco.md` (seção "Abastecimento e manutenção").

## Como reativar (na ordem)

1. Rodar o `revisor-seguranca` nas 4 migrations e no teste; corrigir achados em **nova** migration (nunca editar).
2. Implementar o backend (rotas que leem pelas views `*_vigentes`, RPCs de registro e anulação, tradução de AG010/AG015/AG016, recusa dos endpoints com o recurso desligado) e o frontend.
3. Mover os arquivos de `migrations/` para `supabase/migrations/` e o teste para `supabase/tests/`, mantendo os timestamps (devem ser posteriores à última migration aplicada; se já houver outras, renomear o prefixo para depois da última).
4. Aplicar primeiro num projeto Supabase de teste (a 27 recria `ck_auditoria_acao` e `ck_auditoria_tabela` e toma ACCESS EXCLUSIVE brevemente em `auditoria`), depois `supabase db push` pelo pipeline.
5. Só então o admin pode ligar o recurso em Configurações (exige Quilometragem ligada).
