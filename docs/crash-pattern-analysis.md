# Crash: frequências maiores, não lucro demonstrado

Análise offline realizada em 28/08/2026, sem enviar apostas ou alterar o histórico.

## Fonte e método

- Fonte local: `data/crash_history_30d.csv`, sala 4, 101.173 rodadas concluídas.
- Cobertura observada: 27/07/2026 21:56:04.586 até 26/08/2026 21:54:25.562 UTC.
- SHA-256: `91cfb1c67c546ab2461832486d274dfe023d8e74def8c07f6d603e4a89e1ad41`.
- Registros ordenados por horário; IDs duplicados, horários ambíguos e valores
  inválidos cancelam a análise. Os **6.801 resultados 0x foram preservados**.
- Maior intervalo entre registros: 237,366 segundos. Não há intervalo acima de
  300 segundos, mas isso não prova que todas as rodadas foram capturadas.
- Faixas idênticas às do bot: B < 2x; 2x ≤ M < 5x; A ≥ 5x.
- Entrada ideal na rodada seguinte ao padrão completo. Nunca se usa o resultado
  da própria rodada de entrada para reconhecer o gatilho.
- Stake fixa de 1 unidade: ganho líquido = retirada − 1; perda = −1.
  Acerto exatamente na retirada conta como ganho. Sem martingale.

Foram fixadas 32 combinações: B, BB, BBB, BBBB, BM, BBM, BBBM e BBBBM, cada uma
com retirada em 1,20x, 1,30x, 1,50x e 2,00x. Referências separadas: o antigo
MABBM/5x e entradas incondicionais nas cinco retiradas. Não são buscas exaustivas
de todas as sequências possíveis.

As datas distintas foram divididas aproximadamente em 60%/20%/20%, sem embaralhar:

| Trecho | Início UTC | Fim UTC (exclusivo) |
|---|---|---|
| Treino | 27/07 21:56:04.586 | 14/08 00:00 |
| Validação | 14/08 00:00 | 20/08 00:00 |
| Teste final | 20/08 00:00 | imediatamente após a última rodada de 26/08 |

O contexto anterior à divisão pode confirmar um padrão; a entrada só é contada
no trecho do seu resultado. O critério de seleção foi maior ROI no treino,
exigindo ao menos 500 oportunidades no treino. O escolhido foi BBBBM/1,50x,
**mesmo tendo ROI negativo**: −5,87% no treino, −2,88% na validação, −6,89% no teste.
“Melhor entre os candidatos” não significa aprovado para dinheiro real.

## Resultados

Sinais/dia abaixo são a média de oportunidades no período inteiro, sem limites
operacionais; acertos e ROI são exclusivamente do trecho final (20–26/08 UTC).

| Padrão | Retirada | Sinais/dia | Entradas no teste | Acertos no teste | ROI no teste |
|---|---:|---:|---:|---:|---:|
| BB | 1,50x | 974,7 | 6.738 | 61,62% | −7,57% |
| BBBB | 1,50x | 283,4 | 1.956 | 62,07% | −6,90% |
| BBBBM | 1,50x | 77,0 | 530 | 62,08% | −6,89% |
| MABBM (referência antiga) | 5,00x | 13,8 | 85 | 30,59% | +52,94% |

Todas as **32 combinações frequentes** perderam no trecho final. BBBBM/2x teve
um resultado positivo isolado na validação (+1,55%), mas negativo no treino
(−6,25%) e teste (−10,57%): não foi selecionado após olhar esse resultado.

No BBBBM/1,50x, as 530 entradas finais deram 329 ganhos de 0,50 e 201 perdas de
1,00: **−36,50 unidades**, com queda máxima desde um pico de 40,50 unidades e
até seis perdas consecutivas. A 1,50x, duas vitórias apenas recuperam uma perda;
é preciso acertar mais de 66,67% para lucro. Em todo o período, esse padrão deu
2.310 sinais, 1.455 ganhos e 855 perdas: −127,50 unidades (ROI −5,52%).

MABBM apareceu positivo, mas **já havia sido escolhido explorando este mesmo
histórico**. Seu resultado não é validação independente de vantagem futura.
O total foi de apenas 414 sinais e houve até 19 perdas consecutivas. Manter o
default por compatibilidade não significa que 5x seja comprovadamente melhor.

## Limitações e aplicação no bot

Esta é uma divisão retrospectiva de um arquivo já explorado. Não é um teste
prospectivo. Fazer muitas comparações aumenta a chance de achar um vencedor
aparente; não foi demonstrada significância estatística ou causalidade.
[NIST: múltiplas comparações](https://www.itl.nist.gov/div898/handbook/prc/section4/prc47.htm).

Resultados anteriores, por si sós, não tornam uma recuperação obrigatória em
um jogo de resultados independentes. A descrição geral de jogos aleatórios
da [Gambling Commission](https://www.gamblingcommission.gov.uk/public-and-players/guide/return-to-player-how-much-gaming-machines-payout)
explica essa distinção; ela não é uma auditoria específica da Blaze.

O cálculo não inclui indisponibilidade de rede, atraso, recusa, efeito de bônus
ou arredondamento de saldo. Se o histórico omitir uma rodada, duas observações
consecutivas podem não ser rodadas consecutivas reais. Frequência não é promessa
de execução. Há no máximo uma entrada por rodada; padrões podem compartilhar
resultados anteriores.

O JSON também contém `limited_illustration`: stake 1, limite de 20 entradas,
stop-loss 5 e stop-gain 5 por dia UTC, comparados ao saldo realizado antes de
entrar, como no Crash atual. A última aposta pode ultrapassar o stop-loss. Esse
cenário não simula falhas de rede nem substitui o acompanhamento em paper.

Foi adicionado apenas um **preset experimental opcional**, `baixas-media`:
BBBBM, retirada sugerida 1,50x, sem mudança no default MABBM/5x ou nos limites.
Os comandos de teste estão no README. Não há recomendação de ativá-lo com
dinheiro real: os dados analisados mostraram prejuízo.

## Reproduzir

Na pasta do projeto, com o ambiente instalado:

```powershell
python -m blaze_auto.crash_analysis --input data/crash_history_30d.csv
```

Saída: `data/crash_analysis/report.json`, com todas as combinações, divisões,
taxas, lucros, quedas máximas e sequências de perdas. O script reaproveita
`point_label` e `calculate_profit` usados pelo bot e não precisa do `.env`.
