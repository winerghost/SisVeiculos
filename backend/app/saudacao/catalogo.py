"""Catálogo embutido de saudações (tom leve, curto e institucional; neutro em religião e política).

Só `{nome}` é marcador. Toda ocasião tem ao menos uma frase sem marcador (para perfil sem nome).
Cada frase respeita o limite de 80 caracteres (testado).
"""

from __future__ import annotations

CATALOGO: dict[str, tuple[str, ...]] = {
    "madrugada": (
        "Acordado, coruja noturna?",
        "Madrugada de plantão, {nome}?",
        "Coruja noturna, {nome}? Bem-vindo.",
        "Silêncio lá fora e sistema aberto.",
        "Madrugada tranquila, {nome}. Bom trabalho.",
        "Quem madruga encontra o sistema à disposição.",
    ),
    "manha": (
        "Bom dia, {nome}! Café na mão e bora.",
        "Bom dia! Que a manhã seja produtiva.",
        "Bom dia, {nome}. Tudo pronto por aqui.",
        "Manhã começando bem, {nome}.",
        "Bom dia! Vamos organizar as saídas de hoje.",
        "Bom dia! O sistema já está de pé.",
    ),
    "tarde": (
        "Boa tarde, {nome}! Seguimos juntos.",
        "Boa tarde! Que o expediente renda.",
        "Boa tarde, {nome}. Tudo em ordem por aqui.",
        "Tarde chegando, {nome}. Bom trabalho.",
        "Boa tarde! Hora de acompanhar as viagens.",
        "Boa tarde! Um passo de cada vez.",
    ),
    "noite": (
        "Boa noite, {nome}! Ainda por aqui, coruja?",
        "Boa noite! Que o fim do dia seja leve.",
        "Boa noite, {nome}. Falta pouco para descansar.",
        "Noite chegando, {nome}. Bom trabalho.",
        "Boa noite! O sistema segue à disposição.",
        "Coruja noturna de plantão? Boa noite!",
    ),
    "segunda": (
        "Segunda-feira: semana nova, {nome}!",
        "Boa segunda! Que a semana comece bem.",
        "Começando a semana com o pé direito, {nome}.",
    ),
    "sexta": (
        "Sextou, {nome}! Falta pouco.",
        "Boa sexta! Último gás da semana.",
        "Sexta-feira chegou, {nome}. Bom trabalho.",
    ),
    "fim_de_semana": (
        "Bom fim de semana, {nome}!",
        "Fim de semana por aqui? Bom plantão.",
        "Sábado ou domingo, o sistema segue aberto.",
    ),
    "ano_novo": (
        "Feliz Ano Novo, {nome}!",
        "Ano novo, boas viagens pela frente.",
        "Que este ano seja de boas conquistas, {nome}.",
    ),
    "vespera_ano_novo": (
        "Véspera de Ano Novo, {nome}! Bom fechamento.",
        "Último dia do ano. Obrigado pelo trabalho!",
        "Fechando o ano com chave de ouro, {nome}.",
    ),
    "carnaval": (
        "Bom Carnaval, {nome}!",
        "Carnaval: aproveite com segurança.",
        "Dias de folia. Boa pausa, {nome}!",
    ),
    "sexta_santa": (
        "Boa Sexta-feira Santa, {nome}.",
        "Feriado de Sexta-feira Santa. Bom descanso.",
        "Um dia de pausa e reflexão, {nome}.",
    ),
    "pascoa": (
        "Feliz Páscoa, {nome}!",
        "Boa Páscoa! Um domingo tranquilo.",
        "Páscoa: tempo de renovar as energias.",
    ),
    "tiradentes": (
        "Feriado de Tiradentes, {nome}. Bom descanso.",
        "Dia de Tiradentes. Boa data a todos!",
        "Tiradentes: um dia para lembrar a história.",
    ),
    "trabalho": (
        "Feliz Dia do Trabalho, {nome}!",
        "Dia do Trabalho: obrigado por fazer a diferença.",
        "Hoje celebramos quem trabalha. Parabéns!",
    ),
    "corpus_christi": (
        "Bom feriado de Corpus Christi, {nome}.",
        "Corpus Christi: aproveite o feriado.",
        "Feriado prolongado? Bom descanso, {nome}.",
    ),
    "independencia": (
        "Feliz Dia da Independência, {nome}!",
        "7 de Setembro: boa data a todos.",
        "Dia da Independência. Bom feriado!",
    ),
    "padroeira": (
        "Bom feriado de Nossa Senhora Aparecida, {nome}.",
        "12 de outubro: feriado nacional. Bom descanso!",
        "Dia da Padroeira do Brasil. Bom feriado.",
    ),
    "finados": (
        "Dia de Finados: um dia de lembrança e respeito.",
        "Finados: um dia de memória, {nome}.",
        "Feriado de Finados. Que seja um dia sereno.",
    ),
    "republica": (
        "Feliz Proclamação da República, {nome}!",
        "15 de Novembro: boa data a todos.",
        "Dia da República. Bom feriado!",
    ),
    "consciencia_negra": (
        "Dia da Consciência Negra: respeito e igualdade.",
        "20 de Novembro: dia de reflexão, {nome}.",
        "Consciência Negra: pela igualdade de todos.",
    ),
    "dia_servidor": (
        "Feliz Dia do Servidor Público, {nome}!",
        "Dia do Servidor: obrigado por servir ao país.",
        "Parabéns, servidor! Seu trabalho conta.",
        "Hoje é o seu dia, {nome}. Parabéns!",
    ),
    "vespera_natal": (
        "Véspera de Natal, {nome}. Boas festas!",
        "Boas festas! Que a véspera seja tranquila.",
        "Final de ano chegando, {nome}. Bom descanso.",
    ),
    "natal": (
        "Feliz Natal, {nome}!",
        "Boas festas! Um Natal de paz a todos.",
        "Natal: tempo de família e descanso, {nome}.",
    ),
}
