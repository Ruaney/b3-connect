# Especificação Técnica: B3 CSD & BACEN DREX Fase 2 — Emissão Primária de Ativos RWA em Rede DLT EVM (FL-105)

## 1. Visão Geral da Arquitetura de Tokenização RWA no Drex

O fluxo **FL-105** implementa o motor institucional de originação, bookbuilding, alocação, liquidação atômica primária **Delivery-versus-Payment (DvP)** e gestão automatizada de proventos on-chain para títulos de renda fixa privada tokenizados — **Real World Assets (RWA)** — na rede DLT permissionada do **Piloto Drex Fase 2** (**Hyperledger Besu / EVM**), sob governança conjunta do **Banco Central do Brasil (BACEN)**, da **Comissão de Valores Mobiliários (CVM)** e da **B3 (Central Depositária / CSD)**.

Os instrumentos contemplados incluem:
- **Debêntures**: Títulos corporativos de dívida de médio e longo prazo (simples, conversíveis e incentivadas nos termos da Lei nº 12.431/2006);
- **Certificados de Recebíveis Imobiliários (CRI)**: Lastreados em direitos creditórios do setor imobiliário (Lei nº 9.514/1997 e Resolução CVM 175);
- **Certificados de Recebíveis do Agronegócio (CRA)**: Lastreados em recebíveis de produtores e cooperativas agropecuárias (Lei nº 11.076/2004 e Resolução CVM 175).

```
+-----------------------------------------------------------------------------------------+
|                  BACEN DREX FASE 2 & B3 CSD — ARQUITETURA DLT RWA                       |
+-----------------------------------------------------------------------------------------+
                                             |
             +-------------------------------+-------------------------------+
             |                               |                               |
             v                               v                               v
+--------------------------+   +---------------------------+   +--------------------------+
|  CBDC REAL DIGITAL       |   | ERC-1155 MULTI-TOKEN      |   | ERC-3643 (T-REX)         |
|  - Moeda Soberana BACEN  |   | - Debêntures, CRIs e CRAs |   | - ONCHAINID / Claims     |
|  - Liquidação Atacado    |   | - Tranches: Sênior, Mezz, |   | - KYC / AML Verificado   |
|  - Contas de Reserva IF  |   |   Subordinada             |   | - Elegibilidade CVM 160  |
+--------------------------+   +---------------------------+   +--------------------------+
             |                               |                               |
             +-------------------------------+-------------------------------+
                                             |
                                             v
                      +---------------------------------------------+
                      |   FLUXO DE OFERTA PÚBLICA (CVM 160)         |
                      |   1. Abertura do Livro de Bookbuilding     |
                      |   2. Coleta de Bids com Limite de Taxa      |
                      |   3. Fixação da Taxa de Corte & Pro-Rata    |
                      +---------------------------------------------+
                                             |
                                             v
                      +---------------------------------------------+
                      |   LIQUIDAÇÃO ATÔMICA DVP (LAW-111)          |
                      |   Modalidade A: Direct Atomic DvP           |
                      |   Modalidade B: HTLC Atomic Swap            |
                      +---------------------------------------------+
                                             |
                                             v
                      +---------------------------------------------+
                      |   PROVENTOS ON-CHAIN DETERMINÍSTICOS        |
                      |   - Pagamento de Cupons Periódicos em RD    |
                      |   - Amortização Ordinária de Principal      |
                      |   - Resgate Final e Queima dos Tokens       |
                      +---------------------------------------------+
```

---

## 2. Padrões de Smart Contracts Simulados

### 2.1 ERC-1155 Multi-Token
O padrão **ERC-1155** permite representar múltiplas emissões, séries e tranches de subordinação sob um único contrato inteligente ou coleção parametrizada, otimizando custo de gás e unificando interfaces de consulta:
- **Identificador Único (`token_id`)**: Cada ativo (ou tranche) possui um identificador numérico associado ao ISIN e símbolo B3;
- **Séries e Tranches**:
  - `SENIOR`: Prioridade primária de resgate e cupons;
  - `MEZZANINE`: Subordinação intermediária;
  - `SUBORDINATED`: Absorção prioritária de inadimplência (Equity tranche).
- **Operações**: `mint` (na liquidação primária), `burn` (no resgate total ou estorno de HTLC), `safe_transfer_from` e `balance_of`.

### 2.2 ERC-3643 (T-REX — Token for Regulated EXchanges)
Para assegurar o cumprimento integral das normas da CVM para valores mobiliários na infraestrutura descentralizada, o padrão **ERC-3643** implementa conformidade on-chain estrita:
- **ONCHAINID**: Mapeamento descentralizado da carteira EVM para uma identidade auditável;
- **Identity Claims**: Credenciais criptograficamente assinadas por certificadores autorizados pelo BACEN/CVM:
  - `KYC_APPROVED`: Identificação de cliente concluída;
  - `AML_CLEARED`: Verificação de combate à lavagem de dinheiro e ausência de sanções;
  - `QUALIFIED_INVESTOR`: Certificação de investidor qualificado (patrimônio $\ge$ R$ 1.000.000,00);
  - `PROFESSIONAL_INVESTOR`: Certificação de investidor profissional (patrimônio $\ge$ R$ 10.000.000,00 ou IF).
- **Compliance Rules**: Bloqueio prévio de transferência caso o investidor seja de jurisdição não autorizada, não possua claims válidas ou esteja com a conta judicialmente congelada.

### 2.3 CBDC Real Digital (BACEN)
Contrato ERC-20 compliant operado pelo Banco Central do Brasil para liquidação de atacado:
- Mantém contas de reserva das instituições participantes e carteiras de custódia vinculadas;
- Paridade monetária estrita de $1:1$ com o Real Soberano (BRL).

---

## 3. Livro de Bookbuilding e Rateio Pro-Rata (Resolução CVM 160)

O processo de oferta primária segue o ciclo padronizado pela Resolução CVM 160:

1. **Abertura do Livro**: A oferta é registrada com volume alvo ($Q_{\text{target}}$), Valor Nominal Unitário ($VNU$), descrição de benchmark (ex: IPCA + spread ou % CDI) e limites de taxa de subscrição;
2. **Coleta de Ordens (`BookbuildingBid`)**: Investidores submetem ordens contendo volume desejado e taxa máxima exigida. O Smart Contract valida as credenciais ERC-3643 de cada proponente antes de admitir a ordem no livro;
3. **Determinação da Taxa de Corte ($R_{\text{cut-off}}$)**: As ordens são ordenadas crescentemente por taxa demandada. A taxa de corte é aquela na qual a demanda acumulada atinge ou supera o volume alvo da oferta;
4. **Alocação e Rateio**:
   - Ordens estritamente abaixo da taxa de corte ($R < R_{\text{cut-off}}$) recebem 100% da quantidade solicitada;
   - Ordens na taxa de corte ($R = R_{\text{cut-off}}$) dividem as cotas remanescentes proporcionalmente à demanda marginal via rateio pro-rata:
     $$Q_i = \left\lfloor Q_{\text{remanescente}} \times \frac{D_i}{\sum D_j} \right\rfloor$$
   - Ordens acima da taxa de corte ($R > R_{\text{cut-off}}$) são integralmente rejeitadas.

---

## 4. Modalidades de Liquidação Bilateral DvP

### 4.1 Direct Atomic DvP (Same-Block Execution)
- Realizada em uma única transação atômica na EVM:
  1. O Smart Contract debita o Real Digital da carteira do investidor e credita na conta do emissor;
  2. Concomitantemente, executa o `mint` dos tokens RWA diretamente na carteira do investidor;
  3. Se o investidor não dispuser de saldo suficiente de Real Digital ou reprovar no compliance ERC-3643, a transação reverte integralmente (*revert*).

### 4.2 HTLC Atomic Swap (Hash Time-Locked Contracts)
Para liquidações condicionais entre participantes que necessitam de confirmações bilaterais cruzadas:
1. **Geração do Segredo**: O coordenador gera um segredo criptográfico $S$ e calcula o hashlock $\mathcal{H} = \text{SHA-256}(S)$;
2. **Travamento das Pernas**:
   - O investidor trava o montante de Real Digital no contrato HTLC;
   - O emissor emite os tokens RWA no escrow do contrato;
3. **Resgate (Claim)**: A apresentação da preimage $S$ válida antes do vencimento do timelock libera simultaneamente o Real Digital para o emissor e os tokens RWA para o investidor;
4. **Reversão por Timeout (Refund)**: Caso o timelock expire sem a apresentação do segredo, os fundos em Real Digital retornam ao investidor e os tokens em escrow são queimados/estornados, eliminando o risco de entrega unilateral sem pagamento.

---

## 5. Ciclo de Vida de Proventos On-Chain

Os títulos privados tokenizados possuem suporte nativo à execução de eventos corporativos:

### 5.1 Pagamento de Cupons de Juros (`COUPON_PAYMENT`)
- Na data-base (*record date*), o contrato registra o snapshot de todos os detentores dos tokens via ERC-1155;
- O emissor deposita o montante total de Real Digital necessário na conta escrow do evento;
- A execução distribui automaticamente os proventos para cada carteira proporcionalmente ao saldo de tokens detido:
  $$\text{Valor Pago}_i = Q_i \times \text{Cupom Unitário}$$

### 5.2 Amortização de Principal (`PRINCIPAL_AMORTIZATION`)
- Ocorre pagamento de fração do principal aos investidores;
- O Valor Nominal Unitário atualizado ($VNU_{\text{atual}}$) é reduzido proporcionalmente no contrato inteligente:
  $$VNU_{\text{novo}} = VNU_{\text{anterior}} - \text{Amortização Unitária}$$

### 5.3 Resgate Total no Vencimento (`TOTAL_REDEMPTION`)
- Pagamento integral do principal remanescente aos investidores;
- Queima (*burn*) de 100% dos tokens circulantes na rede e encerramento da oferta com status `MATURED`.

---

## 6. Invariantes Formais Provadas

### Lei 84 / LAW-111: Atomicidade da Liquidação Primária DvP e Conservação de Cupons RWA
1. **Atomicidade Quantitativa**:
   $$\forall \text{ liquidação } i: \quad Q_{\text{alocada}, i} = Q_{\text{entregue}, i}$$
2. **Paridade Financeira DvP**:
   $$\forall \text{ liquidação } i: \quad V_{\text{recebido}, i} = Q_{\text{alocada}, i} \times P_{\text{unitário}} = V_{\text{esperado}, i}$$
3. **Conservação de Saldo de Cupons/Amortizações (Zero-Leakage)**:
   $$\forall \text{ evento } e: \quad V_{\text{provisionado}, e} = \sum_{j=1}^{N} V_{\text{pago}, j, e}$$
   Nenhum centavo de Real Digital pode ser retido ou perdido durante o repasse de proventos.

### Invariantes Auxiliares Integradas
- **LAW-089C**: Não-negatividade absoluta de saldos em todas as carteiras e contratos ($\forall c, \text{Saldo}(c) \ge 0$);
- **ERC-3643**: Inviolabilidade do controle regulatório — nenhuma carteira sem KYC/AML ou perfil inadequado pode subscrever ou receber tokens RWA.

---

## 7. Exemplos de Carga e Uso

```python
from decimal import Decimal
from src.drex_rwa_primary_issuance import (
    DrexRwaPrimaryIssuanceEngine,
    InvestorProfile,
    RwaAssetType,
    RwaTranche,
)

# Inicializa o motor
engine = DrexRwaPrimaryIssuanceEngine()

# Cadastra investidores com compliance ERC-3643
w_alice = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
engine.register_investor(w_alice, "123.456.789-00", InvestorProfile.QUALIFIED)
engine.cbdc.mint(w_alice, Decimal("50000000.00"))

# Criação da oferta de Debênture RWA
offering = engine.create_offering(
    offering_id="OFF-DEB-2026-001",
    asset_symbol="DEB-VALE-2026",
    isin="BRVALEDBS015",
    asset_type=RwaAssetType.DEBENTURE,
    tranche=RwaTranche.SENIOR,
    token_id=1001,
    target_units=50000,
    vnu_nominal=Decimal("1000.00"),
    issuer_wallet="0x1111111111111111111111111111111111111111",
    underwriter_wallet="0x2222222222222222222222222222222222222222",
    escrow_wallet="0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    benchmark_description="100% CDI + 1.25%",
    min_rate=Decimal("1.00"),
    max_rate=Decimal("1.80"),
)

# Processo de Bookbuilding e Alocação
engine.open_bookbuilding("OFF-DEB-2026-001")
engine.submit_bid("OFF-DEB-2026-001", "BID-01", w_alice, 50000, Decimal("1.25"))
allocs = engine.close_and_price_bookbuilding("OFF-DEB-2026-001")

# Liquidação Atômica DvP Direta
receipt = engine.settle_direct_atomic_dvp("OFF-DEB-2026-001", allocs[0].allocation_id)

# Auditoria Formal de Invariantes
results = engine.verify_invariants()
assert results["LAW-111_primary_dvp_atomic_quantity"] is True
```
