"""Catálogo Pedagógico dos Conceitos Complexos do Ecossistema B3.

Fornece fichamentos aprofundados, matemáticos e operacionais sobre os mecanismos mais
críticos do mercado de capitais brasileiro: Novação Legal, Default Waterfall, Risco CORE,
Compensação Multilateral, Leilão MOC/LOC, Túneis de Preço, Central de Gravames, Drex DvP
Atômico, Superfícies de Volatilidade e Vigilância de Abusos BSM.

100% Python Standard Library (dataclasses, typing, json).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class B3Concept:
    """Estrutura analítica de um conceito complexo da B3."""

    id: str
    category: str
    title: str
    badge: str
    summary: str
    what_is: str
    why_exists: str
    how_it_works: List[str]
    math_rule: str
    practical_example: str
    diagram_ascii: str
    related_flows: List[str]
    related_laws: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


CONCEPTS: List[B3Concept] = [
    B3Concept(
        id="novacao-ccp",
        category="CÂMARA & RISCO",
        title="Novação Legal e Contraparte Central (Lei nº 10.214/2001)",
        badge="Lei 10.214 / LAW-004",
        summary="A Câmara B3 extingue o risco bilateral tornando-se compradora de todo vendedor e vendedora de todo comprador.",
        what_is=(
            "No exato milissegundo em que uma ordem é casada no PUMA Trading System, o contrato bilateral "
            "original entre Comprador e Vendedor é extinto pelo instituto jurídico da novação civil (Art. 360 do "
            "Código Civil e Art. 5º da Lei 10.214/2001) e substituído por dois contratos autônomos onde a "
            "Câmara B3 assume a posição de Contraparte Central Garantidora (CCP)."
        ),
        why_exists=(
            "Sem a novação, a quebra de um único grande banco ou corretora desencadearia um calote em cadeia "
            "(efeito dominó sistêmico). Com a B3 interposta, os participantes só têm risco de crédito contra a própria B3, "
            "que opera com nota de crédito AAA e salvaguardas financeiras bilionárias."
        ),
        how_it_works=[
            "Casamento de Ordens no PUMA Trading System (Trade Execution).",
            "Extinção imediata da relação de crédito bilateral entre as duas corretoras.",
            "Criação de 2 pernas garantidas: B3 Compradora do Vendedor e B3 Vendedora do Comprador.",
            "Em caso de inadimplência em D+2, a B3 honra a entrega financeira e física pontualmente.",
            "A B3 executa as garantias do participante faltoso sem afetar a outra ponta adimplente.",
        ],
        math_rule=(
            "Equilíbrio de Neutralidade Direcional da CCP:\n"
            "∑_{i=1}^N PosiçãoLíquida_i + Posição_B3 = 0, onde Posição_B3 ≡ 0.\n"
            "(A B3 nunca assume posição especulativa direcional; todo ativo a receber é rigorosamente igual ao a entregar)."
        ),
        practical_example=(
            "Se o Banco A comprou R$ 800 milhões em ações de VALE3 da Corretora B e quebrou na manhã do dia de liquidação (D+2), "
            "a Corretora B recebe os R$ 800 milhões integralmente no STR/BACEN às 13h, pois a B3 é sua contraparte legal direta."
        ),
        diagram_ascii=(
            "+-------------+                    +-------------+\n"
            "| Comprador A | <================> | Vendedor B  | (Antes: Risco Bilateral A <-> B)\n"
            "+-------------+                    +-------------+\n"
            "                        | \n"
            "                        v [ NOVAÇÃO LEGAL DA B3 ]\n"
            "+-------------+       +--------+       +-------------+\n"
            "| Comprador A | <---> | CCP B3 | <---> | Vendedor B  | (Depois: Risco Bilateral Zero)\n"
            "+-------------+       +--------+       +-------------+"
        ),
        related_flows=["FL-002", "FL-004", "FL-006", "FL-014", "FL-100"],
        related_laws=["LAW-002", "LAW-004", "LAW-106A"],
    ),
    B3Concept(
        id="default-waterfall",
        category="CÂMARA & RISCO",
        title="Cascata de Salvaguardas da Câmara (Default Waterfall)",
        badge="LAW-006 / LAW-106B",
        summary="Muralha financeira de 5 camadas determinísticas que absorve calotes bilionários sem transferir prejuízo ao mercado.",
        what_is=(
            "A sequência obrigatória e inegociável de fontes de recursos que a Câmara B3 aciona para honrar as "
            "obrigações financeiras de um participante inadimplente, blindando o restante do sistema financeiro."
        ),
        why_exists=(
            "Garante que o colateral de participantes inocentes e solventes jamais seja confiscado ou utilizado "
            "antes da exaustão absoluta de todas as garantias do participante faltoso e do capital próprio dedicado da B3."
        ),
        how_it_works=[
            "1ª Camada: Margens e Garantias do próprio Inadimplente (Títulos Públicos, Dólar, Ouro, Ações com haircut).",
            "2ª Camada: Contribuição do Inadimplente ao Fundo de Liquidação da Câmara.",
            "3ª Camada: Capital Próprio da B3 dedicado à CCP (Skin-in-the-game da bolsa).",
            "4ª Camada: Fundo de Salvaguardas Mutualizado entre os Membros de Compensação adimplentes.",
            "5ª Camada: Patrimônio Social Geral e Linhas Especiais de Liquidez da B3 S.A.",
        ],
        math_rule=(
            "Conservação de Absorção Deterministica (LAW-106B):\n"
            "Deficit_Residual = Perda_Total - (Margem_Default + Fundo_Default + SkinGame_B3 + Fundo_Mutual) ≡ 0.\n"
            "Invariante: SafeguardPool < InitialSafeguard => CollateralPool[Defaulted] ≡ 0 (Ordem inviolável)."
        ),
        practical_example=(
            "Em uma inadimplência de R$ 600 milhões: R$ 420M são quitados com os títulos federais depositados pelo faltoso, "
            "R$ 80M com sua cota do fundo, e os R$ 100M restantes com o capital próprio dedicado da B3. "
            "Nenhum centavo de outro corretor é utilizado."
        ),
        diagram_ascii=(
            "[ CALOTE INADIMPLENTE: R$ 600M ]\n"
            "       |\n"
            "       v\n"
            "+-------------------------------------------------------+\n"
            "| 1ª CAMADA: Garantias e Margem do Inadimplente (R$ 420M)|\n"
            "+-------------------------------------------------------+\n"
            "       | (Resta R$ 180M)\n"
            "       v\n"
            "+-------------------------------------------------------+\n"
            "| 2ª CAMADA: Cota do Fundo do Inadimplente (R$ 80M)     |\n"
            "+-------------------------------------------------------+\n"
            "       | (Resta R$ 100M)\n"
            "       v\n"
            "+-------------------------------------------------------+\n"
            "| 3ª CAMADA: Capital Próprio da B3 (Skin-in-the-game)   |\n"
            "+-------------------------------------------------------+ ---> [ DÉFICIT ZERADO! ]"
        ),
        related_flows=["FL-005", "FL-038", "FL-068", "FL-100"],
        related_laws=["LAW-006", "LAW-106B"],
    ),
    B3Concept(
        id="leilao-moc-freeze",
        category="PUMA TRADING",
        title="Leilão de Fechamento MOC/LOC & Janela de Congelamento (Freeze)",
        badge="LAW-104A / LAW-104B / LAW-104C",
        summary="A dinâmica estocástica que determina os preços oficiais de fechamento e impede predações de último segundo.",
        what_is=(
            "Mecanismo eletrônico executado nos últimos minutos do pregão ordinário (16:55 às 17:00+) no PUMA Trading System, "
            "onde todas as ordens convergem para a apuração de um preço único por ativo: o Preço Teórico de Fechamento (PTP)."
        ),
        why_exists=(
            "Trilhões de reais em cotas de fundos de investimento, índices internacionais (MSCI, FTSE, Ibovespa) e derivativos "
            "são precificados no fechamento. O leilão com congelamento aleatório impede manipulações pontuais de milissegundos."
        ),
        how_it_works=[
            "Entrada de ordens Market-On-Close (MOC - sem preço) e Limit-On-Close (LOC - com preço limite).",
            "Cálculo em tempo real do PTP e do desbalanceamento entre compradores e vendedores.",
            "Janela de Congelamento (Freeze Period): congelamento automático do livro com duração pseudo-aleatória.",
            "Inviolabilidade do Freeze: cancelamentos e reduções de ordens são sumariamente rejeitados pelo PUMA (LAW-104A).",
            "Prioridade MOC: ordens a mercado MOC são atendidas antes de qualquer ordem limitada LOC (LAW-104B).",
            "Uncross: casamento simultâneo de todo o volume executável no PTP oficial (LAW-104C).",
        ],
        math_rule=(
            "Maximização do Volume Executável (LAW-104C):\n"
            "PTP = argmax_P [ min( QtdCompraAcumulada(P), QtdVendaAcumulada(P) ) ]\n"
            "Critério de Desempate: Menor Desbalanceamento residual |QtdCompra(P) - QtdVenda(P)|."
        ),
        practical_example=(
            "Um fundo de pensão lança ordem MOC de 500.000 ações de PETR4 às 16:58. Ele tem garantia absoluta de que será "
            "executado pelo preço oficial de fechamento apurado, à frente de ordens LOC que tentaram adivinhar o preço limite."
        ),
        diagram_ascii=(
            "16:55:00                       16:59:30                  17:00:00 (Aleatório)\n"
            "   |------------------------------|-----------------------------|\n"
            "      Leilão Normal (MOC / LOC)          Janela de Freeze             UNCROSS FINAL\n"
            "   - Inclusão e alteração livres      - Cancelamento PROIBIDO!      - Preço Único PTP\n"
            "   - Cálculo contínuo do PTP          - Ordens travadas no book     - MOC priorizado"
        ),
        related_flows=["FL-011", "FL-044", "FL-059", "FL-098"],
        related_laws=["LAW-104A", "LAW-104B", "LAW-104C"],
    ),
    B3Concept(
        id="tuneis-circuit-breaker",
        category="PUMA TRADING",
        title="Túneis de Oscilação de Preços e Circuit Breaker PUMA",
        badge="LAW-093 / Resolução CVM 135",
        summary="Filtros perimetrais contínuos que bloqueiam erros de digitação e interrompem pânicos de mercado.",
        what_is=(
            "Limites matemáticos de oscilação impostos pelo PUMA Trading System a cada microsegundo sobre o preço "
            "de qualquer ordem inserida ou negócio prestes a ser casado."
        ),
        why_exists=(
            "Evita perdas patrimoniais catastróficas decorrentes de erros de digitação operacional (fat-finger), "
            "algoritmos de alta frequência descalibrados ou pânicos sistêmicos."
        ),
        how_it_works=[
            "Túnel de Rejeição: se a ordem tiver preço com desvio extremo do preço de referência, é rejeitada sem entrar no livro.",
            "Túnel de Leilão Estático: se o negócio for casar com desvio superior à banda estática em relação à abertura, entra em leilão.",
            "Túnel de Leilão Dinâmico: se a oscilação em relação ao último negócio for muito rápida (1% a 2%), aciona leilão dinâmico curto.",
            "Circuit Breaker Geral: paralisação do pregão inteiro se o índice Ibovespa cair -10% (30 min) ou -15% (1 hora).",
        ],
        math_rule=(
            "Fórmula do Túnel de Preço Permitido (LAW-093):\n"
            "P_limite_inferior = Preço_Ref × (1 - Percentual_Túnel)\n"
            "P_limite_superior = Preço_Ref × (1 + Percentual_Túnel)\n"
            "Condição de Aceite: P_ordem ∈ [P_limite_inferior, P_limite_superior]."
        ),
        practical_example=(
            "Uma ordem de venda de VALE3 enviada a R$ 6,00 (quando a cotação é R$ 60,00) por erro humano é instantaneamente "
            "barrada pelo Túnel de Rejeição do PUMA, sem tocar no livro de ofertas."
        ),
        diagram_ascii=(
            "+---------------------------------------------------------+\n"
            "| TÚNEL SUPERIOR DE REJEIÇÃO (> +20%): Ordem DESCARTADA   |\n"
            "+ - - - - - - - - - - - - - - - - - - - - - - - - - - - - +\n"
            "| TÚNEL ESTÁTICO / DINÂMICO: Aciona LEILÃO de 5 minutos   |\n"
            "+ - - - - - - - - - - - - - - - - - - - - - - - - - - - - +\n"
            "| FAIXA DE NEGOCIAÇÃO CONTÍNUA (Preço Referência +- 2%)    |\n"
            "+ - - - - - - - - - - - - - - - - - - - - - - - - - - - - +\n"
            "| TÚNEL INFERIOR DE REJEIÇÃO (< -20%): Ordem DESCARTADA   |\n"
            "+---------------------------------------------------------+"
        ),
        related_flows=["FL-011", "FL-028", "FL-093"],
        related_laws=["LAW-093"],
    ),
    B3Concept(
        id="netting-multilateral",
        category="LIQUIDAÇÃO & SPB",
        title="Compensação Multilateral Contínua (Netting) vs Liquidação Bruta",
        badge="LAW-002 / LAW-027",
        summary="Como a B3 transforma bilhões em transações brutas em uma fração mínima de saldo financeiro a liquidar no Banco Central.",
        what_is=(
            "O processo matemático pelo qual centenas de milhares de operações de compra e venda de um participante "
            "são consolidadas em uma única posição devedora ou credora líquida no final do ciclo."
        ),
        why_exists=(
            "Se cada operação exigisse uma transferência individual em tempo real de moeda soberana no STR do Banco Central, "
            "o sistema financeiro entraria em colapso por falta de liquidez imediata (gridlock)."
        ),
        how_it_works=[
            "Durante a sessão, a B3 registra todas as compras e vendas de ativos de cada participante.",
            "No fechamento da grade, aplica a matriz de compensação multilateral contínua.",
            "Cada participante passa a ter apenas 1 número: Devedor Líquido (deve pagar) ou Credor Líquido (deve receber).",
            "A B3 envia mensagens LDL0001 ao Banco Central para debitar os devedores e creditar os credores.",
            "Eficiência de liquidez superior a 90% a 95% do volume bruto transacionado.",
        ],
        math_rule=(
            "Teorema da Conservação Soma-Zero Multilateral (LAW-002 / LAW-027):\n"
            "PosiçãoLíquida_i = ∑_j Vendas_{i,j} - ∑_j Compras_{i,j}\n"
            "Propriedade Invariante Global: ∑_{i=1}^N PosiçãoLíquida_i ≡ 0 (Total Devedor = Total Credor)."
        ),
        practical_example=(
            "Em um dia onde a B3 negociou R$ 40 bilhões brutos em ações e derivativos, a necessidade de liquidação "
            "efetiva em contas de reservas bancárias no Banco Central foi de apenas R$ 2,2 bilhões após o netting."
        ),
        diagram_ascii=(
            "OPERAÇÕES BRUTAS (R$ 40 BILHÕES):           COMPENSAÇÃO MULTILATERAL (NETTING B3):\n"
            "Banco A ---> R$ 100M ---> Banco B             Banco A: Deve R$ 20M líquido\n"
            "Banco B ---> R$  90M ---> Banco C     ===>    Banco B: Deve R$ 10M líquido\n"
            "Banco C ---> R$  80M ---> Banco A             Banco C: Recebe R$ 30M líquido\n"
            "(Total bruto: R$ 270M)                        (Total líquido STR: Apenas R$ 30M!)"
        ),
        related_flows=["FL-004", "FL-027", "FL-095", "FL-100"],
        related_laws=["LAW-002", "LAW-027", "LAW-106A"],
    ),
    B3Concept(
        id="registro-recebiveis-gravames",
        category="BALCÃO & GARANTIAS",
        title="Central de Gravames e Registro de Recebíveis (CMN 4.734 / Lei 14.711)",
        badge="LAW-031 / LAW-037",
        summary="A trava de domicílio bancário e prelazia fiduciária multi-grau que eliminou a fraude de garantias duplicadas.",
        what_is=(
            "A infraestrutura de Balcão (NoMe) que registra direitos creditórios (cartões de crédito, duplicatas, CPRs) "
            "e averba garantias de forma unívoca perante o Banco Central."
        ),
        why_exists=(
            "Historicamente, empresas davam as mesmas vendas futuras de cartão como garantia para múltiplos bancos diferentes. "
            "A Central de Gravames da B3 garante que o primeiro credor tenha prioridade jurídica absoluta (prelazia)."
        ),
        how_it_works=[
            "Credenciadoras de cartão enviam agendas de recebíveis para o Sistema Registrador da B3.",
            "Quando um lojista toma empréstimo, a B3 constitui o gravame fiduciário com grau de prelazia (1º grau, 2º grau).",
            "Trava de Domicílio: o dinheiro das vendas no cartão é direcionado automaticamente para o banco credor.",
            "Somente após a quitação integral da dívida de 1º grau os recebíveis fluem para o credor de 2º grau ou lojista.",
        ],
        math_rule=(
            "Cascata de Prelazia de Excussão (LAW-037):\n"
            "Para Fluxo Financeiro Recebido V:\n"
            "Pago_Grau1 = min(V, SaldoDevedor_Grau1)\n"
            "Pago_Grau2 = min(V - Pago_Grau1, SaldoDevedor_Grau2)\n"
            "Excedente_Lojista = max(0, V - Pago_Grau1 - Pago_Grau2)."
        ),
        practical_example=(
            "Um restaurante faturou R$ 100.000 em cartões. O Banco A tem garantia de 1º grau de R$ 70.000 e o Banco B tem "
            "garantia de 2º grau de R$ 50.000. A B3 direciona R$ 70.000 ao Banco A e R$ 30.000 ao Banco B. Ninguém perde garantias."
        ),
        diagram_ascii=(
            "+-------------------------------------------------------------------+\n"
            "| AGENDA DE RECEBÍVEIS REGISTRADA NA B3: R$ 100.000                 |\n"
            "+-------------------------------------------------------------------+\n"
            "             | \n"
            "             v [ CASCATA DE PRELAZIA - LEI 14.711 ]\n"
            "+-------------------------------------------------------------------+\n"
            "| 1º GRAU: Banco A (Dívida R$ 70k) ===> Recebe R$ 70.000 (100%)    |\n"
            "+-------------------------------------------------------------------+\n"
            "             | (Excedente: R$ 30k)\n"
            "             v\n"
            "+-------------------------------------------------------------------+\n"
            "| 2º GRAU: Banco B (Dívida R$ 50k) ===> Recebe R$ 30.000 (Parcial) |\n"
            "+-------------------------------------------------------------------+"
        ),
        related_flows=["FL-010", "FL-037", "FL-070", "FL-097"],
        related_laws=["LAW-031", "LAW-037"],
    ),
    B3Concept(
        id="drex-dvp-atomico",
        category="DLT & DREX",
        title="Liquidação DvP/PvP Atômica no Drex Fase 2 (Hyperledger Besu)",
        badge="LAW-089 / LAW-111",
        summary="A liquidação atômica síncrona em blockchain onde a entrega do título e o pagamento em Real Digital ocorrem no mesmo bloco.",
        what_is=(
            "Protocolo de liquidação em infraestrutura DLT/EVM do Banco Central e B3 onde a transferência de um título privado "
            "tokenizado (ERC-1155 / ERC-3643) e o débito em Real Digital (CBDC) ocorrem numa única transação atômica."
        ),
        why_exists=(
            "No mercado tradicional, títulos e dinheiro circulam em trilhos diferentes (título na depositária, dinheiro no SPB), "
            "criando o risco de liquidação (Risco Herstatt). O DvP atômico em smart contracts torna o calote fisicamente impossível."
        ),
        how_it_works=[
            "Contrato inteligente HTLC (Hash Time-Locked Contract) ou contrato DvP é acionado na rede Besu.",
            "O emissor do título tokenizado trava os tokens na custódia on-chain.",
            "O investidor trava o montante equivalente em Real Digital.",
            "A revelação do hash SHA-256 troca simultaneamente a propriedade de ambos os lados no mesmo bloco.",
            "Condição All-or-Nothing: se qualquer verificação falhar, ocorre rollback automático instantâneo.",
        ],
        math_rule=(
            "Princípio da Atomicidade All-or-Nothing (LAW-111):\n"
            "Estado_Final ∈ { (Tokens_Entregues ∧ CBDC_Pago), (Rollback_Tokens ∧ Rollback_CBDC) }.\n"
            "Jamais existe estado intermediário onde tokens foram entregues sem pagamento ou vice-versa."
        ),
        practical_example=(
            "Uma empresa emite R$ 50 milhões em debêntures tokenizadas no Drex. O investidor institucional transfere o Real Digital "
            "e recebe as debêntures em sua carteira com conformidade CVM 175 em 1,2 segundo."
        ),
        diagram_ascii=(
            "+----------------------------------------------------------------+\n"
            "|           SMART CONTRACT DVP ATÔMICO (HYPERLEDGER BESU)         |\n"
            "+----------------------------------------------------------------+\n"
            "|  Lado A: Debênture Token RWA      <--->  Lado B: Real Digital  |\n"
            "|  Hashlock: SHA-256(Segredo)             Timelock: 24 Horas     |\n"
            "+----------------------------------------------------------------+\n"
            "             |                                   |\n"
            "             v [ EXECUÇÃO SÍNCRONA NO MESMO BLOCO EVM ]\n"
            " [ Tokens entregues ao Investidor ] ∧ [ Real Digital pago ao Emissor ]"
        ),
        related_flows=["FL-089", "FL-100", "FL-105"],
        related_laws=["LAW-089A", "LAW-106C", "LAW-111"],
    ),
    B3Concept(
        id="superficies-volatilidade",
        category="ENGENHARIA QUANTITATIVA",
        title="Superfícies de Volatilidade Paramétricas SVI & SABR",
        badge="LAW-107A / LAW-107B",
        summary="Modelagem matemática contínua do sorriso de volatilidade com garantia formal contra oportunidades de arbitragem.",
        what_is=(
            "Modelos matemáticos avançados (SVI de Jim Gatheral e SABR de Hagan et al.) que calibram a volatilidade implícita "
            "de opções de Dólar (DOL) e Ibovespa (IBOV) para qualquer preço de exercício e prazo de vencimento."
        ),
        why_exists=(
            "O modelo simplificado de Black-Scholes assume volatilidade plana, o que é falso no mercado real. Opções muito fora do "
            "dinheiro têm prêmios mais altos para precificar caudas gordas e risco de crash (o 'smile' ou 'skew')."
        ),
        how_it_works=[
            "O modelo ajusta parâmetros de nível base (a), inclinação de assimetria (b, rho) e curvatura (sigma).",
            "Não-Arbitragem de Calendário (LAW-107A): a variância total w(k, T) deve ser monotonicamente crescente com o tempo.",
            "Não-Arbitragem Butterfly (LAW-107B): a densidade neutra ao risco g(k) calculada analiticamente deve ser sempre ≥ 0.",
            "Garante que mesas quantitativas operem sem distorções de arbitragem de borboleta de custo negativo.",
        ],
        math_rule=(
            "Densidade de Risco de Gatheral (LAW-107B):\n"
            "g(k) = (1 - \\frac{k w'}{2w})^2 - \\frac{(w')^2}{4}(\\frac{1}{w} + \\frac{1}{4}) + \\frac{w''}{2} ≥ 0\n"
            "Calendário (LAW-107A): T1 < T2 => w(k, T1) ≤ w(k, T2)."
        ),
        practical_example=(
            "Ao estruturar um derivativo exótico sobre Dólar de 1 ano, a mesa de câmbio da B3 interpola a superfície SABR "
            "garantindo que nenhuma opção intermediária permita montar uma trava de borboleta com ganho certo e risco zero."
        ),
        diagram_ascii=(
            "Volatilidade Implícita (σ)\n"
            "  ^\n"
            "  |        *                        *   <--- Sorriso de Volatilidade (Smile / Skew)\n"
            "  |         *                      * \n"
            "  |          *                    *  \n"
            "  |            *       ATM      *    \n"
            "  |              *-------------*     \n"
            "  +-------------------------------------> Moneyness k = ln(K/F)\n"
            "     Put OTM (Deep)         Call OTM"
        ),
        related_flows=["FL-039", "FL-042", "FL-101"],
        related_laws=["LAW-107A", "LAW-107B"],
    ),
    B3Concept(
        id="algorithmic-sor",
        category="EXECUÇÃO & ALGORITMOS",
        title="Algorithmic Smart Order Router (TWAP, VWAP, POV) & Jitter",
        badge="LAW-110",
        summary="Fatiamento algorítmico institucional com jitter anti-predação e conservação estrita de quantidade.",
        what_is=(
            "Motores algorítmicos que subdividem ordens-mãe de grande porte institucional em dezenas de fatias menores "
            "ao longo do dia de negociação para minimizar o impacto de mercado."
        ),
        why_exists=(
            "Se um fundo tentar comprar 1 milhão de ações a mercado de uma só vez, limpará o livro, pagará preços exorbitantes "
            "(slippage brutal) e será predado por algoritmos HFT que detectam ordens massivas."
        ),
        how_it_works=[
            "TWAP (Time-Weighted): divide a ordem em N janelas temporais iguais, inserindo jitter estocástico aleatório nos horários.",
            "VWAP (Volume-Weighted): pondera o fatiamento pela curva histórica em U da B3 (mais volume na abertura e fechamento).",
            "Arredondamento de Hare-Niemeyer: garante que a soma dos lotes padrão de 100 ações bata com 100% de exatidão na ordem-mãe.",
            "POV (Percent of Volume): limita a participação do robô a no máximo 10% a 20% do volume real transacionado.",
        ],
        math_rule=(
            "Conservação Estrita de Quantidade Institucional (LAW-110):\n"
            "∑_{i=1}^N Qtd_Fatia_i ≡ Qtd_Ordem_Mãe\n"
            "Preço_Compra_Fatia_i ≤ Preço_Limite_Mãe (Para ordens de compra)\n"
            "Preço_Venda_Fatia_i ≥ Preço_Limite_Mãe (Para ordens de venda)."
        ),
        practical_example=(
            "Para comprar 100.000 ações de PETR4 com limite de R$ 34,80, o VWAP divide em 10 fatias proporcionais à curva do pregão, "
            "executando com preço médio de R$ 34,58 e gerando economia de R$ 22.000 em relação à execução descuidada."
        ),
        diagram_ascii=(
            "Volume B3\n"
            "  ^\n"
            "  |  ██                                 ██   <--- Curva em U da B3 (VWAP Profile)\n"
            "  |  ██   ██                       ██   ██\n"
            "  |  ██   ██   ██             ██   ██   ██\n"
            "  |  ██   ██   ██   ██   ██   ██   ██   ██\n"
            "  +-----------------------------------------> Tempo (10h às 17h)\n"
            "    Abertura        Almoço         Fechamento"
        ),
        related_flows=["FL-011", "FL-049", "FL-104"],
        related_laws=["LAW-110"],
    ),
    B3Concept(
        id="bsm-surveillance",
        category="VIGILÂNCIA & COMPLIANCE",
        title="Vigilância de Mercado BSM: Detecção de Wash Trading e Spoofing",
        badge="LAW-024 / Resolução CVM 35",
        summary="A auditoria contínua de livros L2/L3 com análise de OTR (Order-to-Trade Ratio) para punir manipulação de preços.",
        what_is=(
            "O sistema analítico autônomo da BSM Supervisão de Mercados que monitora 100% das mensagens FIX, ofertas inseridas, "
            "alteradas e canceladas para identificar padrões fraudulentos."
        ),
        why_exists=(
            "Garante a integridade dos livros de ofertas, prevenindo que fraudadores iludam os participantes com falsa liquidez "
            "ou gerem volumes artificiais para valorizar ativos de baixa liquidez."
        ),
        how_it_works=[
            "Detecção de Wash Trading: negócios onde o titular final (CPF/CNPJ) ou grupo econômico é o mesmo em ambas as pontas.",
            "Detecção de Spoofing / Layering: participante envia ordens gigantescas para pressionar o book e cancela antes da execução.",
            "Order-to-Trade Ratio (OTR): razão entre ofertas enviadas/canceladas e negócios executados; desvios extremos geram alerta.",
            "Auditoria de Leilão de Fechamento MOC: monitoramento de front-running e manipulação deliberada do PTP.",
        ],
        math_rule=(
            "Fórmula de Order-to-Trade Ratio (OTR):\n"
            "OTR_Volume = \\frac{Volume_Inserido + Volume_Alterado + Volume_Cancelado}{Volume_Efetivamente_Executado}\n"
            "Se OTR > Teto_BSM (ex: 20x a 50x) e Taxa_Cancelamento > 95% => Alerta de Spoofing Imediato."
        ),
        practical_example=(
            "Um operador coloca 500 lotes de compra no mini-dólar e cancela 80 milissegundos depois para simular suporte e "
            "vender contratos mais caro. O motor BSM identifica o cancelamento em cascata e abre processo punitivo disciplinar."
        ),
        diagram_ascii=(
            "LIVRO DE OFERTAS:                  COMPORTAMENTO DETECTADO (BSM):\n"
            "Venda: 5.120 (100)                 1. Insere 1.000 lotes na compra a 5.118 (falso suporte)\n"
            "Venda: 5.119 (50)                  2. Mercado sobe para 5.120\n"
            "------------------- SPREAD         3. Vende 10 lotes a 5.120 (executa venda real)\n"
            "Compra: 5.118 (1.000) <--- SPOOF!  4. Cancela os 1.000 lotes em 40ms\n"
            "Compra: 5.117 (50)                 ===> ALERTA BSM: SPOOFING DETECTADO!"
        ),
        related_flows=["FL-024", "FL-030", "FL-041", "FL-091"],
        related_laws=["LAW-024", "LAW-041"],
    ),
]


class B3ConceptsCatalog:
    """Gerenciador e provedor do acervo pedagógico de conceitos complexos da B3."""

    def __init__(self) -> None:
        self._concepts_map: Dict[str, B3Concept] = {c.id: c for c in CONCEPTS}

    def list_all(self) -> List[Dict[str, Any]]:
        return [c.to_dict() for c in CONCEPTS]

    def get_by_id(self, concept_id: str) -> Optional[B3Concept]:
        return self._concepts_map.get(concept_id.lower().strip())

    def filter_by_category(self, category: str) -> List[B3Concept]:
        cat_clean = category.upper().strip()
        return [c for c in CONCEPTS if cat_clean in c.category.upper()]

    def search(self, query: str) -> List[B3Concept]:
        q = query.lower().strip()
        if not q:
            return list(CONCEPTS)
        return [
            c
            for c in CONCEPTS
            if q in c.id.lower()
            or q in c.title.lower()
            or q in c.summary.lower()
            or q in c.category.lower()
            or any(q in law.lower() for law in c.related_laws)
        ]
