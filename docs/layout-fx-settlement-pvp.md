# Especificação Técnica: Câmara de Câmbio B3 — Liquidação Atômica PvP (FL-060)

## 1. Visão Geral e Princípios Basileia / CPMI-IOSCO

A **Câmara de Câmbio B3** atua como contraparte central e agente de compensação multilateral para o mercado de câmbio interbancário e balcão no Brasil. O mecanismo de **Payment versus Payment (PvP)** é projetado para aniquilar o chamado **Risco Herstatt** (risco de uma das contrapartes liquidar sua ponta em fuso horário anterior sem receber a contraparte de moeda estrangeira).

### Arquitetura de Liquidação Multimoeda
- **Ponta Doméstica (BRL)**: Liquidada em tempo real no Sistema de Pagamentos Brasileiro (SPB/STR) junto ao Banco Central.
- **Ponta Estrangeira (USD)**: Liquidada através de banco correspondente e custodiante no exterior (Fedwire / CHIPS / Euroclear).
- **Condicionalidade Estrita**: O pagamento em BRL só é liberado ao vendedor se e somente se o crédito em USD for irreversivelmente confirmado na conta de custódia da B3 no exterior.

### Instrumentos
- **Câmbio Spot (Pronto)**: Liquidação física de USD contra BRL em D+2.
- **Termo de Câmbio com Entrega Física (Forward)**: Liquidação física em D+N.
- **NDF (Non-Deliverable Forward)**: Liquidação exclusivamente financeira do diferencial cambial frente à PTAX em BRL.

---

## 2. Leis Formais de Conservação e Invariantes

1. **Conservação da Paridade Cambial**:
   $$\text{Valor BRL} = \text{Valor USD} \times \text{Taxa PTAX / Contratada}$$
2. **Atomicidade Estrita PvP (Zero Risco Herstatt)**:
   $$\text{Status} = \texttt{SETTLED} \iff (\text{Ponta USD} = \texttt{CONFIRMED} \land \text{Ponta BRL} = \texttt{CONFIRMED})$$
3. **Conservação Multilateral Soma-Zero**:
   $$\sum_{p} \text{Net USD}_{p} = 0, \quad \sum_{p} \text{Net BRL}_{p} = 0$$

---

## 3. Interfaces do Motor

- Módulo Python: `src/fx_settlement_pvp.py`
- Fixture Sintética: `fixtures/fx_settlement_pvp.json`
- Testes Unitários: `tests/test_fx_settlement_pvp.py`
