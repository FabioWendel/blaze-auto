# Blaze Auto — Crash e Double

Monitoramento, coleta e automação do Crash por padrões, com login local pelo
navegador e menu para executar Crash ou Double. O Double tem uma sequência
limitada de dobragens com alternância de vermelho/preto. Simulação é o padrão;
apostas reais exigem autorização explícita. Padrões passados e dobragens não
garantem ganhos nem recuperação de perdas.

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

### Opção 1: capturar pelo navegador após fazer login

Instale o suporte opcional e execute dentro da pasta `blaze-auto`:

```powershell
python -m pip install -e ".[browser]"
python -m blaze_auto.login_cli
```

O comando abre o executável do **Google Chrome instalado diretamente**, em uma
janela com perfil temporário separado. O modo padrão é `--launch-mode normal`:
não usa o inicializador de automação do Playwright; conecta a observação das
requisições via Chrome DevTools Protocol, somente em `127.0.0.1`.
Isso **não significa um navegador sem instrumentação nem garante evitar bloqueios**.
Faça login
manualmente e permaneça no Crash. Se solicitado no terminal, abra seu perfil e
a carteira BRL nessa mesma janela. **Não é necessário fazer uma aposta.**

Ao observar respostas autenticadas com token, usuário, rank e carteira BRL, o
comando cria/atualiza o `.env` e mantém a janela e o programa abertos, mostrando:

```text
ESCOLHA O JOGO
1 - Crash
2 - Double
0 - Sair e fechar a janela de captura
```

Digite `1` ou `2` para abrir o jogo na **mesma janela já logada** e configurar
a automação. Em seguida escolha **simulação** (Enter/padrão), **apostas reais**,
**só abrir o site**, ou voltar. Configure o valor inicial, limites diários e
quantidade de entradas da sessão (`0` = contínuo; `1` = uma entrada e seu resultado).

- **Crash:** executa o mesmo `blaze_auto.auto_bot` já existente. O menu sugere
  padrão `MABBM` e autoretirada `5.00x`; ambos podem ser alterados. No campo
  **Padrão Crash**, digite `2` para experimentar `BBBBM` com retirada sugerida
  de `1.50x`. Essa opção teve prejuízo no teste histórico e não é recomendação
  para dinheiro real. Selecione simulação para acompanhá-la sem apostar.
- **Double:** executa `blaze_auto.double_bot`, com a sequência descrita abaixo.
- **Modo real:** mostra um resumo e exige digitar `REAL` antes de iniciar.
  Entradas e resultados aparecem no terminal; não é necessário clicar em apostar.

Só um bot é executado por vez nesse menu. `Ctrl+C` durante a automação interrompe
o bot e volta ao menu; **não cancela uma aposta já enviada**. Ao atingir o limite
de entradas da sessão, aguarda o resultado da última aposta antes de voltar.
`0` ou `Ctrl+C` no menu principal fecha a janela criada pelo programa e mantém o
`.env`. Fechar apenas o navegador não é um comando de parada do bot: use `Ctrl+C`
no terminal. Não execute outra instância do bot ao mesmo tempo.

A captura de credenciais é desligada antes do menu. Ao iniciar um bot pelo menu,
os valores do `.env` recém-salvo têm prioridade sobre credenciais antigas do
terminal, sem imprimir seus valores. Navegar entre jogos não regrava o `.env`.

Para somente capturar e encerrar, como antes:

```powershell
python -m blaze_auto.login_cli --login-only
```

Configurações
não relacionadas e o `BLAZE_ROOM_ID` existente são preservados (padrão 4 se ausente).
Os campos opcionais de sessão/versão antigos são limpos se não vierem na captura.
Com `--login-only`, não inicia o bot nem envia entradas/cashouts.

O formato atual de inicialização da conta é reconhecido em `/api/bootstrap/me`:
`user.username`, `user.xp.rank` e `wallets[].currency.type` (carteira BRL), usando
o `Authorization` da própria requisição autenticada. Os formatos anteriores de
perfil/carteira continuam aceitos. O terminal informa os nomes dos campos já
capturados e dos que ainda faltam, sem mostrar seus valores.

Para usar o Microsoft Edge instalado:

```powershell
python -m blaze_auto.login_cli --browser edge
```

Se não tiver Chrome/Edge, use o Chromium do Playwright:

```powershell
python -m playwright install chromium
python -m blaze_auto.login_cli --browser chromium
```

O prazo padrão para login é de 5 minutos; ajuste com `--timeout-seconds 600`.
O script observa somente as APIs permitidas da Blaze nessa janela: não lê seu
perfil pessoal do navegador, não exporta senhas/cookies, não imprime tokens e não
grava arquivos de tráfego. O Chrome pode manter dados da sessão no perfil temporário
durante o login; esse perfil é removido ao encerrar normalmente. Se o computador
desligar ou a execução for encerrada à força, ele pode permanecer na pasta temporária.
CAPTCHA e autenticação em duas etapas, quando houver,
são concluídos por você normalmente no site.

O Chrome atual exige um diretório de dados separado para depuração, conforme a
[documentação do Chrome](https://developer.chrome.com/blog/remote-debugging-port).
Não é uma conexão com o seu Chrome pessoal já aberto: suas abas e seu perfil
normal não são alterados nem fechados. A porta de depuração permite controle
desse navegador por processos locais; não use essa janela para outros sites
e nunca exponha a porta na rede.

Se a instalação não for encontrada, use `--browser-path` com o caminho do
executável. Para voltar ao inicializador anterior, use `--launch-mode playwright`.
Se o botão de login continuar desabilitado, a causa precisa ser verificada no
site (validação do formulário, recursos não carregados, CAPTCHA ou restrição).
O comando não resolve nem contorna essas verificações.

Se faltar um campo ou a janela for fechada antes da captura completa, o `.env`
permanece intacto. Não mistura os dados existentes com uma captura parcial. Se
forem observadas várias carteiras BRL, não escolhe uma arbitrariamente: informe
`--wallet-id ID_DA_CARTEIRA` para selecionar uma das carteiras observadas.
O site pode mudar as rotas/formatos de perfil e carteira; nesse caso a captura
informará os nomes dos campos ausentes e exigirá adaptação, sem inventar valores.

O `.env` contém credenciais em texto e deve permanecer privado. Ele e os arquivos
temporários `.env.*` são ignorados pelo Git. O menu usa a captura nova. Ao iniciar
os bots **diretamente pela linha de comando**, reinicie-os após renovar a captura.
Nesses comandos diretos, remova variáveis `BLAZE_*` antigas do terminal ou abra
outro terminal: variáveis do ambiente têm prioridade sobre o arquivo `.env`.
Quando o token expirar, execute o comando de login novamente.

### Opção 2: preencher manualmente pelo DevTools

Se já tiver um `.env`, edite-o; não copie o modelo por cima dele. Para criar um
arquivo pela primeira vez:

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

### Se ocorrer reset de conexão, timeout de resposta ou resposta incerta

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

## Crash: bot automático por sequência

O bot acompanha `crash.tick`, classifica cada resultado como `B` (`<2x`), `M`
(`2x–4,99x`) ou `A` (`>=5x`) e arma uma entrada para a próxima rodada `waiting`
quando encontra o padrão configurado. O padrão inicial é `MABBM`, com retirada
automática em `5x`.

### Opção mais frequente: baixas + média (experimental)

O preset `baixas-media` espera **quatro resultados abaixo de 2x**, seguidos de
**uma média concluída entre 2x e menos de 5x** (`BBBBM`). Só depois arma a
entrada na **próxima** rodada em `waiting`, com retirada sugerida em `1.50x`.
Não entra durante a média que confirmou o sinal. A stake é fixa; não há dobragem.

No histórico local de 101.173 rodadas, ocorreram aproximadamente 77 sinais/dia,
contra 14 do `MABBM`. São oportunidades teóricas: as janelas e os limites diários
continuam valendo, incluindo o máximo padrão de 20 entradas/dia.

**Mais sinais não deram mais lucro:** `BBBBM/1.50x` acertou 62,08% no trecho
final do teste, abaixo dos 66,67% necessários para empatar. Foram 530 entradas,
329 ganhos e 201 perdas: prejuízo de 36,50 unidades de stake (ROI −6,89%).
As 32 combinações frequentes analisadas tiveram ROI negativo nesse trecho.
Veja [metodologia e comparação](docs/crash-pattern-analysis.md).

Para testar continuamente **sem dinheiro real**, a partir da pasta do projeto:

```powershell
python -m blaze_auto.auto_bot --preset baixas-media --stake 1 --max-session-entries 0
```

Para fazer só uma entrada simulada, esperar o resultado e encerrar:

```powershell
python -m blaze_auto.auto_bot --preset baixas-media --stake 1 --max-session-entries 1
```

No menu após o login: **Crash → Simulação → Padrão Crash: `2`**; aceite a
retirada sugerida `1.50`. O preset não ativa `--live`, não altera o `.env`, não
troca o padrão de outras execuções e não aumenta os limites de risco. Os modos
reais existentes continuam exigindo `--live` na CLI ou confirmação `REAL` no menu.
Não execute dois bots ao mesmo tempo sobre a mesma conta/ledger.

As opções explícitas `--pattern` e `--auto-cashout-at` substituem as do preset.
Sem preset, permanece `MABBM/5.00x`; isso preserva a configuração, não certifica
que ela será lucrativa. Uma sequência de baixas não torna a próxima média devida.

Reproduzir a análise offline, sem credenciais nem apostas:

```powershell
python -m blaze_auto.crash_analysis --input data/crash_history_30d.csv
```

O relatório fica em `data/crash_analysis/report.json` (ignorado pelo Git).
O CSV original não é modificado. A análise usa stake de uma unidade, separa
treino/validação/teste por datas e também inclui uma ilustração com limites diários.

### Falha na entrada e janela de tempo

Se a API recusar a entrada, o bot descarta o sinal e espera **um novo padrão**.
O sinal antigo não é reaproveitado em outra rodada. Se a rodada de entrada já estiver em
`graphing`/`complete`, ou o socket estiver desconectado/sem tick recente, não entra.

Enquanto o socket ainda mostra `complete` da **rodada que gerou o padrão**, o
bot mantém o sinal armado, mesmo se esse último tick tiver mais de 2 segundos.
Isso é espera entre rodadas, não autorização para apostar com dados antigos.
Ao chegar a próxima rodada, continua exigindo `waiting` recente antes de enviar.
Erro/desconexão descarta o sinal; um novo resultado também invalida o gatilho antigo.

O log mostra `SINAL ARMADO | ... | aguardando abertura da próxima rodada` uma
vez por gatilho. Nos descartes, `motivo=` distingue socket indisponível, tick
desatualizado (com idade), estado fora de `waiting`, nova rodada concluída,
troca de rodada durante uma tentativa, prazo local e tentativas esgotadas.

O bot só repete uma falha `ConnectTimeout` (conexão não estabelecida, pedido
não enviado), sempre na **mesma rodada ainda em `waiting`**, com tick recebido
há no máximo 2 segundos e sem erro no socket. São até 3 tentativas no total,
separadas por pelo menos 0,5 segundo, com teto local de 3 segundos desde o início
da tentativa. A janela fecha assim que qualquer condição deixa de valer.
Recusas HTTP não são repetidas automaticamente.

Esse teto local não é uma contagem regressiva oficial da Blaze: o socket fornecido
não informa o instante exato de fechamento. Uma requisição em andamento ainda
pode terminar depois da janela; se for aceita, o bot acompanha essa entrada.

Para permitir somente uma tentativa, acrescente `--max-entry-attempts 1` ao
comando do bot. O teto local é configurável com `--entry-window-seconds 3`.
Essas opções valem para `blaze_auto.auto_bot`, não para o executor manual.

**Reset 10054, timeout de leitura ou resposta perdida não comprovam falha da
aposta.** Nesses casos, o bot continua pausando para conferência conforme a seção
anterior, mesmo se ainda houver tempo: repetir ou seguir poderia duplicar apostas
ou ignorar dinheiro já comprometido. Nenhuma repetição aumenta a stake.

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

### Ver se o socket continua conectado

Os logs periódicos do socket ficam **desligados por padrão**, tanto no Crash
quanto no Double e no menu após login. Entradas, resultados, descartes e pausas
importantes continuam aparecendo. O monitoramento e as verificações de conexão
continuam ativos: somente a impressão desses status foi desativada.

Para diagnóstico, acrescente `--socket-log-interval 10` ao comando do bot.
Isso habilita um status a cada 10 segundos e quando observa mudança de conexão,
mesmo sem encontrar um padrão. Exemplo ilustrativo:

```text
[20:15:10] SOCKET | CONECTADO | tentativa de conexão=1 | última mensagem=0.2s atrás | crash.tick=0.2s atrás (RECENTE) | rodada=exemplo | estado=waiting
[20:15:20] SOCKET | RECONECTANDO | tentativa de conexão=2 | última mensagem=10.2s atrás | crash.tick=10.2s atrás (SEM TICK RECENTE) | rodada=exemplo | estado=complete
```

`CONECTADO` indica abertura do WebSocket; a idade da última mensagem (inclusive
ping/pong) e do último `crash.tick` mostra se há atividade. **Conexão aberta não
garante ticks recentes.** `SEM TICK RECENTE` significa mais de 2 segundos sem
tick e pode ocorrer entre rodadas; sozinho, não comprova desconexão.
Depois de reconectar, o indicador aguarda um novo tick, sem reutilizar o anterior.

Para mostrar a cada 5 segundos, acrescente `--socket-log-interval 5` ao seu
comando; `--socket-log-interval 0` desliga novamente. O indicador não exige
`--verbose`, não altera as regras de entrada e
não exibe tokens nem conteúdo bruto das mensagens. Uma chamada HTTP em andamento
pode atrasar a próxima linha de status; a leitura do socket continua em background.

### Proteções que continuam ativas

Mesmo no modo contínuo, os padrões são: stop-loss diário de R$ 5, stop-gain
diário de R$ 5, máximo de 20 entradas diárias e apenas uma entrada por rodada.
O bot também impede uma segunda entrada na mesma rodada após reinício.

Paper e live são gravados separadamente em `data/auto_paper_signals.csv` e
`data/auto_live_signals.csv`.

## Double: última cor, dobragem e alternância

Usa o evento público `double.tick` na sala `double_room_1`. As cores são branco
(`0`), vermelho (`1`) e preto (`2`). O resultado é processado somente no estado
`complete`, mesmo que cor e número já apareçam durante `rolling`.

A regra implementada é:

1. Esperar um resultado concluído vermelho ou preto; apostar nessa mesma cor na
   **próxima** rodada `waiting`. Não tenta apostar na rodada já encerrada.
2. Se perder, dobrar o valor e apostar na cor oposta na próxima rodada.
3. Continuar alternando a cada perda: início vermelho → preto → vermelho;
   início preto → vermelho → preto.
4. Branco conta como perda para uma aposta aberta em vermelho/preto. Sem aposta
   aberta, branco não inicia sequência: espera o próximo resultado vermelho/preto.
   Não há aposta adicional de proteção no branco.
5. Ao ganhar, restaurar o valor inicial e esperar **outro** resultado vermelho/preto.
   O próprio resultado vencedor não inicia uma nova sequência.
6. Ao perder a última dobragem permitida, encerrar a sessão. Não continua dobrando.

O padrão é **3 dobragens**, além da entrada inicial. Com base de R$ 0,10, os
valores são R$ 0,10 → R$ 0,20 → R$ 0,40 → R$ 0,80; perder todas custa R$ 1,50.
Com base de R$ 1, a mesma sequência custa R$ 15. Dobrar aumenta rapidamente a
exposição e não torna uma cor mais provável. Este recurso não foi validado com
apostas reais; comece em simulação e confira o comportamento e os registros.

Simulação contínua (não exige login):

```powershell
python -m blaze_auto.double_bot --stake 0.10 --max-gales 3
```

Uma entrada simulada e seu resultado, **sem executar dobragens seguintes**:

```powershell
python -m blaze_auto.double_bot --stake 0.10 --max-session-entries 1
```

Execução real, somente se decidir autorizar e após configurar o `.env`:

```powershell
python -m blaze_auto.double_bot --live --stake 0.10 --max-gales 3 --daily-stop-loss 5.00 --daily-take-profit 5.00 --max-daily-entries 20
```

Sem `--live`, nunca envia apostas. No comando direto, `--live` é a autorização;
a confirmação digitada `REAL` é específica do menu. A sala do Double é sempre
`1`, independentemente de `BLAZE_ROOM_ID=4` usado pelo Crash.

### Limites, falhas e retomada do Double

Os padrões são perda diária máxima de R$ 5, ganho diário de R$ 5 e 20 entradas
por dia **UTC**, contando cada dobragem como uma entrada. Esses limites são
obrigatoriamente positivos no Double. `--max-session-entries 0` é contínuo e
`--max-gales 0` desliga dobragens; aceita no máximo 10 dobragens configuradas.
Antes de cada entrada, verifica se uma perda integral ultrapassaria o orçamento
diário de perda líquida. Por exemplo, base R$ 1 e stop-loss R$ 5 permitem perder
R$ 1 e R$ 2, mas bloqueiam a próxima entrada de R$ 4.

Se perder a janela ou a conexão, descarta a sequência e espera novo vermelho/preto;
não leva uma dobragem atrasada para outra rodada. Recusa da API não conta como
perda nem aumenta o valor. Só `ConnectTimeout` comprovadamente anterior ao envio
pode ser repetido, na mesma janela recente, usando as mesmas proteções do Crash.
Resposta incerta, mudança de janela durante o POST ou resposta incompatível
interrompem o bot e bloqueiam reinícios até conferência manual.

Os CSVs são separados: `data/double_paper_signals.csv` e
`data/double_live_signals.csv`. Guardam cor apostada, dobragem, valor, sequência,
resultado e lucro líquido calculado (vermelho/preto: +stake ao ganhar, -stake
ao perder). Esse cálculo pelo resultado público **não é uma conferência do
saldo da conta**. O status periódico do socket é opcional; use
`--socket-log-interval 10` para exibir `double.tick` durante diagnóstico.

### Confirmação da entrada Double

A resposta usada pelo cliente público da Blaze contém a cor na raiz (`color`)
e valor/moeda em `bet.amount` e `bet.currency_type`. A validação aceita esse
formato **sem exigir `bet.id` ou `bet.color`**; o formato anterior com cor dentro
de `bet` também é aceito. Se ambas as cores vierem, precisam coincidir. A origem
deste contrato é o handler de `/roulette_bets` e o reducer `DOUBLE_V2/OWN_BET`
no [código público da Blaze](https://blaze.bet.br/static/js/index~27.c094d883.js).

Um HTTP 2xx sozinho não basta: continuam obrigatórias a cor solicitada, o valor
e BRL, sem indicação de erro. IDs explícitos da rodada, quando presentes, são
comparados. Respostas incompatíveis ou perdidas continuam pausando sem reenviar.
O terminal agora mostra o motivo da pausa e a rodada, em vez de só “aceitação
incerta”. Para erros de formato, registra apenas nomes conhecidos de campos e
seus tipos; nunca o JSON bruto, tokens, headers ou valores pessoais. A resposta
original da tentativa antiga não foi armazenada, portanto não pode ser reconstruída.

Uma aposta aceita mas sem resultado observado antes de interromper também
bloqueia o reinício. Confira o histórico da conta e reconcilie, por exemplo:

```powershell
python -m blaze_auto.reconcile --signals data/double_live_signals.csv --round-id ID_DA_RODADA --outcome loss --profit=-0.10 --confirmed
```

Para simulação interrompida, use o CSV `double_paper_signals.csv` e confira o
resultado público da rodada. Não apague ou troque o ledger para ignorar uma
pendência. Após reconciliar, uma nova execução começa esperando novo vermelho/preto,
sem recuperar dobragens antigas. Não há ajuste automático de saldo ou apostas
de recuperação. A API privada pode mudar; respostas fora do formato esperado
exigem verificação, sem reenviar apostas.

## Testes

Instale as dependências de desenvolvimento e execute:

```powershell
python -m pip install -r requirements-dev.txt
pytest
```

O teste de navegador é opcional, usa Chrome em modo headless e substitui toda a
rede da página por respostas fictícias (não faz login nem acessa a conta). Para
incluí-lo no PowerShell, após instalar `.[browser]` e ter Chrome instalado:

```powershell
$env:BLAZE_RUN_BROWSER_TEST = "1"
python -m pytest -q
```

## Baixar o histórico público do Double

O coletor abaixo não usa login nem envia apostas. Retorna as rodadas públicas
com ID, data UTC, cor (`0` branco, `1` vermelho, `2` preto), número e server seed.
Não confunda com `/api/game_provider_rounds`, que retorna apostas da conta.

```powershell
python -m blaze_auto.double_history_cli --start 2026-07-30T04:00:00.000Z --end 2026-08-29T03:59:59.999Z --output data/double_history/2026-07-30_2026-08-28
```

O exemplo corresponde a 30/07–28/08 no horário de Manaus (UTC−4). Se o fim
solicitado ainda não estiver disponível, o coletor fixa o fim em agora menos
2 minutos para excluir rodadas em andamento e manter a paginação estável.
O intervalo solicitado e o efetivamente coletado ficam em `request.json`.
Uma execução retomada mantém o mesmo fim; use uma nova pasta para atualizar
a cobertura depois.

O campo `total_pages` dessa API representa a **quantidade de registros**, inclusive
como texto numérico, e cada página contém até 100 registros. O coletor verifica
o tamanho de cada página, IDs duplicados, consistência entre cor/número, datas,
contagem final e uma página vazia após o fim. Se algo divergir, não declara a
coleta concluída. Usa até 2 conexões com espaçamento compartilhado e recua ao
receber HTTP 429. Não aumente a carga para contornar limites da API.

Os JSONs ficam na pasta escolhida, ignorada pelo Git quando estiver dentro de
`data/double_history/`:

- `double_history.json`: rodadas em ordem cronológica e metadados da cobertura.
- `hourly_counts.json`: contagens por hora de Manaus, incluindo brancos, e por dia.
- `summary.json`: totais e verificações de integridade.
- `pages/`: cache para retomar com o mesmo comando após interrupção.

Contagens por horário são descritivas; diferenças históricas não demonstram
previsibilidade. Horas/dias incompletos e testes de vários horários precisam
ser considerados antes de qualquer conclusão. O coletor não altera o bot.

## Baixar um mês de histórico do Crash

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
