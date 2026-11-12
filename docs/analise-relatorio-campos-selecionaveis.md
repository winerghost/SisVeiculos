# Análise — relatório com campos selecionáveis

Pedido: escolher os campos do relatório (ex.: solicitante, unidade, hora da saída, trajeto, hora da chegada, observação).

## Viabilidade: SIM, esforço pequeno a médio
- O catálogo de colunas de Relatórios já existe (`COLUNAS` em `services/relatorios.py`); a tela monta filtros/colunas a partir dele. Falta o cliente **escolher** colunas.
- Proposta (backend manda): parâmetro `colunas` (lista de chaves) validado por lista branca e cruzado com as flags (`incluir_nomes`, KM etc.); `catalogo()` passa a expor `colunas_disponiveis` (chave, rótulo, exige_flag). Seleção só em `movimentacao`, `pendencias` e `opcionais_km`; resumo/tempos/auditoria mantêm colunas fixas.
- Prévia, arquivo e e-mail usam a mesma seleção. Resumo IA ignora `colunas` (só vê contagens).
- Frontend: checkboxes com ordem e botão "restaurar padrão"; nada de regra no cliente.
- Banco: nenhum índice novo. **Observação** não está em `rel_movimentacao`: exige migration.

## Riscos (LGPD)
- Observação é texto livre e pode conter dado pessoal. Recomendado: flag `incluir_observacao`, desligada por padrão, fora da prévia e do resumo IA, e sem envio ao n8n/IA sem aprovação.

## Decisões pendentes (do usuário)
1. Incluir a observação (com migration + flag de LGPD) ou deixá-la de fora?
2. Coluna inválida: responder 400 ou ativar a flag `incluir_*` automaticamente? (recomendado: 400)
3. Presets salvos de colunas (API) ou só o padrão?
4. "Formulário gerado" = o relatório, ou a própria requisição? (a segunda é outro escopo)
