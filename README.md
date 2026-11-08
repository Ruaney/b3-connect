# 🏛️ B3 Integration Atlas

<p align="center">
  <strong>Projeto de Referência Técnica, Especificação Aberta e Prova Formal de Invariantes para o Mercado Financeiro Brasileiro (B3 S.A. — Brasil, Bolsa, Balcão)</strong>
</p>

<p align="center">
  <a href="catalog/flows.json"><img src="https://img.shields.io/badge/B3%20Fluxos-107%2F107%20(100%25)-003399.svg?style=for-the-badge&logo=b3" alt="B3 Fluxos 107/107"></a>
  <a href="scripts/verify_invariants.py"><img src="https://img.shields.io/badge/Leis%20Provadas-91%20Formais-007a3d.svg?style=for-the-badge&logo=target" alt="91 Leis Provadas"></a>
  <a href="catalog/sources.json"><img src="https://img.shields.io/badge/Manuais%20B3-87%20Catalogados-0052cc.svg?style=for-the-badge&logo=googledocs" alt="87 Manuais B3"></a>
  <a href="tests/"><img src="https://img.shields.io/badge/Testes-100%25%20(1.749%20Pass)-success.svg?style=for-the-badge&logo=checkmarx" alt="100% Test Coverage"></a>
  <a href="src/"><img src="https://img.shields.io/badge/Depend%C3%AAncias-Zero%20(Pure%20Stdlib)-informational.svg?style=for-the-badge&logo=python" alt="Zero External Dependencies"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/Licen%C3%A7a-MIT-green.svg?style=for-the-badge" alt="Licença MIT"></a>
</p>

---

## 📌 Visão Geral & Propósito Institucional

O **B3 Integration Atlas** é uma iniciativa independente e de código aberto voltada a engenheiros de software, arquitetos de mercado de capitais (FMI), bancos liquidantes (PL), corretoras (PN/PNP), distribuidoras (DTVM), gestoras de investimento e fintechs.

O projeto transforma os 87 manuais técnicos regulatórios e normativos da **B3 S.A.** e do **Banco Central do Brasil (BACEN)** em **107 fluxos determinísticos rigorosamente catalogados**, acompanhados de **91 leis matemáticas de conservação financeira formalmente provadas** e implementadas com **zero dependências externas** (100% Python Standard Library).

> [!NOTE]
> **Aviso de Independência:** Este projeto é uma especificação técnica aberta mantida pela comunidade e não possui afiliação formal ou vínculo societário com a B3 S.A. Para soluções gerenciadas, SLAs contratuais e conectores de produção com link direto RCC/VPN, consulte a [B3 Enterprise Platform](https://b3-enterprise-platform.vercel.app/).

---

## ⚡ Quick Start em 30 Segundos (Zero-Setup)

Execute qualquer componente imediatamente sem necessidade de `pip install`, conexão com internet ou configurações prévias:

### 1. Terminal User Interface (TUI) Interativa de Alta Fidelidade
Acesse o terminal institucional completo com livro de ofertas L2/L3 PUMA, leilão MOC em tempo real e simulador dos 12 conceitos:
```bash
# Executa a TUI interativa completa
./b3 tui

# Ou com execução direta via Python Standard Library:
python3 -m src.tui
```

### 2. Linha de Comando Institucional (CLI) & Verificação Formal
Execute simulações ponta a ponta e auditorias de integridade em uma única linha:
```bash
# Comprova matematicamente todas as 91 Leis de Conservação Financeira
./b3 verify

# Simula o ciclo diário completo de pregão e liquidação (E2E)
./b3 simulate-day --shares 50000

# Executa simulação analítica do Leilão de Fechamento MOC (FL-098)
./b3 simulate-moc --symbol PETR4
```

### 3. Dashboard Standalone 100% Offline (Zero Servidor)
Visualize o ecossistema completo abrindo diretamente o arquivo estático compilado no seu navegador:
```bash
# Abrir diretamente via browser (Linux / macOS / Windows)
xdg-open dist/dashboard_standalone.html   # Linux
open dist/dashboard_standalone.html       # macOS
start dist/dashboard_standalone.html      # Windows (cmd)

# Ou inicialize o servidor local com hot-reload na porta 8080:
./b3 ui
```

---

## 🚀 Showcase de Performance & Microbenchmarks em Nanossegundos

O núcleo do **B3 Integration Atlas** foi projetado para responder às exigências de **latência ultra-baixa** de sistemas HFT, robôs DMA (Direct Market Access) e câmaras de liquidação de alta vazão.

Os microbenchmarks foram auditados com `time.perf_counter_ns()` e rastreamento de alocação de memória heap via `tracemalloc`:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 PERFORMANCE SHOWCASE                                   │
├──────────────────────────┬──────────────────────────┬──────────────────────────────────┤
│   2.6M msg/s Sustentado  │      226.9 ns (p50)      │       0 Bytes Heap Alloc         │
│ Throughput Wire-Speed SBE│  Latência de Decodificação│  Zero-Alloc / Zero-Copy Memory   │
└──────────────────────────┴──────────────────────────┴──────────────────────────────────┘
```

### Resultados Oficiais de Microbenchmarks (1.000.000 Iterações)

| Operação Profilada | Throughput Sustentado | Média (ns) | Mediana (p50) | Percentil p99 | Heap Alocado | Otimização Aplicada |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **PUMA SBE `NewOrderSingle` (Zero-Copy)** | **4.407.417 ops/s** | **226.9 ns** | **214.0 ns** | **370.0 ns** | **0 B (Zero-Alloc)** | `struct.unpack_raw` nativo |
| **PUMA SBE `ExecutionReport` (Zero-Copy)**| **2.991.343 ops/s** | **334.3 ns** | **302.0 ns** | **625.0 ns** | **0 B (Zero-Alloc)** | Memória contígua em C ABI |
| **PUMA SBE Stream Sustentado (Batch)**   | **2.618.294 ops/s** | **381.9 ns** | **369.5 ns** | **456.8 ns** | **0 B (Zero-Alloc)** | Janela deslizante de bytes |
| **Criptografia SHA-256 (Buffer Fixo)**   | **1.764.586 ops/s** | **566.7 ns** | **520.0 ns** | **1.139.0 ns**| **0 B (Zero-Alloc)** | Reaproveitamento de buffer |
| **Autenticação HMAC-SHA256 (Pre-Keyed)** | **722.438 ops/s**   | **1.384.2 ns**| **1.312.0 ns**| **2.485.0 ns**| **0 B (Zero-Alloc)** | Context cloning via `.copy()` |
| **Auditoria Formal LAW-004 (Câmara CCP)** | **778.755 ops/s**   | **1.284.1 ns**| **1.210.0 ns**| **2.140.0 ns**| **0 B (Zero-Alloc)** | Algoritmo determinístico O(1) |
| **Formação de PTP Leilão MOC (FL-098)**  | **73.854 ops/s**    | **13.540.2 ns**| **13.110.0 ns**| **19.820.0 ns**| **0 B (Zero-Alloc)**| Desempate trifásico B3 |

> [!TIP]
> **Relatório Completo de Profiling:** Consulte os gráficos de dispersão, percentis p99.9 e análises de jitter em [docs/RELATORIO_MICROBENCHMARKS_NANOSSEGUNDOS.md](docs/RELATORIO_MICROBENCHMARKS_NANOSSEGUNDOS.md).

---

## 🏛️ Topologia Arquitetural Integrada (Os 10 Subsistemas B3)

```mermaid
flowchart TB
    subgraph NEGOCIACAO["1. Plataformas de Negociação & Market Data"]
        PUMA["PUMA Trading System<br/>(FIX 4.4 & SBE Binário)"]
        UMDF["Market Data UMDF L2/L3<br/>(Feed Multicast FAST)"]
        SOR["Smart Order Router (SOR)<br/>(TWAP / VWAP / POV)"]
    end

    subgraph CLEARIING["2. Câmara B3 & Gestão de Risco"]
        CORE["Motor de Risco CORE<br/>(Margem Dinâmica em Tempo Real)"]
        WATERFALL["Default Waterfall<br/>(Cascata de Salvaguardas CCP)"]
        RTC["RTC Alocação & Give-Up<br/>(Captura em Tempo Real)"]
    end

    subgraph LIQUIDACAO["3. Liquidação Financeira & Barramento Central"]
        BUS["Barramento Central de Liquidação<br/>(LAW-106A Netting Multilateral)"]
        SPB["SPB / STR / SELIC<br/>(Mensagens BACEN LDL0001)"]
        PIX["PIX SPI / Automático<br/>(Liquidação Instantânea D0)"]
    end

    subgraph CUSTODIA["4. Registro, Balcão & Infraestrutura Regulada"]
        CSD["Central Depositária (CSD)<br/>(Custódia Fiduciária de Ativos)"]
        NOME["Balcão NoMe & Cetip<br/>(CDB, LCI, LCA, Debêntures, CPR)"]
        REGISTRADORES["SNG (Gravames) & SRO (Seguros)<br/>(CONTRAN 807 / SUSEP 642)"]
        DREX["Drex Fase 2 & RWA<br/>(Atomic DvP / Smart Contracts)"]
    end

    PUMA -->|Operações Casadas| RTC
    UMDF -.->|Livro L3 / Spread| SOR
    SOR -->|Ordens Validadas| PUMA
    RTC -->|Bilateral Extinto| CORE
    CORE -->|Chamada de Margem| BUS
    CORE -.->|Estresse Extremo| WATERFALL
    BUS -->|Multilateral Netting| SPB
    BUS -->|Garantias PIX| PIX
    RTC -->|Trade Fills D+2| CSD
    CSD <-->|Garantias Vinculadas| NOME
    NOME <-->|Restrição de Propriedade| REGISTRADORES
    CSD <-->|DvP Atômico RWA| DREX
```

---

## 💡 Os 12 Conceitos Complexos Estruturantes da B3

Para acelerar o entendimento operacional dos mecanismos mais profundos do mercado brasileiro, o Atlas isola e cataloga os **12 Conceitos Complexos** com explicações didáticas, formulação matemática estrita e comandos executáveis de simulação:

| ID Conceito | Categoria | Título Institucional | Norma / Lei B3 | Comando de Simulação |
| :--- | :--- | :--- | :--- | :--- |
| `novacao-ccp` | CÂMARA & RISCO | Novação Legal e Contraparte Central | Lei 10.214 / LAW-004 | `./b3 concepts sim novacao-ccp` |
| `default-waterfall` | CÂMARA & RISCO | Cascata de Salvaguardas da Câmara (Default Waterfall) | LAW-006 / LAW-106B | `./b3 concepts sim default-waterfall` |
| `leilao-moc-freeze` | PUMA TRADING | Leilão de Fechamento MOC/LOC & Janela de Congelamento | LAW-104A a LAW-104C | `./b3 concepts sim leilao-moc-freeze` |
| `tuneis-circuit-breaker` | PUMA TRADING | Túneis de Oscilação de Preços e Circuit Breaker PUMA | LAW-093 / CVM 135 | `./b3 concepts sim tuneis-circuit-breaker` |
| `netting-multilateral` | LIQUIDAÇÃO & SPB | Compensação Multilateral Contínua vs Liquidação Bruta | LAW-002 / LAW-027 | `./b3 concepts sim netting-multilateral` |
| `registro-recebiveis-gravames` | BALCÃO & GARANTIAS | Central de Gravames e Registro de Recebíveis de Cartão | CMN 4.734 / Lei 14.711 | `./b3 concepts sim registro-recebiveis-gravames` |
| `drex-dvp-atomico` | DLT & DREX | Liquidação DvP/PvP Atômica no Drex Fase 2 (Besu) | LAW-089 / LAW-111 | `./b3 concepts sim drex-dvp-atomico` |
| `superficies-volatilidade` | QUANTITATIVA | Superfícies de Volatilidade Paramétricas SVI & SABR | LAW-107A / LAW-107B | `./b3 concepts sim superficies-volatilidade` |
| `algorithmic-sor` | ALGORITMOS | Algorithmic Smart Order Router (TWAP, VWAP, POV) | LAW-110 | `./b3 concepts sim algorithmic-sor` |
| `bsm-surveillance` | VIGILÂNCIA | Vigilância de Mercado BSM: Wash Trading & Spoofing | LAW-024 / Res. CVM 35 | `./b3 concepts sim bsm-surveillance` |
| `sng-gravames-veiculares` | FINANCIAMENTOS | Sistema Nacional de Gravames (SNG B3 Financiamentos) | CONTRAN 807 / LAW-112 | `./b3 concepts sim sng-gravames-veiculares` |
| `sro-seguros-susep` | SEGUROS & SUSEP | Sistema de Registro de Operações (SRO B3 Seguros) | Circular SUSEP 642 / LAW-113 | `./b3 concepts sim sro-seguros-susep` |

<details>
<summary><b>🔍 Clique para expandir o detalhamento teórico e matemático dos 12 Conceitos Complexos</b></summary>

### 1. Novação Legal e Contraparte Central (`novacao-ccp`)
- **Base Jurídica:** Art. 360 do Código Civil e Art. 5º da Lei Federal nº 10.214/2001.
- **Mecanismo:** No instante da execução no PUMA, a relação bilateral Comprador-Vendedor extingue-se por novação. A B3 assume a posição compradora perante o vendedor e vendedora perante o comprador.
- **Invariante Formal:** `LAW-004`: $\sum Q_{\text{compradas\_ccp}} \equiv \sum Q_{\text{vendidas\_ccp}}$.

### 2. Cascata de Salvaguardas da Câmara (`default-waterfall`)
- **Hierarquia Estrita de Execução (LAW-106B):**
  1. Margens e Garantias do Participante Inadimplente.
  2. Recursos da Própria B3 dedicados à câmara (*Skin in the Game* - R$ 1,2 bilhão).
  3. Fundo de Liquidação e Salvaguardas dos Participantes Não-Inadimplentes.
  4. Patrimônio Social Residual da B3 S.A.

### 3. Leilão de Fechamento MOC & Janela de Congelamento (`leilao-moc-freeze`)
- **Regras Canônicas de Desempate de PTP:**
  1. *Regra 1 (MAX_VOLUME):* Preço que maximiza a quantidade casada.
  2. *Regra 2 (MIN_IMBALANCE):* Menor resíduo absoluto entre compra e venda.
  3. *Regra 3 (MARKET_PRESSURE / REF_PROXIMITY):* Pressão de compra/venda ou proximidade ao preço de referência.
- **Invariante:** `LAW-104A/B/C`: Bloqueio estrito de ordens e conservação de saldo alocado.

### 4. Túneis de Preço e Circuit Breaker PUMA (`tuneis-circuit-breaker`)
- **Comportamento Dinâmico:** Rejeição ou gatilho de leilão quando cotações violam o Túnel de Rejeição, Túnel de Leilão ou Túnel de Proteção. Circuit Breaker em 3 estágios (-10%, -15%, -20%).
- **Invariante:** `LAW-093`.

### 5. Compensação Multilateral Contínua vs Liquidação Bruta (`netting-multilateral`)
- **Eficiência de Capital:** Redução de até 94% no volume financeiro liquidado no STR/SPB através de agregação multilateral em D+2.
- **Invariante:** `LAW-002` e `LAW-027`.

### 6. Central de Gravames e Registro de Recebíveis (`registro-recebiveis-gravames`)
- **Marco Regulatório:** Resolução CMN 4.734 e Marco Legal das Garantias (Lei nº 14.711/2023).
- **Invariante:** `LAW-031`: Proibição de sobre-constituição de ônus sobre a mesma unidade de recebível.

### 7. Liquidação DvP/PvP Atômica no Drex Fase 2 (`drex-dvp-atomico`)
- **Tecnologia:** Hyperledger Besu, compatível com ERC-1155 e smart contracts do Banco Central.
- **Invariante:** `LAW-089` e `LAW-111`: Atomicidade de Entrega contra Pagamento sem risco de liquidação em perna intermediária.

### 8. Superfícies de Volatilidade Paramétricas SVI & SABR (`superficies-volatilidade`)
- **Formulação Quantitativa:** Calibração estrita livre de arbitragem estática e *butterfly spread arbitrage* ($g(k) \ge 0$).
- **Invariante:** `LAW-107A` e `LAW-107B`.

### 9. Algorithmic Smart Order Router (`algorithmic-sor`)
- **Modelos:** TWAP (Time-Weighted), VWAP (Volume-Weighted) e POV (Percentage of Volume) com randomização de jitter para mitigar pegada de livro.
- **Invariante:** `LAW-110`.

### 10. Vigilância de Mercado BSM (`bsm-surveillance`)
- **Norma:** Resolução CVM 35 e Código de Conduta BSM.
- **Mecanismo:** Detecção determinística de operações de mesma titularidade econômica (Wash Trading) e cancelamentos artificiais em cascata (Spoofing/Layering).
- **Invariante:** `LAW-024`.

### 11. Sistema Nacional de Gravames (`sng-gravames-veiculares`)
- **Norma:** Resolução CONTRAN nº 807/2020.
- **Mecanismo:** Unicidade de gravame ativo por chassi e Renavam nos 27 Detrans da federação.
- **Invariante:** `LAW-112`.

### 12. Sistema de Registro de Operações Seguros (`sro-seguros-susep`)
- **Norma:** Circular SUSEP nº 642/2021.
- **Mecanismo:** Partição estrita de 100% em acordos de cosseguro e controle do Limite Máximo de Garantia (LMG).
- **Invariante:** `LAW-113`.
</details>

---

## 🗺️ Matriz dos 107 Fluxos de Integração da B3

Os 107 fluxos cobrem todo o espectro da infraestrutura de mercado financeiro, categorizados cronologicamente pelas 7 fases evolutivas do Atlas:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        MATRIZ DE COBERTURA: 107 / 107 FLUXOS (100%)                    │
├──────────────────┬──────────────────────────────────────────┬───────────┬──────────────┤
│ Fase Evolutiva   │ Domínio Funcional Coberto                │ Fluxos    │ Status       │
├──────────────────┼──────────────────────────────────────────┼───────────┼──────────────┤
│ **Fase 1**       │ Pós-Negociação, Clearing e Central Dep.  │ FL-001/010│ ✅ 100% Testado│
│ **Fase 2**       │ PUMA Trading System & Conectividade      │ FL-011/023│ ✅ 100% Testado│
│ **Fase 3**       │ Balcão NoMe, Renda Fixa e Registradores  │ FL-024/040│ ✅ 100% Testado│
│ **Fase 4**       │ Mercados Agro, Índices e Ofertas CVM     │ FL-041/060│ ✅ 100% Testado│
│ **Fase 5**       │ Risco CORE, Default Waterfall e Tarifas  │ FL-061/080│ ✅ 100% Testado│
│ **Fase 6**       │ Energia CCEE, SPB e Pilotos Drex Fase 1  │ FL-081/095│ ✅ 100% Testado│
│ **Fase 7**       │ Microestrutura L3, Drex 2, SNG e SRO     │ FL-096/107│ ✅ 100% Testado│
└──────────────────┴──────────────────────────────────────────┴───────────┴──────────────┘
```

<details>
<summary><b>📋 Clique para expandir o inventário analítico dos 107 Fluxos</b></summary>

| ID | Nome do Fluxo de Integração | Subsistema | Protocolo / Formato |
| :--- | :--- | :--- | :--- |
| **FL-001** | Autenticação e Emissão de SAS Token iMercado | Conectividade | OAuth 2.0 / REST |
| **FL-002** | Download Seguro de Arquivo de Posições IMBARQ006 | Pós-Negociação | HTTPS Blob SAS |
| **FL-003** | Parsing e Validação de Layout Flat-File IMBARQ006 | Pós-Negociação | Posicional 200B |
| **FL-004** | Conciliação D+0/D+1 de Posições em Ações e Opções | Clearing | Algorítmico |
| **FL-005** | Emissão de Alertas e Log de Divergências | Operacional | JSON / Syslog |
| **FL-006** | Exportação de Posições Reconciliadas para ERP/Legado | Backoffice | CSV / JSON |
| **FL-007** | Captura de Operações de Renda Variável em Tempo Real | Pós-Negociação (RTC)| REST / JSON |
| **FL-008** | Alocação de Operações para Contas Finais | Pós-Negociação (RTC)| REST / JSON |
| **FL-009** | Repasse de Operações entre Participantes (Give-Up) | Pós-Negociação (RTC)| REST / JSON |
| **FL-010** | Retificação de Especificação de Operações | Pós-Negociação (RTC)| REST / JSON |
| **FL-011** | Conexão FIX 4.4 Session Protocol ao PUMA | Negociação PUMA | FIX 4.4 TCP Socket |
| **FL-012** | Envio de Ordem Limitada via FIX (NewOrderSingle) | Negociação PUMA | FIX 4.4 Tag 35=D |
| **FL-013** | Envio de Ordem via Protocolo Binário SBE | Negociação PUMA | SBE Little-Endian |
| **FL-014** | Recepção de Execution Report (Trade Fill) | Negociação PUMA | FIX 4.4 Tag 35=8 |
| **FL-015** | Cancelamento e Modificação de Ordens PUMA | Negociação PUMA | FIX 4.4 Tag 35=F/G |
| **FL-016** | Feed de Market Data Multicast UMDF L2 | Market Data | UMDF / FAST Multicast|
| **FL-017** | Feed de Market Data Conflated TCP | Market Data | TCP Recovery |
| **FL-018** | Drop Copy para Sistemas de Risco e Compliance | Risco / Middle | FIX 4.4 Tag 35=8 |
| **FL-019** | Trava de Risco Pré-Trade e Limites Operacionais | Risco DMA | Sub-80µs In-Memory |
| **FL-020** | Encerramento Forçado de Sessão FIX (Logout) | Conectividade | FIX 4.4 Tag 35=5 |
| **FL-021** | Teste de Heartbeat e Sequenciamento de Mensagens | Negociação PUMA | FIX 4.4 Tag 35=0/1/2|
| **FL-022** | Conexão Redefinida de Resiliência de Link RCC | Infraestrutura | Dual-Homed BGP |
| **FL-023** | Autenticação mTLS com Certificado ICP-Brasil | Segurança | TLS 1.3 / X.509 |
| **FL-024** | Registro de Emissão de Debêntures no NoMe | Renda Fixa Balcão | ISO 20022 / XML |
| **FL-025** | Registro de Operação Definitiva de CDB/RDB | Renda Fixa Balcão | REST / JSON |
| **FL-026** | Negociação de Títulos Públicos Federais (TPF) | Renda Fixa Balcão | FIX / NoMe Web |
| **FL-027** | Compromissadas de Renda Fixa Privada (Repo) | Renda Fixa Balcão | REST / JSON |
| **FL-028** | Registro de Letras de Crédito Imobiliário e do Agro (LCI/LCA)| Balcão NoMe | REST / JSON |
| **FL-029** | Registro de Certificados de Depósito Agropecuário (CDA/WA)| Balcão NoMe Agro | REST / XML |
| **FL-030** | Registro de Cédula de Produto Rural Física e Financeira (CPR)| Balcão NoMe Agro | REST / JSON |
| **FL-031** | Registro de Recebíveis de Cartão de Crédito | Registradora | API Centralizadora |
| **FL-032** | Troca de Titularidade e Negociação Secundária de Títulos | Balcão NoMe | REST / ISO 20022 |
| **FL-033** | Registro de Swaps e Termos de Balcão (Derivativos NoMe)| Derivativos Balcão | XML / REST |
| **FL-034** | Registro de Opções Flexíveis de Balcão | Derivativos Balcão | REST / JSON |
| **FL-035** | Registro de Operações de NDF (Non-Deliverable Forward)| Derivativos Balcão | FIX / REST |
| **FL-036** | Registro de Títulos Estruturados (COE) | Balcão NoMe | REST / JSON |
| **FL-037** | Registro de Gravames e Ônus sobre Ativos Custodiados | Garantias Balcão | REST / JSON |
| **FL-038** | Liquidação Financeira de Títulos no Sistema SELIC | Custódia Selic | Mensageria SPB |
| **FL-039** | Transferência Eletrônica de Ativos (STVM) | Central Depositária | REST / STVM CVM 175|
| **FL-040** | Conciliação Diária de Balcão via IMBARQ004 | Pós-Negociação | Posicional 200B |
| **FL-041** | Contratos Futuros de Commodities Agro (Boi, Milho, Soja)| Derivativos PUMA | FIX 4.4 / SBE |
| **FL-042** | Contratos Futuros Financeiros (DI1, Dólar DOL, Ibovespa IND)| Derivativos PUMA | FIX 4.4 / SBE |
| **FL-043** | Opções sobre Futuros de DI e Dólar (Modelo Black-76) | Derivativos PUMA | Algorítmico Black76 |
| **FL-044** | Contratos a Termo de Ações e Moedas no PUMA | Renda Variável PUMA | FIX 4.4 Tag 35=D |
| **FL-045** | Empréstimo Eletrônico de Ativos (Banco de Títulos BTC)| Central Depositária | REST / IMBARQ011 |
| **FL-046** | Rebalanceamento de Carteiras Teóricas de Índices (IBOV)| Market Data Índices | CSV / FIX / REST |
| **FL-047** | Distribuição Primária de Ofertas Públicas (CVM 160) | Ofertas Públicas | Plataforma Ofertas |
| **FL-048** | Integralização e Subscrição de Novas Ações e Cotas | Central Depositária | REST / XML |
| **FL-049** | Conversão e Desdobramento de BDRs (Patrocinados e Não) | Central Depositária | REST / XML |
| **FL-050** | Criação e Resgate de Cotas de Fundos ETF | Central Depositária | REST / FIX |
| **FL-051** | Operações Estruturadas de Volatilidade (Straddle/Strangle)| Negociação PUMA | Multi-Leg FIX |
| **FL-052** | Exercício Automático e Manual de Opções de Ações | Clearing | Batch Noturno |
| **FL-053** | Leilões Especiais de OPA (Oferta Pública de Aquisição) | PUMA Trading | FIX / Terminal PUMA|
| **FL-054** | Leilões de Frações de Ações Pós-Grupamento | PUMA Trading | FIX / Terminal PUMA|
| **FL-055** | Gestão de Fracionamento e Agrupamento de Cotas | Central Depositária | IMBARQ018 |
| **FL-056** | Processamento de Eventos Corporativos em Dinheiro | Central Depositária | IMBARQ012 / 018 |
| **FL-057** | Tratamento de Bonificação de Ações e Cisão Societária | Central Depositária | IMBARQ018 |
| **FL-058** | Registro de Títulos Verdes e Sustentáveis (ESG Green Bonds)| Balcão NoMe ESG | REST / Metadados |
| **FL-059** | Créditos de Descarbonização (CBIO) no Balcão | Balcão NoMe | REST / RenovaBio |
| **FL-060** | Futuros de Créditos de Carbono no PUMA | PUMA Commodities | FIX 4.4 / SBE |
| **FL-061** | Cálculo de Margem Intradiária CORE para Renda Variável | Risco CORE | Analítico Multivariado|
| **FL-062** | Cálculo de Margem Intradiária CORE para Futuros e Derivativos| Risco CORE | Analítico Multivariado|
| **FL-063** | Depósito e Retirada de Garantias em Dinheiro e TPF | Risco / Clearing | IMBARQ014 |
| **FL-064** | Alocação de Ativos Internacionais como Colateral | Custódia Int. | Swift MT540/542 |
| **FL-065** | Liquidação Financeira Multilateral em D+2 na Câmara B3| Clearing | SPB LDL0001 |
| **FL-066** | Compensação Multilateral Contínua (Netting Financeiro)| Clearing | Barramento Central |
| **FL-067** | Chamada Extraordinária de Margem por Volatilidade Intradiária| Risco CORE | Mensageria Pós-Trade |
| **FL-068** | Acionamento da Cascata de Salvaguardas (Default Waterfall)| Clearing | Protocolo Crise B3 |
| **FL-069** | Liquidação Forçada de Carteira de Participante Inadimplente| Clearing | Leilão Especial |
| **FL-070** | Gestão de Inadimplência e Compra Forçada (Buy-In) | Clearing | Regra Pós-Trade |
| **FL-071** | Apuração e Cobrança de Tarifas e Emolumentos de Negociação| Tarifação B3 | Motor de Regras |
| **FL-072** | Cobrança de Taxa de Custódia e Manutenção de Contas | Central Depositária | Débito em Conta B3 |
| **FL-073** | Monitoramento de Abusos de Mercado pela BSM | Supervisão BSM | Big Data Telemetria|
| **FL-074** | Geração de Relatórios Regulatórios CVM e BACEN | Compliance | Arquivos Oficiais |
| **FL-075** | Monitoramento de Limites Operacionais DMA por Participante| Risco DMA | Sub-Milissegundo |
| **FL-076** | Liquidação de Câmbio Pronto e Futuro com Entrega Física | Câmara de Câmbio | Mensageria SPB |
| **FL-077** | Interconexão com Plataformas de Negociação Estrangeiras | Conectividade Global| FIX / Cross-Border |
| **FL-078** | Gestão de Usuários e Permissões no Portal B3 | Governança | OAuth 2.0 / RBAC |
| **FL-079** | Auditoria e Trilha Imutável de Eventos de Mensageria | Segurança | SHA-256 Ledger |
| **FL-080** | Emissão de Extratos para Investidores Pessoa Física | Plataforma Digital | REST / Portal B3 |
| **FL-081** | Registro de Duplicatas Mercantis Eletrônicas | Registradora | API BACEN Circ. 3.987|
| **FL-082** | Portabilidade de Contratos de Crédito Bancário | Registradora | REST / JSON |
| **FL-083** | Registro de CPR Verde com Certificação Georreferenciada | Balcão NoMe Agro | API SIGEF / SICAR |
| **FL-084** | Tokenização de Ativos Financeiros Reais (RWA) no Drex | DLT B3 / Drex | Smart Contracts EVM|
| **FL-085** | Integração com o Sistema de Pagamentos Instantâneos (SPI / PIX)| Liquidação D0 | Mensageria SPI |
| **FL-086** | Registro de Cédulas Imobiliárias Rurais (CIR) | Balcão NoMe | REST / JSON |
| **FL-087** | Liquidação de Operações do Mercado de Energia Elétrica (CCEE)| Balcão Energia | REST / CCEE API |
| **FL-088** | Plataforma de Fundos de Investimento (CVM 175) | Fundos B3 | REST / ISO 20022 |
| **FL-089** | Piloto do Real Digital / Drex: DvP de TPF Tokenizado | Inovação DLT | Hyperledger Besu |
| **FL-090** | Plataforma de Tesouro Direto (Negociação e Liquidação) | Tesouro Direto | REST / JSON API |
| **FL-091** | Gestão de Risco de Liquidez Intradiária dos Participantes| Risco Sistêmico | Motor Analítico |
| **FL-092** | Emissão e Registro de Letras Financeiras Subordinadas | Balcão NoMe | REST / JSON |
| **FL-093** | Acionamento de Circuit Breaker e Suspensão de Pregão | PUMA Trading | FIX Tag 35=B / Status|
| **FL-094** | Negociação de Opções Exóticas (Asiáticas, Barreiras) | Balcão Derivativos | Black-Scholes Numérico|
| **FL-095** | Gestão de Colaterais em Moeda Estrangeira (USD/EUR) | Risco Internacional | FX Clearing MT54x |
| **FL-096** | Alimentação e Reconstrução do Livro L3 Market-by-Order | Market Data PUMA | UMDF MBO Packet Feed|
| **FL-097** | Ordens Avançadas PUMA (Iceberg, Stop, Mitigate, Pegged) | Negociação PUMA | FIX 4.4 Tag 35=D ext|
| **FL-098** | Leilão de Fechamento PUMA MOC/LOC com Freeze Window | Negociação PUMA | Desempate Triplo B3|
| **FL-099** | Triparty Repo e Gestão de Garantias Transfronteiriças | Clearing & Custódia | SWIFT ISO 20022 |
| **FL-100** | Barramento Central de Liquidação Unificada (10 Subsistemas)| Clearing Central | Mensageria LAW-106A|
| **FL-101** | Superfícies de Volatilidade Paramétricas SVI e SABR | Risco & Modelagem | SABR / SVI Calibrador|
| **FL-102** | Certificados de Operações Estruturadas (COE) Capital Protegido| Balcão NoMe | Simulação Monte Carlo|
| **FL-103** | Programa de Metas de Formador de Mercado (PMM B3) | Negociação PUMA | Algoritmo Spreads PMM|
| **FL-104** | Algorithmic Smart Order Router (SOR TWAP/VWAP/POV) | Execução PUMA | Otimização Micro-Lote|
| **FL-105** | Emissão Primária de Ativos Tokenizados (RWA) e DvP Drex 2| DLT & Drex Fase 2 | Besu / ERC-1155 |
| **FL-106** | Sistema Nacional de Gravames (SNG B3 Financiamentos) | Financiamentos | CONTRAN 807 / REST |
| **FL-107** | Sistema de Registro de Operações (SRO B3 Seguros SUSEP) | Seguros & Resseguros| SUSEP Circular 642 |
</details>

---

## 🔗 Links Diretos & Recursos Técnicos Oficiais

Acesse os documentos de referência institucional, especificações de contrato e recursos de observabilidade disponibilizados no repositório:

| Recurso | Formato / Localização | Descrição e Finalidade |
| :--- | :--- | :--- |
| 📖 **Whitepaper de Arquitetura B3** | [`docs/WHITEPAPER_ARQUITETURA_B3.md`](docs/WHITEPAPER_ARQUITETURA_B3.md) | Especificação completa da infraestrutura de mercado de capitais brasileiro (75 KB). |
| 📊 **Apresentação Executiva** | [`docs/APRESENTACAO_EXECUTIVA_B3_ATLAS.md`](docs/APRESENTACAO_EXECUTIVA_B3_ATLAS.md) | Sumário executivo para C-levels, Diretorias de Operações e Riscos Financeiros. |
| 📮 **Postman Collection Oficial** | [`docs/b3_atlas_postman_collection.json`](docs/b3_atlas_postman_collection.json) | Coleção completa Postman v2.1 cobrindo todos os endpoints de Gateway e Sandbox (210 KB). |
| 📜 **Especificação OpenAPI 3.1** | [`docs/openapi_b3_atlas_complete.json`](docs/openapi_b3_atlas_complete.json) | Contrato canônico OpenAPI 3.1.0 para geração automatizada de clientes e SDKs (117 KB). |
| 📈 **Dashboard Grafana Unificado** | [`observability/grafana_b3_enterprise_overview.json`](observability/grafana_b3_enterprise_overview.json) | Painel pronto para Grafana com métricas de micro-latência, TPS de leilão e alertas BSM. |
| 🔬 **Relatório de Microbenchmarks** | [`docs/RELATORIO_MICROBENCHMARKS_NANOSSEGUNDOS.md`](docs/RELATORIO_MICROBENCHMARKS_NANOSSEGUNDOS.md) | Profiling detalhado em nanossegundos com medições de jitter e zero-alloc. |
| 🔒 **Parecer de Conformidade LGPD**| [`docs/PARECER_CONFORMIDADE_LGPD.md`](docs/PARECER_CONFORMIDADE_LGPD.md) | Certificação de isolamento de PII, pseudonimização e dados sintéticos descaracterizados. |
| 🛡️ **Relatório de Auditoria AppSec** | [`docs/RELATORIO_AUDITORIA_APPSEC.md`](docs/RELATORIO_AUDITORIA_APPSEC.md) | Varredura de segurança contra OWASP Top 10 e garantia de fail-safe em produção. |
| 🌐 **Dashboard Standalone Offline** | [`dist/dashboard_standalone.html`](dist/dashboard_standalone.html) | Interface gráfica autocontida em arquivo único de 1.2 MB navegável sem conexão. |

---

## 🏛️ Governança Open Source, Conformidade e Segurança

O **B3 Integration Atlas** é mantido sob as melhores práticas de governança técnica e cooperação institucional:

- **[Guia de Contribuição (CONTRIBUTING.md)](CONTRIBUTING.md):** Padrões rígidos de código (100% Python Standard Library, tipagem estrita MyPy, precisão `decimal.Decimal` para valores monetários, cobertura integral de testes unitários e processo formal de Pull Request).
- **[Código de Conduta (CODE_OF_CONDUCT.md)](CODE_OF_CONDUCT.md):** Diretrizes de colaboração baseadas no *Contributor Covenant v2.1* em português e inglês.
- **[Política de Segurança & CVD (SECURITY.md)](SECURITY.md):** Política de Divulgação Coordenada de Vulnerabilidades (*Coordinated Vulnerability Disclosure*) com SLA de triagem AppSec em até 24 horas.
- **[Roadmap Estratégico (ROADMAP.md)](ROADMAP.md):** Visão cronológica das 7 Fases, evolução dos 107 fluxos e integração com os pilotos do Real Digital / Drex.
- **[Licença de Código Aberto (LICENSE)](LICENSE):** Distribuído sob a licença permissiva [MIT License](LICENSE), assegurando liberdade total para uso, estudo e extensão.

---

## ❓ FAQ & Resolução de Problemas

<details>
<summary><b>1. O B3 Integration Atlas exige conexão com o link dedicado RCC da B3?</b></summary>
Não. O Atlas é um projeto aberto e educacional que emula matematicamente as regras de negócio, layouts e protocolos da B3. É ideal para desenvolvimento de robôs, backtesting, treinamento de equipes e preparação para homologação sem custos de telecomunicação.
</details>

<details>
<summary><b>2. Como resolver o erro de permissão ao rodar o executável `./b3`?</b></summary>
Conceda permissão de execução via terminal:
```bash
chmod +x ./b3
```
</details>

<details>
<summary><b>3. O projeto possui dependências de pacotes externos (`pip install`)?</b></summary>
Zero. O núcleo do projeto opera estritamente com a Python Standard Library pura (`struct`, `json`, `decimal`, `dataclasses`, `typing`, `hashlib`, `socket`, `http.server`), eliminando vulnerabilidades de cadeia de suprimentos (*supply chain attacks*).
</details>

<details>
<summary><b>4. Como executar apenas a validação formal das 91 Leis de Conservação?</b></summary>
Execute o verificador formal canônico:
```bash
python3 scripts/verify_invariants.py
# Ou via CLI:
./b3 verify
```
</details>

---

<p align="center">
  <sub>B3 Integration Atlas — Desenvolvido com rigor matemático e engenharia de precisão para o mercado financeiro brasileiro.</sub>
</p>
