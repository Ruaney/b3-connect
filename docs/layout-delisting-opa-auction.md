# Especificação Técnica — Leilão Especial de Deslistagem / OPA por Fechamento de Capital, Quórum de 2/3 e Tag Along (FL-070)

---

## 1. Visão Geral e Arcabouço Regulatório

O fluxo **`FL-070` — Leilão Especial de Deslistagem / OPA por Fechamento de Capital** implementa o motor determinístico de condução do leilão público na **B3 (Brasil, Bolsa, Balcão)** para cancelamento de registro de companhia aberta (deslistagem) e saída de segmentos especiais de governança corporativa (**Novo Mercado, Nível 2 e Nível 1**), em estrita observância da legislação societária e das instruções normativas da **Comissão de Valores Mobiliários (CVM)**:

1. **Lei nº 6.404/1976 (Lei das Sociedades por Ações)**:
   - **Art. 4º, § 4º**: Determina que o cancelamento de registro de companhia aberta somente será concedido pela CVM se precedido de oferta pública de aquisição de ações (OPA), por preço justo avaliado por laudo de avaliação independente.
   - **Art. 4º-A**: Estabelece o quórum qualificado obrigatório: a deslistagem somente se consuma se titulares de **mais de 2/3 (dois terços)** das ações em circulação (*free float*) concordarem com o fechamento do capital ou aceitarem a oferta.
   - **Art. 4º, § 5º**: Disciplina o resgate compulsório (*Squeeze-Out*): caso remanesçam em circulação menos de 5% (cinco por cento) do total das ações emitidas, a assembleia geral pode aprovar o resgate dessas ações remanescentes pelo valor da oferta.
   - **Art. 254-A**: Determina o direito de saída conjunta (*Tag Along*) aos acionistas minoritários ordinaristas, com valor mínimo de 80% do preço pago pelo bloco de controle (ou 100% nos regulamentos de Novo Mercado da B3).
2. **Resolução CVM nº 166/2022 (Consolidação das Ofertas Públicas de Aquisição de Ações)**:
   - Substitui e moderniza as antigas Instruções CVM 361/2002 e CVM 480/2009.
   - Regula o procedimento de habilitação de acionistas na B3, prazos editalícios, cálculo de concordantes e discordantes, rateio proporcional e liquidação financeira via câmara.
3. **Regulamentos de Listagem da B3 (Novo Mercado, Nível 2 e Básico)**:
   - Novo Mercado: Exige 100% de Tag Along para ações ordinárias e quórum estrito com laudo de avaliação econômico-financeira auditado.

---

## 2. Invariantes Formais Provadas

1. **Quórum Qualificado de 2/3 do Free Float (Art. 4º-A)**:
   A deslistagem é homologada como aprovada se, e somente se, o total de ações concordantes (vendas habilitadas + concordantes expressos) superar `2/3` do *free float* habilitado.
2. **Inviolabilidade do Piso Regulatório de Tag Along (Art. 254-A)**:
   O preço ofertado por ação não pode ser inferior ao percentual estatuário aplicável ao preço pago ao bloco de controle (`P_ofertado >= TagAlong_Pct * P_controle`).
3. **Conservação Estrita de Ações Habilitadas**:
   `Ações_Habilitadas == Ações_Aceitas + Ações_Devolvidas` (sem geração ou extravio de ações).
4. **Conservação Financeira de Desembolso da OPA**:
   `Desembolso_Total == (Ações_Aceitas * Preço_Ofertado) + (Ações_SqueezeOut * Preço_Ofertado)`.
5. **Rateio Proporcional pelo Método dos Maiores Restos (Hamilton / Hare-Niemeyer)**:
   Alocação inteira e determinística de ações entre os ofertantes quando há limite de aceitação, eliminando resíduos fracionários.
6. **Segregação Fiduciária de Custódia Bloqueada**:
   Ordens somente são habilitadas no book de OPA se previamente bloqueadas na subconta de custódia da B3 pelo participante de liquidação.

---

## 3. Interfaces do Módulo

- **Módulo Python**: `src/delisting_opa_auction.py`
- **Fixture JSON**: `fixtures/delisting_opa_auction.json`
- **Testes Unitários**: `tests/test_delisting_opa_auction.py`
