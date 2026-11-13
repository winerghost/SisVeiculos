# Plano: hora de saída da Nova requisição (não pode ser passado + valor inicial = agora)

Status: **implementado em 13/11/2026, não commitado** (decisões fechadas abaixo; mockups 03 e 11 ainda precisam ser recapturados). Inclui também a cor da data e hora (seção 10).
Referências: `CLAUDE.md` (regra de ouro, requisição, PWA), `docs/mockups/README.md`, mockups `03-nova-requisicao.png` e `11-seletor-data-hora.png`.

## 1. Diagnóstico do que existe hoje

| Camada | Situação | Lacuna |
|---|---|---|
| Backend `services/validacao.py` (`_data_hora`) | Exige texto ISO 8601 **com fuso**, converte para UTC e só recusa anos fora de 2000–2100. | **Nada impede data/hora passada.** Hoje é possível criar requisição para ontem. |
| Backend `services/requisicoes.py` (`criar`) | Chama `validar_criacao`, checa veículo ativo/ocupado e insere. Não existe relógio injetável (usa `datetime.now(UTC)` direto em outros pontos). | Sem ponto único e testável de "agora". |
| Fuso | `services/dashboards.py` define `FUSO_NOME = "America/Sao_Paulo"` e `FUSO = timezone(timedelta(hours=-3))` (fixo, correto desde 2019, sem horário de verão). O front tem `OFFSET = '-03:00'` em `lib/formato.ts`. | O fuso já está documentado e centralizado; só falta reutilizá-lo. |
| Banco (`20261107120200_requisicoes.sql`) | `hora_saida timestamptz not null`, **sem CHECK/trigger** de data passada. O próprio cabeçalho da migration diz "nenhum CHECK depende de now()". | Ver seção 3 (decisão: não criar CHECK). |
| Testes pytest | `test_requisicoes.py` usa `hora_saida: "2026-11-10T08:30:00-03:00"` fixo e só testa formato (texto livre, sem fuso). Há ~43 ocorrências de datas de 2026 em 18 arquivos de teste. | Nenhum teste de passado/limite/fuso. **Datas fixas de 2026-11-10 já estão no passado** e quebrarão assim que a regra entrar (ver seção 5). |
| Frontend `app/requisicoes/nova/page.tsx` | `dia` começa vazio (botão mostra "Escolha o dia e o horário"). Ao abrir, `rDia = dia ?? new Date()` (relógio do **celular**), mas `hora=8` e `minuto=0` são fixos. O `Calendar` não desabilita nada. | O padrão de hora é 08:00 mesmo às 15h (já nasce no passado). Mês e dia dependem do relógio/fuso do aparelho. |
| Playwright | `fluxo.spec.ts` e `capturas/solicitante.cap.ts` clicam no dia **28** do mês corrente, hora 14:30. | Quebra se o teste rodar depois do dia 28 ou com a regra nova e relógio mockado. |

## 2. Regra de negócio (backend, `services/`)

**Regra:** `hora_saida` (convertida para UTC) deve ser `>= agora_servidor - TOLERANCIA`, com `TOLERANCIA = 10 minutos`.

Justificativa da tolerância:
- O seletor trabalha em minutos cheios (segundos sempre `:00`). Quem escolhe "agora" (minuto corrente) envia, no melhor caso, até 59 s no passado, e leva mais alguns minutos preenchendo trajeto e observação.
- Sem tolerância, o caso mais comum ("saio agora") falharia por segundos. Dez minutos cobrem isso sem permitir requisição de fato retroativa.
- É uma constante nomeada em um só lugar (`TOLERANCIA_PASSADO`), fácil de ajustar. **Decidido: 10 minutos.**

Detalhes:
- Relógio do **servidor** (UTC, host com NTP). O fuso não entra na comparação (instantes em UTC); ele só aparece na **mensagem** e na **sugestão** (Brasília, `FUSO` já existente, importado de `services/dashboards.py`; não criar um segundo ponto de definição).
- Onde: nova função `validar_hora_saida_nao_passada(em_utc, agora)` em `services/validacao.py`, chamada por `ServicoRequisicoes.criar` logo após `validar_criacao` e **antes** de qualquer acesso ao banco (barata e sem efeito colateral). O `agora` vem de um relógio injetável no serviço (`relogio: Callable[[], datetime] = lambda: datetime.now(UTC)`, mesmo padrão de `dashboards`/`relatorios`), para testar sem `freezegun`.
- Só na **criação**. Autorizar, registrar saída e chegada **não** revalidam `hora_saida` (a fila pode autorizar depois do horário previsto; `hora_saida` é previsão).
- Erro: `RequisicaoInvalida` (400) com `codigo="hora_saida_passada"` (já suportado pelo construtor de `ErroApi`; opcional criar subclasse `HoraSaidaPassada` em `errors.py`). Formato `{"erro": {"codigo", "mensagem"}}`. Mensagem sugerida:
  > "A hora de saída não pode ser anterior ao horário atual. Agora são 13/11/2026 às 14:32 (horário de Brasília). Escolha um horário a partir das 14:22."
- Ordem das validações: formato → passado → veículo existe/ativo → veículo ocupado → insert (o índice único continua sendo a decisão final de ocupação).
- Esta regra **não** é "antecedência mínima" (que o `CLAUDE.md` exclui na v1): aceita saída imediata. Mesmo assim o `CLAUDE.md` deve ganhar uma linha (seção 7).

## 3. Banco: não criar CHECK nem trigger (recomendação)

- `CHECK (hora_saida >= now())` é inválido na prática: `now()` não é imutável, e o CHECK seria reavaliado em UPDATEs (autorizar, saída, chegada) e em restore/seed, quebrando justamente a fila descrita acima.
- Trigger `BEFORE INSERT` seria viável (só insert), mas: (a) só o Flask com `service_role` escreve; (b) as fixtures `supabase/tests` inserem datas livres; (c) o relógio do banco e o do Flask divergiriam em testes. Custo maior que o ganho na v1.
- Defesa em profundidade fica como **opcional futuro** (trigger só em INSERT, com a mesma tolerância), se a AGU exigir.
- **Sem migration.** Registros antigos com `hora_saida` passada permanecem válidos (histórico).

## 4. Valor inicial do seletor: alternativas e recomendação

Regra de ouro: o front não calcula regra; mas pode **exibir o que a API informa**.

| Alternativa | Como funciona | Prós | Contras |
|---|---|---|---|
| A. Relógio do cliente (`new Date()`) só como sugestão | Front preenche com a hora do celular; servidor valida no envio. | Zero backend, zero latência. | Relógio/fuso errado do celular gera sugestão errada (ex.: aparelho em outro fuso perto da meia-noite mostra o dia errado, e `montarHoraSaida` usa `OFFSET` fixo -03:00). É o comportamento parcial de hoje. |
| B. **Endpoint de referência (recomendada)** | `GET /api/requisicoes/horario-referencia` devolve o "agora" do servidor em Brasília, já decomposto, e a sugestão arredondada. | Fonte única (servidor), imune a celular errado, sem aritmética de fuso no cliente. | 1 rota nova e 1 chamada ao abrir o seletor. |
| C. Embutir `agora` em payload existente (`/api/me` ou `/api/veiculos`) | Sem rota nova. | Sem chamada extra. | Valor envelhece (sessão longa); mistura responsabilidades. |
| D. API devolve grade de horários válidos por dia | Front só renderiza permitido/negado. | Máxima fidelidade. | Payload e complexidade desproporcionais para a v1. |

**Decidido: B**, com fallback para A apenas como sugestão (se a chamada falhar, abre com o relógio do aparelho, também arredondado; o servidor segue sendo quem recusa).

Contrato proposto (rota autenticada, qualquer perfil que possa criar requisição; `Cache-Control: no-store`, pois o service worker é network-first para API e o valor não pode vir de cache):

```json
{
  "fuso": "America/Sao_Paulo",
  "agora": "2026-11-13T14:32:07-03:00",
  "sugestao": { "data": "2026-11-13", "hora": 14, "minuto": 35 }
}
```

- `data/hora/minuto` já decompostos evitam o cliente converter ISO com `new Date(iso)` (que aplicaria o fuso do aparelho e deslocaria o dia). O front monta o `Date` do calendário pelos componentes (`new Date(ano, mes-1, dia)`), sem aritmética de fuso.
- **Decidido:** `sugestao` = agora **arredondado para cima ao próximo múltiplo de 5 min** (14:32:07 → 14:35; 14:35:00 → 14:35). Sempre válida e próxima de "agora". O arredondamento é feito no servidor; a virada de hora e de dia (23:58 → 00:00 do dia seguinte) também. O limite mínimo aceito (`agora - 10 min`) não é enviado: aparece só na mensagem de erro, calculado no servidor.

### Dias/horas passados: só validar no envio (decidido)

O calendário **não** desabilita dias nem horas passados. Quem escolher um horário passado recebe a mensagem clara do servidor ao enviar. É a opção mais simples e a mais fiel à regra de ouro (nenhuma regra no cliente). Alternativas descartadas: desabilitar dias anteriores a um limite vindo da API, e desabilitar também horas/minutos do dia atual (duplicaria regra no cliente).

Comportamento ao abrir: se já houver valor confirmado, **mantém** (não sobrescreve); se vazio, preenche o rascunho com `sugestao`. O botão do formulário continua com o placeholder até "Confirmar" (evita enviar horário por engano).

## 5. Arquivos a mudar

**Backend**
- `backend/app/services/validacao.py`: `TOLERANCIA_PASSADO`, `validar_hora_saida_nao_passada`.
- `backend/app/services/requisicoes.py`: relógio injetável; chamada em `criar`; método `horario_referencia()` (usa `FUSO` de `services/dashboards.py`).
- `backend/app/rotas/api.py`: `GET /requisicoes/horario-referencia` (registrar **antes** de rotas com `/<id>` para não colidir; só delega ao serviço; cabeçalho `no-store`).
- `backend/app/errors.py`: opcional, `HoraSaidaPassada`.
- Verificar no app factory como o serviço é instanciado, para injetar o relógio nos testes.

**Frontend**
- `frontend/src/lib/api.ts` e `lib/tipos.ts`: `api.horarioReferencia()` e tipo da resposta.
- `frontend/src/lib/formato.ts`: helper para montar `Date` a partir de `{data}` sem fuso.
- `frontend/src/app/requisicoes/nova/page.tsx`: `abrir()` busca a referência (estado "carregando", fallback ao relógio local só como sugestão), preenche `rDia/rHora/rMinuto` e `defaultMonth` (sem desabilitar dias). Mensagem de erro continua vinda da API (`Alerta`); avaliar rolar até o alerta ao falhar o envio (UX apenas).

**Testes e docs**
- `backend/tests/test_requisicoes.py`, `backend/tests/fakes.py` (helper `hora_saida_futura()` e relógio fixo), `backend/tests/test_integracao_concorrencia.py` e demais que criam via API com data fixa.
- `frontend/e2e/mock-api.ts`, `e2e/fluxo.spec.ts`, `e2e/capturas/solicitante.cap.ts`, novo `e2e/nova-requisicao-horario.spec.ts`.
- `docs/mockups/03-nova-requisicao.png` e `11-seletor-data-hora.png` (recapturar), `docs/mockups/README.md` e `docs/plano-de-acao-mockups.md`.
- `CLAUDE.md`: uma linha (seção 7).

## 6. Testes

**pytest (backend)**
1. Passado: 1 dia antes e 1 hora antes → 400, `codigo == "hora_saida_passada"`, mensagem contém horário de Brasília; **nada inserido** (contagem do fake) e notificador não chamado.
2. Limite (relógio fixo): `agora - 10 min` exato → aceita; `agora - 10 min - 1 s` → recusa; `agora` e futuro → aceitam; segundos/microssegundos do instante enviado tratados de forma determinística.
3. Fuso: o mesmo instante em `-03:00`, `+00:00` e `-04:00` tem o mesmo resultado; `23:30-03:00` equivale a `02:30Z` do dia seguinte; relógio em `2026-11-14T01:00Z` (= 13/11 22:00 em Brasília): sugestão e mensagem usam **13/11**.
4. Sem regressão de estado: requisição já existente com `hora_saida` no passado ainda pode ser autorizada, ter saída e chegada registradas.
5. Ordem: hora passada responde 400 antes de consultar veículo ocupado (sem acesso ao repositório).
6. Endpoint de referência: 401 sem token; 200 para perfis permitidos; esquema do JSON; `Cache-Control: no-store`; arredondamento da `sugestao` (14:32:07 → 14:35; 14:35:00 → 14:35; 14:55:30 → 15:00; 23:58 → 00:00 do dia seguinte, em Brasília); a `sugestao` devolvida é sempre aceita por `criar`.
7. Concorrência (`test_integracao_concorrencia`): continua "duas simultâneas, só uma persiste", agora com `hora_saida_futura()`.
8. Correção em massa: rodar `pytest` e trocar toda data fixa de 2026-11-10 usada em `POST /requisicoes` por `hora_saida_futura()`; testes que inserem direto no fake/banco não são afetados.

**Playwright (frontend)**
1. Ao abrir o seletor, `Hora` e `Minuto` e o dia selecionado refletem a `sugestao` do mock (p. ex. 14:35), e não 08:00.
2. Valor já confirmado é preservado ao reabrir.
3. Envio com horário no passado: mock devolve 400 `hora_saida_passada` e o `Alerta` "Não foi possível enviar" mostra a mensagem do servidor, sem regra no cliente.
4. Falha da chamada de referência: seletor abre com fallback do relógio local (arredondado) e o envio segue sendo validado pelo servidor.
5. Nenhum dia do calendário fica desabilitado.
6. `fluxo.spec.ts`: trocar o clique fixo no dia 28 por um dia derivado da `sugestao` do mock (hoje ou o seguinte), evitando fragilidade por data.

## 7. Riscos

| Risco | Mitigação |
|---|---|
| Fuso `America/Sao_Paulo` (sem horário de verão desde 2019; pode voltar) | Um só ponto (`FUSO` em `services/dashboards.py`) no backend; no front, `OFFSET` em `lib/formato.ts`. Se o horário de verão voltar, trocar o offset fixo por `ZoneInfo` e devolver o offset na resposta de referência. |
| Relógio errado ou fuso diferente no celular | Servidor é a fonte do "agora" e da validação; sugestão e mês vêm da API; fallback local só sugere. |
| Relógio do servidor/VPS | Host com NTP (Docker herda o relógio do host); comparação em UTC. |
| Quem passa os minutos preenchendo o formulário | Tolerância de 10 min e mensagem que informa o horário mínimo aceitável. |
| Service worker network-first e cache | `Cache-Control: no-store` e a rota não entra no cache offline; sem dado autenticado persistente. |
| Testes com data fixa quebrando | Helper `hora_saida_futura()` e relógio injetável; e2e sem dia fixo. |
| Conflito com o `CLAUDE.md` ("sem antecedência mínima") | A regra aceita saída imediata (não é antecedência); registrar a regra no `CLAUDE.md` com aprovação do usuário. |
| Vazamento/LGPD | A rota devolve apenas horário do servidor; nenhum dado pessoal. |

Linha sugerida para o `CLAUDE.md` (seção Requisição): "`hora_saida` não pode ser anterior ao horário atual do servidor (tolerância de 10 min, fuso America/Sao_Paulo); só vale na criação; continua sem antecedência mínima nem prazo máximo."

## 8. Decisões pendentes (padrão recomendado entre parênteses)

1. Tolerância de passado: **10 min (decidido pelo usuário)**.
2. Sugestão inicial: **arredondada para cima ao próximo múltiplo de 5 min (decidido)**.
3. Dias passados no calendário: **não desabilitar; só validar no envio (decidido)**.
4. Rota nova `GET /api/requisicoes/horario-referencia`: **autorizada**.
5. Linha nova no `CLAUDE.md`: **autorizada e adicionada**.
6. Trigger de defesa em profundidade no banco: não na v1 (recomendado).

## 9. Deploy

- Mudam `backend/` e `frontend/`: no servidor, `git pull` e **`docker compose up -d --build`**.
- **Sem migration** (nenhuma alteração de schema; nada a rodar em `supabase db push`).
- Sem novas variáveis de ambiente (`.env`, `SUPABASE_URL` e `SUPABASE_ANON_KEY` inalterados).
- Antes de concluir a implementação: `ruff`, `black`, `pytest`, lint/typecheck do front e Playwright do fluxo; recapturar os mockups 03 e 11.
- Ordem sugerida: (1) backend (regra, rota, testes) → (2) front (seletor) → (3) e2e e mockups → (4) `CLAUDE.md`. Mudanças pequenas, um commit por etapa, somente quando o usuário pedir.

## 10. Cor da data e hora (pedido adicional, implementado)

Regra decidida no backend (`services/horario_saida.py`, `situacao_do_horario`), devolvida em toda requisição como `situacao_horario`:

| Situação | Quando | Cor |
|---|---|---|
| `no_prazo` | requisição aberta (solicitada ou autorizada), faltando mais de 10 min | verde (`#D9F2E6` / texto `#0A5A3A`) |
| `proximo` | faltam 10 min ou menos para o horário previsto (inclusive no próprio horário) | amarelo (`#FFF1D6` / texto `#7A4A00`) |
| `atrasado` | passou do horário previsto e a saída ainda não foi registrada | vermelho (`#FDE2E0` / texto `#8A1F17`) |
| `null` | em uso, concluída, negada ou cancelada | sem destaque |

- O front só mapeia o valor para a cor (`components/ui/hora-saida.tsx`), usando os mesmos pares do chip de status (fundo claro, texto escuro, contraste acima de 4,5:1). A cor não é o único sinal: amarelo e vermelho trazem "em breve" e "vencido" em texto, e o verde traz o estado só para leitor de tela.
- Aparece na lista (cartão), no detalhe da requisição e na lista da equipe.
- A cor é calculada quando a API responde: ela muda ao recarregar ou reabrir a tela, não em tempo real na tela aberta (o front não calcula regra).
- A janela de 10 min (`JANELA_ALERTA`) é independente da tolerância de passado (`TOLERANCIA_PASSADO`), embora hoje ambas valham 10.

## 11. O que foi feito e verificado

- Backend: `app/fuso.py` (fuso em módulo folha, reexportado por `dashboards`), `services/horario_saida.py`, `criar` com relógio injetável, rota nova com `no-store`. `ruff` ok e `pytest` com 1952 testes passando.
- Testes pytest novos em `test_requisicoes.py` (passado, limite, fusos, sem regressão na autorização, sugestão, rota e cores); datas fixas de 2026-11-10 trocadas por `tests/horarios.py::hora_saida_futura()`.
- Frontend: seletor com sugestão da API (fallback do relógio do aparelho), componente `HoraSaida`, `lint` e `tsc` ok. Playwright: suíte completa passando, com `e2e/nova-requisicao-horario.spec.ts` novo.
- Pendente: recapturar `03-nova-requisicao.png` e `11-seletor-data-hora.png` e registrar no `docs/mockups/README.md`.
- Deploy: `docker compose up -d --build` (mudou `backend/` e `frontend/`); sem migration e sem variável nova.
