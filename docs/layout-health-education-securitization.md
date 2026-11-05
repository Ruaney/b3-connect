# Especificação Técnica — Emissão, Registro e Gestão de Fundo de Reserva de CR Saúde e CR Educação no Balcão NoMe B3 (FL-085)

---

## 1. Visão Geral e Arcabouço Regulatório

O fluxo **`FL-085` — Emissão, Registro e Gestão de Fundo de Reserva de Certificados de Recebíveis de Saúde (CR Saúde) e Educação (CR Educação)** implementa o modelo determinístico, operacional e contábil da **B3 (Brasil, Bolsa, Balcão)** para a estruturação fiduciária, escrituração em custódia no Balcão NoMe B3 e cascata periódica de amortização de certificados de securitização social, em estrita observância a:

1. **Lei nº 14.430/2022 (Marco Legal da Securitização)**:
   - Institui a emissão ampla de Certificados de Recebíveis (CR), abrangendo direitos creditórios originados nos setores de saúde e educação (artigos 1º, 3º e 4º).
   - Disciplina a constituição do **Regime Fiduciário e do Patrimônio Separado** (artigos 17 a 33), garantindo a incomunicabilidade absoluta dos bens e direitos integrantes da emissão em relação ao patrimônio geral da companhia securitizadora ou de outras emissões (art. 19).
2. **Resolução CVM nº 60/2021**:
   - Regulamenta as companhias securitizadoras de direitos creditórios registradas na CVM, a atuação obrigatória do Agente Fiduciário (Trustee), as regras de transparência, subordinação de tranches e constituição de reservas.
3. **Resolução CVM nº 175/2022**:
   - Marco regulatório dos fundos de investimento (incluindo FIDC e aquisição de cotas/direitos creditórios sociais).
4. **Regulação Setorial Específica**:
   - **Saúde Suplementar**: Lei nº 9.656/1998 e normativos da Agência Nacional de Saúde Suplementar (ANS).
   - **Saúde Pública (SUS)**: Sistema Único de Saúde, faturas hospitalares lastreadas em AIH (Autorização de Internação Hospitalar) e APAC (Alta Complexidade) via Fundo Nacional de Saúde (FNS).
   - **Educação Básica e Superior**: Lei de Diretrizes e Bases da Educação Nacional (LDB - Lei nº 9.394/1996), censo escolar INEP e autorizações do MEC.

---

## 2. Tipologia de Lastros Sociais Elegíveis

### Setor de Saúde (CR Saúde)
- **`HEALTH_PLAN_PREMIUM`**: Mensalidades de planos de saúde individuais e corporativos coletivos regulados pela ANS.
- **`HOSPITAL_SUS_INVOICE`**: Faturas de serviços hospitalares prestados ao Sistema Único de Saúde (SUS), lastreadas em AIH e APAC devidamente auditadas e homologadas.
- **`PRIVATE_CLINIC_RECEIVABLE`**: Recebíveis de atendimentos clínicos particulares, exames laboratoriais, centros de diagnóstico por imagem e procedimentos cirúrgicos em hospitais privados.

### Setor de Educação (CR Educação)
- **`TUITION_FEES_K12`**: Mensalidades escolares de educação infantil, ensino fundamental e ensino médio (K-12).
- **`TUITION_FEES_HIGHER_ED`**: Mensalidades universitárias de graduação presencial/EAD e cursos de pós-graduação/especialização com registro no MEC.
- **`STUDENT_LOAN_PRIVATE`**: Contratos de mútuo e financiamento estudantil privado com garantias fidejussórias de fiadores/avalistas solidários.

---

## 3. Invariantes Formais Provadas e Auditadas

1. **Conservação Financeira Multilateral do Waterfall (Entradas == Saídas)**:
   $$Caixa_{Inicial} + Inflow_{Lastros} + Rendimentos + Saques_{Reserva} = Despesas + Serviço_{Sênior} + Serviço_{Mezanino} + Aportes_{Reserva} + Serviço_{Subordinada} + Residual + Caixa_{Final}$$
2. **Incomunicabilidade Absoluta do Patrimônio Separado (Lei nº 14.430/2022, art. 19)**:
   Os ativos, lastros, contas e fundo de reserva respondem unicamente pelas obrigações decorrentes da respectiva emissão social. Qualquer tentativa de direcionamento para contas da securitizadora ou de outras emissões dispara `HealthEducationSegregationViolationError`.
3. **Inviolabilidade da Subordinação (Water-Tight Subordination)**:
   Nenhum pagamento é efetuado à classe Subordinada caso haja inadimplemento de juros/amortização na Sênior ou caso o Fundo de Reserva esteja abaixo de sua meta regulamentar.
4. **Absorção Hierárquica de Perdas (First-Loss Piece)**:
   Perdas decorrentes de glosas hospitalares do SUS irremediáveis ou inadimplência de mensalidades consomem sucessivamente: Subordinada $\to$ Mezanino $\to$ Sênior.
5. **Conservação de Preço Unitário e Quantidade**:
   $$Saldo\_Devedor = Quantidade \times Preço\_Unitário$$
6. **Sanidade e Recomposição do Fundo de Reserva**:
   Saldo atual não-negativo, piso obrigatório respeitado e recomposição com prioridade sobre classes subordinadas.
7. **Elegibilidade Estrita por Setor Social**:
   CR Saúde só admite lastros de saúde; CR Educação só admite lastros de educação.

---

## 4. Estrutura de Arquivos

- **Implementação**: `src/health_education_securitization.py`
- **Fixture Sintética Oficial**: `fixtures/health_education_securitization.json`
- **Suíte de Testes Unitários**: `tests/test_health_education_securitization.py`
