# Plano de ação — Perfis Motorista (revisado) e Vigilante

Em conflito com o `CLAUDE.md`, vale o `CLAUDE.md`. Regra de ouro: o backend manda; o frontend só exibe o que `acoes_permitidas` e as flags da API informarem.

## 1. Análise do estado atual
- Role `motorista` já existe (migrations 20261111120000/120100, **ainda não aplicadas**, conforme memória do projeto): recurso opcional `configuracoes.motorista`, flag `motorista_registra`, `requisicoes.motorista_id` (só a partir de `autorizada`), designação feita por autorizador/admin ao autorizar, RPC `registrar_transicao_motorista`, localização, checklist e notificações.
- O registro de saída/chegada pelo motorista já está **desligado por padrão** (`motorista_registra`). Isso já atende ao pedido.
- Não existe role `vigilante`. `requisicoes_validar` só aceita autorizador/admin/motorista como responsável por saída/chegada. Quilometragem é uma flag global (`quilometragem`).
- Relatórios (`services/relatorios.py`) não distinguem quem lançou o KM.
- Como as migrations do motorista ainda não foram aplicadas, as mudanças entram **em migrations novas** (nunca editar as existentes), conforme a memória do projeto.

## 2. Decisões fechadas (usuário)
1. **Motorista padrão**: hoje há um só motorista. Ao autorizar sem `motorista_id`, se o recurso Motorista estiver ligado e existir **exatamente um** motorista ativo, o backend o designa por padrão (a coluna só admite motorista a partir de `autorizada`, por CHECK). Com mais de um motorista ativo, o autorizador escolhe (a API informa o seletor e qual é o padrão, se houver). O motorista vê **somente** as viagens designadas a ele.
2. **Motorista**: mesma visualização do Solicitante; registrar saída/chegada segue **desligado por padrão** (`motorista_registra`); vê a **linha do tempo completa** (abertura → autorização → saída → chegada/encerramento) das suas viagens.
3. **Vigilante** (segurança patrimonial): novo role `vigilante`. Permissões do Solicitante **mais** registrar saída e chegada de **qualquer** requisição `autorizada`/`em_uso`, sempre ativo (sem flag). **Não** autoriza, nega nem cancela.
4. **KM do vigilante**: nova flag `configuracoes.vigilante_km` (desligada por padrão, exige `quilometragem` ligada). Ligada, o vigilante informa o KM de saída ao registrar a saída e o KM de chegada ao registrar a chegada. Cada alteração vai para `auditoria`.
5. **Relatórios**: mostram os lançamentos de KM feitos por vigilantes (quem lançou, KM de saída/chegada, data), com filtro "lançado por vigilante". A origem é derivada de `saida_registrada_por`/`chegada_registrada_por` + `perfis.role`; sem coluna nova.
6. **Cadastro**: o admin escolhe o perfil (Solicitante, Autorizador, Admin, Motorista, Vigilante) no convite e na edição do usuário.

## 3. Contrato entre as frentes
- Enum `role_usuario` ganha `vigilante` (migration própria, separada da que a usa — erro 55P04).
- `configuracoes` ganha `vigilante_km boolean not null default false`; CHECK `vigilante_km → quilometragem`; `configuracoes_validar` audita a nova chave; erro AG010 amigável.
- `requisicoes_validar`: ramo `vigilante` — só `autorizada→em_uso` e `em_uso→concluida`; qualquer outra transição (autorizar, negar, cancelar) ⇒ AG002; sem checagem de designação. KM conforme `vigilante_km`/`quilometragem` (a frente de banco documenta o tratamento escolhido).
- API: `acoes_permitidas` da requisição inclui `registrar_saida`/`registrar_chegada` para o vigilante (e para o motorista designado com a flag ligada); `GET /configuracoes` (visão pública das flags) informa `vigilante_km`; o seletor de motoristas informa `padrao` (id do único motorista ativo, ou nulo).
- Relatórios: campo `km_lancado_por_vigilante` (bool) e `lancado_por` (nome) nos itens; filtro `lancado_por_vigilante=true`.
- Frontend: nenhuma regra de permissão no cliente; botões vêm de `acoes_permitidas`; campos de KM só aparecem se a API informar `vigilante_km`/`quilometragem`.

## 4. Frentes, responsáveis e ordem
| # | Frente (nome do painel) | Agente | Depende de |
|---|---|---|---|
| 1 | `db-vigilante-migrations` | db-supabase | — |
| 2 | `api-vigilante-motorista` | backend-flask | contrato (§3) |
| 3 | `ui-perfis-e-km` | frontend-next | contrato (§3) |
| 4 | `revisao-seguranca` | revisor-seguranca | 1, 2, 3 |
| 5 | `testes-e-lint` | (principal) | 1–3 |
Frentes 1–3 rodam em paralelo; 4 e 5 depois.

## 5. Critérios de aceite
- pytest (permissões, IDOR, máquina de estados, concorrência) e testes SQL cobrindo vigilante e motorista padrão; ruff/black; tsc/eslint.
- Vigilante nunca autoriza/nega/cancela (API e banco). Motorista não vê viagem de outro motorista.
- Desligar `vigilante_km` não apaga dados. Sem dados pessoais em logs.

## 6. Deploy
Migrations novas ⇒ `supabase db push` (usuário roda; avisar). Mudou `backend/` e `frontend/` ⇒ servidor precisa de `docker compose up -d --build`.
