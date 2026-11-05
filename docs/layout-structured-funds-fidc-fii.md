# Especificação Técnica — Escrituração, Liquidação e Gestão de Cotas de Fundos de Investimento Imobiliário (FII) e Cotas Subordinadas/Seniores de FIDC no Balcão NoMe B3 (FL-077)

---

## 1. Visão Geral e Arcabouço Regulatório

O fluxo **`FL-077` — Escrituração, Liquidação e Gestão de Cotas de FII e FIDC** modela o motor determinístico de escrituração, custódia no Balcão NoMe / Central Depositária (CSD) da B3, gestão da cascata de subordinação de cotas em FIDC e o cálculo mandatória de proventos periódicos de FIIs, em estrita conformidade com o novo Marco Regulatório dos Fundos de Investimento:

1. **Resolução CVM nº 175/2022**:
   - **Parte Geral**: Disciplina a emissão de classes e subclasses de cotas, a segregação de direitos e obrigações e a possibilidade de responsabilidade limitada dos cotistas.
   - **Anexo Normativo II (Regulamento de FIDC)**:
     - Classes de cotas: Sênior, Mezanino (Subordinadas Mezanino) e Subordinada Júnior;
     - Hierarquia estrita de subordinação e regras de absorção de perdas por inadimplência (PCLD / Write-Off);
     - Índice mínimo de subordinação (Razão de Subordinação) para proteção da classe sênior.
   - **Anexo Normativo III (Regulamento de FII)**:
     - Regras de governança de ativos imobiliários físicos (tijolo), CRIs, LCIs e cotas de outros fundos;
     - Regime de caixa e apuração de resultados operacionais e financeiros.
2. **Lei nº 8.668/1993 (Regime Legal dos Fundos Imobiliários)**:
   - **Artigo 10, Parágrafo Único**: Distribuição obrigatória de, no mínimo, 95% dos lucros auferidos segundo o regime de caixa, com base em balancete semestral (30 de junho e 31 de dezembro), antecipada mensalmente na prática de mercado.
3. **Regulamento Operacional do Balcão NoMe B3 / Central Depositária B3**:
   - Registro escritural de cotas, eventos de amortização com abatimento de valor patrimonial unitário (NAV), liquidação multilateral e distribuição financeira de proventos.

---

## 2. Invariantes Formais Provadas

O módulo implementa e valida 5 invariantes matemáticas e regulatórias:

1. **Invariante Formal de Conservação Patrimonial de Cotas**:
   - Para FIDC:
     `Patrimônio_Líquido == PL_Sênior + PL_Mezanino + PL_Júnior == Ativos_Totais - Passivo_Exigível`
     Onde cada `PL_Classe == Quantidade_Cotas * Valor_Unitário_Cota`.
   - Para FII:
     `Patrimônio_Líquido == Quantidade_Total_Cotas * Valor_Unitário_Cota == Ativos_Totais - Passivo_Exigível`.
2. **Invariante de Subordinação Estrita (Water-Tight Priority)**:
   - A cota Subordinada Júnior absorve 100% de qualquer perda decorrente de inadimplência/baixa a prejuízo (write-off) até que seu patrimônio líquido seja integralmente consumido (`PL_Junior == 0`), antes que a cota Mezanino sofra qualquer baixa contábil.
   - A cota Mezanino absorve perdas remanescentes até esgotar seu saldo antes que a cota Sênior seja afetada.
   - A cota Sênior permanece 100% preservada enquanto houver patrimônio positivo nas subclasses subordinadas.
3. **Invariante de Rendimentos Obrigatórios FII (Lei nº 8.668/93, Art. 10 § único)**:
   - Para qualquer período de apuração em que o lucro líquido em regime de caixa for positivo (`Lucro_Caixa > 0`):
     `Proventos_Declarados >= round_cents(0.95 * Lucro_Caixa)`.
   - Qualquer tentativa de declarar proventos inferiores a 95% do lucro caixa semestral é interceptada com `FiiRegulatoryDistributionError`.
4. **Conservação Financeira do Payment Waterfall (FIDC)**:
   - A cascata de pagamentos respeita a conservação de caixa:
     `Caixa_Inicial == Despesas_Operacionais + Serviço_Sênior + Serviço_Mezanino + Proventos_Júnior + Caixa_Final`.
   - O serviço da Sênior (juros e amortização) possui prioridade absoluta: zero centavos são distribuídos à Mezanino ou Júnior se houver déficit na Sênior.
5. **Sanidade dos Preços Unitários e Não-Negatividade de Saldos**:
   - `Valor_Cota >= 0.00000000` em todas as classes;
   - `Caixa_Disponivel >= 0.00`.

---

## 3. Estrutura de Arquivos e Componentes

| Componente | Caminho | Descrição |
|:---|:---|:---|
| **Motor Central** | `src/structured_funds_fidc_fii.py` | Motor determinístico de escrituração, subordinação e liquidação de FIDC e FII |
| **Fixtures Sintéticas** | `fixtures/structured_funds_fidc_fii.json` | Dados de teste com FIDC multitranchas, eventos de write-off e FII corporativo/papel |
| **Bateria de Testes** | `tests/test_structured_funds_fidc_fii.py` | 19 testes unitários cobrindo 100% das regras e invariantes |
| **Invariantes Globais** | `src/invariants.py` | Leis formais LAW-077A, LAW-077B e LAW-077C |
| **Auditoria Central** | `scripts/verify_invariants.py` | Auditoria formal registrada na suíte unificada de conservação B3 |
