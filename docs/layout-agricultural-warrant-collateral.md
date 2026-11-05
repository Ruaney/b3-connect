# Especificação Técnica — Gestão de Garantias em Commodities Agrícolas (CDA e WA) com Armazéns Credenciados B3 (FL-078)

---

## 1. Visão Geral e Arcabouço Regulatório

O fluxo **`FL-078` — Gestão de Garantias em Commodities Agrícolas (CDA e WA)** modela o motor determinístico de registro, precificação mark-to-market, controle de quebra técnica de armazenagem, aplicação de haircuts regulatórios da Câmara B3 e procedimentos de excussão física em leilão público dos títulos agropecuários emitidos por armazéns gerais.

Títulos cobertos:
- **Certificado de Depósito Agropecuário (CDA)**: Título representativo da propriedade da mercadoria depositada no armazém.
- **Warrant Agropecuário (WA)**: Título de crédito representativo da promessa de pagamento em dinheiro que confere direito de penhor sobre a mercadoria descrita no respectivo CDA.

Bases normativas:
1. **Lei nº 11.076/2004 (Títulos do Agronegócio)**:
   - Arts. 1º a 22: Disciplina a emissão conjunta e inseparável na origem (CDA/WA), a possibilidade de desmembramento para cessão de crédito com penhor no WA, e o procedimento de protesto em cartório por falta de pagamento.
2. **Decreto nº 1.102/1903 (Regulamento dos Armazéns Gerais)**:
   - Fixa as obrigações dos depositários, seguro obrigatório contra fogo e intempéries, tarifas de armazenagem e responsabilidade pela custódia física dos produtos.
3. **Lei nº 14.711/2023 (Marco Legal das Garantias)**:
   - Aprimora a excussão extrajudicial e o leilão público de bens dados em garantia fiduciária e penhor mercantil.
4. **Manual de Risco e Garantias da Câmara B3 (CORE)**:
   - Aceitação de colaterais em commodities (Soja, Milho, Boi Gordo e Café Arábica) como margem para derivativos agropecuários, com aplicação de matriz multidimensional de haircuts (base, safra, tempo de armazenagem e desvios de umidade/impureza).

---

## 2. Invariantes Formais Provadas

1. **Conservação Física de Massa/Sacas e Quebra Técnica**:
   `Quantidade_Original == Quantidade_Efetiva_Armazenada + Quebra_Tecnica_Acumulada`.
2. **Monotonicidade da Matriz de Haircuts B3**:
   Deságios crescentes conforme o tempo de armazenamento e antiguidade da safra (`Haircut(Safra_Velha) >= Haircut(Safra_Atual)`).
3. **Trava de Segurança de Desoneração (Safe-Release Guardrail)**:
   É proibida a liberação de warrants se o colateral remanescente na conta de compensação for inferior à margem de garantia exigida pela Câmara B3.
4. **Waterfall Financeiro Estrito de Excussão em Leilão**:
   Ordem prioritária de liquidação:
   1. Custas de armazenagem, impostos e leiloeiro;
   2. Liquidação integral do crédito garantido pelo WA (principal + encargos contratuais);
   3. Sobra remanescente entregue ao titular do CDA.
   `Recursos_Brutos_Leilao == Despesas_Armazém + Crédito_WA_Liquidado + Remanescente_CDA` (centavo a centavo, zero-leakage).
5. **Seguro Obrigatório Válido**:
   Apenas armazéns gerais devidamente credenciados perante a B3 e com apólices de seguro vigentes podem emitir títulos elegíveis a colateral.

---

## 3. Interfaces do Módulo

- **Módulo Python**: `src/agricultural_warrant_collateral.py`
- **Fixture JSON**: `fixtures/agricultural_warrant_collateral.json`
- **Testes Unitários**: `tests/test_agricultural_warrant_collateral.py`
