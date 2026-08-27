# Blaze Crash (`crash.tick`)

Projeto separado para monitorar e coletar resultados do Blaze Crash. Ele usa o
mesmo transporte Socket.IO/Engine.IO do projeto Double, mas assina a sala e o
evento próprios do Crash.

## O que foi confirmado no socket ao vivo

- URL: `wss://api-gaming.blaze.bet.br/replication/?EIO=3&transport=websocket`
- Sala principal: `crash_room_4`
- Sala de apostas: `crash_room_4:bets`
- Evento: `crash.tick`
- Evento de apostas: `crash.tick-bets`
- Estados: `waiting`, `graphing` e `complete`
- `crash_point` só vem preenchido quando a rodada está `complete`
- O servidor repete ticks idênticos; o coletor deduplica pelo `id` da rodada

## Requisitos

- Python 3.10 ou superior
- Git

## Instalação

Clone o repositório e entre na pasta criada. Os comandos abaixo funcionam
independentemente de onde cada pessoa guarda seus projetos:

```powershell
git clone https://github.com/FabioWendel/blaze-auto.git
cd blaze-auto
python -m venv .venv
```

No Windows/PowerShell, ative o ambiente e instale o projeto:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

No Linux/macOS:

```bash
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Todos os comandos deste README devem ser executados dentro da pasta clonada
`blaze-auto`. Nenhum caminho absoluto do computador do autor é necessário.

## Configuração obrigatória para apostas reais

O monitor, o coletor histórico e o paper trading funcionam sem credenciais. Os
comandos com `--live` exigem uma conta Blaze autenticada e um arquivo `.env`
local.

No Windows/PowerShell:

```powershell
Copy-Item .env.example .env
notepad .env
```

No Linux/macOS:

```bash
cp .env.example .env
```

Preencha o novo arquivo `.env` com os dados da própria conta:

```dotenv
BLAZE_AUTHORIZATION=Bearer SEU_TOKEN
BLAZE_WALLET_ID=ID_DA_SUA_CARTEIRA
BLAZE_USERNAME=SEU_USUARIO
BLAZE_RANK=SEU_RANK
BLAZE_ROOM_ID=4

# Opcionais, somente se existirem nos headers capturados:
BLAZE_SESSION_ID=
BLAZE_CLIENT_VERSION=
```

O valor de `BLAZE_AUTHORIZATION` pode ser copiado do header `Authorization` de
uma requisição autenticada no DevTools do navegador. Cada usuário deve usar os
dados da própria conta; os valores do autor não fazem parte do repositório.

O arquivo `.env` é carregado automaticamente e já está no `.gitignore`. Nunca
publique esse arquivo, tokens, cookies ou IDs de carteira. O arquivo
`.env.example` é apenas um modelo sem credenciais reais.

## Uso

Somente acompanhar os estados e resultados:

```powershell
python -m blaze_auto.cli
```

Acompanhar e gravar cada resultado uma única vez em CSV:

```powershell
python -m blaze_auto.cli --collect --output data/crash_rounds.csv
```

Teste curto, encerrando após uma rodada completa:

```powershell
python -m blaze_auto.cli --collect --max-rounds 1 --verbose
```

Mostrar o payload recebido a cada mudança de estado:

```powershell
python -m blaze_auto.cli --raw
```

Mostrar também os agregados da sala de apostas, sem exibir nomes ou IDs de
jogadores:

```powershell
python -m blaze_auto.cli --show-bets
```

Se a Blaze atribuir outra sala à sessão, ambas podem ser informadas:

```powershell
python -m blaze_auto.cli --room crash_room_4 --bets-room crash_room_4:bets
```

## Diferença importante em relação ao Double

No Double, o próprio tick traz cor e roll. No Crash, `crash.tick` informa o
estado da rodada e, ao final, o multiplicador em `crash_point`. Este stream não
mostrou um multiplicador crescente durante `graphing`; portanto ele é adequado
para detectar a janela `waiting` e registrar o resultado, mas não basta sozinho
para implementar cashout automático por multiplicador.

O monitor não envia apostas. O executor descrito abaixo pode enviar transações
reais somente quando `--live` é informado. Tokens, cookies e IDs de carteira
nunca devem ser gravados no Git.

## Executor de entrada e cashout

Os endpoints de entrada e cashout estão implementados, mas ficam bloqueados sem
`--live`. Configure primeiro o `.env` conforme a seção de instalação. Como
alternativa temporária, as variáveis também podem ser definidas apenas no
terminal PowerShell atual:

```powershell
$env:BLAZE_AUTHORIZATION = "Bearer SEU_TOKEN"
$env:BLAZE_WALLET_ID = "SUA_CARTEIRA"
$env:BLAZE_USERNAME = "SEU_USUARIO"
$env:BLAZE_RANK = "SEU_RANK"
$env:BLAZE_ROOM_ID = "4"
```

Entrada na próxima rodada em `waiting`, com retirada manual:

```powershell
python -m blaze_auto.bet_cli --live enter --amount 0.10
```

Entrada com retirada automática em `5.00x`:

```powershell
python -m blaze_auto.bet_cli --live enter --amount 0.10 --auto-cashout-at 5.00
```

Cashout manual de uma entrada aberta:

```powershell
python -m blaze_auto.bet_cli --live cashout
```

O executor não repete automaticamente um POST que falhou, porque uma repetição
após perda da resposta pode criar uma segunda aposta. Confira a interface da
Blaze antes de tentar novamente.

Cada transação abre e fecha uma sessão HTTP própria, sem reutilizar conexões
ociosas e sem seguir redirecionamentos. Isso reduz o risco de reutilização de
uma conexão antiga; não garante que erros de rede deixem de ocorrer.

### Se ocorrer reset de conexão, timeout ou resposta incerta

O bot automático grava `unknown` no CSV e **encerra com código 3**, sem repetir
o POST. O bloqueio continua depois de reiniciar ou virar o dia. Registros
`sending` interrompidos e `error` de versões anteriores também exigem
conferência. Um resultado público da rodada não comprova que a sua aposta foi
aceita, por isso não libera esse bloqueio automaticamente.

Confira o histórico de apostas e o saldo na sua conta. Se confirmar que a
tentativa não foi registrada, use o ID da **rodada de entrada** mostrado no log
(não o ID da aposta nem o gatilho do padrão):

```powershell
python -m blaze_auto.reconcile --round-id ID_DA_RODADA --outcome not-placed --confirmed
```

Se a aposta foi registrada e já foi liquidada, informe o resultado e o lucro
líquido efetivamente conferidos na conta. Exemplo de perda de R$ 1:

```powershell
python -m blaze_auto.reconcile --round-id ID_DA_RODADA --outcome loss --profit=-1.00 --confirmed
```

Para uma vitória, use `--outcome win --profit VALOR_LIQUIDO`. Se você usa outro
CSV, passe o mesmo `--signals CAMINHO` ao comando de conferência. O comando não
envia apostas, mantém o registro e preserva a proteção contra duplicar a rodada.
Não apague o CSV nem use outro arquivo para contornar uma entrada incerta.
Depois da conferência, inicie o bot novamente com o comando desejado.

## Bot automático por sequência

O bot acompanha `crash.tick`, classifica cada resultado como `B` (`<2x`), `M`
(`2x–4,99x`) ou `A` (`>=5x`) e arma uma entrada para a próxima rodada `waiting`
quando encontra o padrão configurado. O padrão inicial é `MABBM`, com retirada
automática em `5x`.

### Rodar somente uma entrada

Este é o modo padrão e o mais seguro para testar. O bot permanece aberto
esperando o padrão `MABBM`, faz uma entrada, aguarda o resultado dessa rodada e
encerra. Se o padrão demorar, ele continuará aguardando.

Uma entrada em paper trading, sem dinheiro real:

```powershell
python -m blaze_auto.auto_bot
```

Uma entrada real de R$ 0,10 com retirada automática em `5x`:

```powershell
python -m blaze_auto.auto_bot --live --stake 0.10 --auto-cashout-at 5.00
```

### Rodar continuamente

O valor `--max-session-entries 0` significa que não existe limite de entradas
por execução. Depois de resolver uma entrada, o bot volta a procurar `MABBM` e
continua até receber `Ctrl+C` ou atingir uma proteção diária.

Paper trading contínuo:

```powershell
python -m blaze_auto.auto_bot --max-session-entries 0
```

Execução real contínua:

```powershell
python -m blaze_auto.auto_bot --live --stake 0.10 --auto-cashout-at 5.00 --max-session-entries 0
```

Use o modo real somente depois da validação em paper e com as variáveis do
`.env` configuradas.

### Proteções que continuam ativas

Mesmo no modo contínuo, os padrões são: stop-loss diário de R$ 5, stop-gain
diário de R$ 5, máximo de 20 entradas diárias e apenas uma entrada por rodada.
O bot também impede uma segunda entrada na mesma rodada após reinício.

Paper e live são gravados separadamente em `data/auto_paper_signals.csv` e
`data/auto_live_signals.csv`.

## Testes

Instale as dependências de desenvolvimento e execute:

```powershell
python -m pip install -r requirements-dev.txt
pytest
```

## Baixar um mês de histórico

A API histórica é paginada em blocos de 100 rodadas. O coletor percorre as
páginas, tenta novamente falhas transitórias, elimina IDs duplicados, aplica o
intervalo de data localmente e grava em ordem cronológica:

```powershell
python -m blaze_auto.history_cli --days 30
```

Por padrão o coletor faz pausas entre páginas e recua automaticamente quando a
API responde `429 Too Many Requests`. Evite aumentar `--workers`, pois a API
aplica limite de requisições.

Saída padrão:

- `data/crash_history_30d.csv`
- `data/crash_history_30d.csv.meta.json`

Para escolher datas exatas:

```powershell
python -m blaze_auto.history_cli `
  --start 2026-07-27T21:49:10.310Z `
  --end 2026-08-26T21:49:10.310Z `
  --output data/crash_2026-07-27_a_2026-08-26.csv
```
