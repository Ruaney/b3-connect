# Especificação Técnica — Gestão de Risco de Liquidez Intradiária e Estresse Cambial PTAX (BCBS 248 & LCR) (FL-072)

---

## 1. Visão Geral e Arcabouço Regulatório

O fluxo **`FL-072` — Gestão de Risco de Liquidez Intradiária e Estresse Cambial PTAX** implementa o motor de monitoramento contínuo de horizontes de sobrevivência de caixa intradiário, elegibilidade e monetização de ativos líquidos de alta qualidade (**HQLA - High Quality Liquid Assets**), e testes de estresse de choques cambiais de PTAX na câmara de compensação e liquidação da **B3**.

Marco regulatório e prudencial:
1. **Comitê da Basileia para Supervisão Bancária (BCBS 248)**:
   - *Monitoring tools for intraday liquidity management*: métricas de absorção de picos de liquidez intradiária por janelas horárias (W0900 a W1800), dependência de crédito intradiário e capacidade de sobrevivência sob choques de mercado.
2. **Resolução CMN nº 4.557/2017 e Circular BACEN nº 3.749**:
   - Estrutura de gerenciamento de risco de liquidez (GRL) e apuração do **Índice de Cobertura de Liquidez (LCR - Liquidity Coverage Ratio)**.
   - Tetos regulatórios de composição de HQLA: Ativos Nível 1 (TPF / reservas) sem limite; Ativos Nível 2 restritos a no máximo 40% do colchão total; haircuts obrigatórios de 15% para Nível 2A e 50% para Nível 2B.
3. **Câmara B3 — Chamadas de Margem Extraordinárias Intradiárias**:
   - Variações extremas na taxa de câmbio PTAX acionam chamadas de margem em tempo real para posições em derivativos cambiais (Dólar Futuro DOL, Minicontrato WDO, Cupom Cambial DDI e Opções de Câmbio).

---

## 2. Invariantes Formais Provadas

1. **Preservação de Buffer de Liquidez Não-Negativo**:
   Sob qualquer cenário de estresse, o caixa líquido remanescente da instituição financeira deve se manter maior ou igual a zero (`Buffer_Liquido >= 0,00`).
2. **Monotonicidade Estrita de Choque Cambial de PTAX**:
   `Choque_PTAX(delta_2) >= Choque_PTAX(delta_1) ==> Margem_Requerida(delta_2) >= Margem_Requerida(delta_1)`.
3. **Hierarquia e Tetos Prudenciais de HQLA**:
   Ativos Nível 1 são monetizados prioritariamente sem deságio. Ativos Nível 2 têm haircut prudencial e teto legal de 40% do buffer total.
4. **Conservação de Fluxos de Liquidação por Janela Horária**:
   `Saldo_Final_Janela == Saldo_Inicial + Inflows - Outflows - Chamadas_Margem + HQLA_Monetizado`.
5. **Determinismo do Horizonte de Sobrevivência (Survival Horizon)**:
   Determina o número exato de horas e janelas que o participante suporta antes da exaustão completa de liquidez.

---

## 3. Interfaces do Módulo

- **Módulo Python**: `src/intraday_liquidity_risk.py`
- **Fixture JSON**: `fixtures/intraday_liquidity_risk.json`
- **Testes Unitários**: `tests/test_intraday_liquidity_risk.py`
