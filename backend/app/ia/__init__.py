"""IA opcional (OpenAI e Anthropic).

Regras deste pacote:
- A IA só redige ou resume. Nunca decide autorização, disponibilidade nem validação.
- Todo recurso tem um texto padrão determinístico: `ServicoIA.gerar` devolve None em qualquer
  falha (desligado, sem cota, circuito aberto, timeout) e o chamador usa o texto padrão.
- `AI_ENABLED=false` no ambiente vence qualquer configuração da tela.
- Chaves de API existem só no ambiente do backend: nunca no banco, no frontend, em logs ou em
  respostas da API. Prompts e respostas nunca são logados nem gravados.
- Dados pessoais (nome, e-mail, placa, trajeto) nunca entram em prompts.
"""
