# Especificação Técnica — Registro Escritural e Custódia de Cédulas Imobiliárias Rurais (CIR) com Afetação — NoMe Agro B3 (FL-097)

## 1. Visão Geral e Arcabouço Regulatório

O fluxo **FL-097** implementa a esteira integrada da Registradora e Central Depositária B3 (**Balcão NoMe Agro**) para registro escritural, controle de patrimônio de afetação, validação SIGEF/INCRA, liquidação financeira e excussão fiduciária de **Cédulas Imobiliárias Rurais (CIR)**.

A CIR é um título de crédito representativo de promessa de pagamento em dinheiro, emitido por produtor rural proprietário de imóvel rural, vinculado a fração perfeitamente demarcada e georreferenciada de seu imóvel sob regime fiduciário de afetação.

### 1.1 Normativos Aplicáveis
- **Lei Federal nº 13.986/2020 (Lei do Agro)**: Instituição do patrimônio de afetação sobre imóveis rurais e criação da Cédula Imobiliária Rural (arts. 17 a 33).
- **Resolução CMN nº 4.818/2020**: Requisitos de escrituração e registro de CIR em entidades autorizadas pelo Banco Central do Brasil.
- **Normas INCRA / Sistema de Gestão Fundiária (SIGEF)**: Certificação de poligonais georreferenciadas e unicidade cartorial com matrículas imobiliárias (CRI).
- **Manual de Operações da Registradora de Títulos do Agronegócio B3**: Conciliação de garantias reais, incomunicabilidade patrimonial e liquidação financeira DvP.

---

## 2. Estrutura Técnica e Regras Operacionais

1. **Patrimônio de Afetação Rural**:
   - Fração de terra demarcada em hectares (`affected_fraction_ha <= total_area_ha`).
   - Avaliação pericial independente (`appraisal_value_brl`).
   - Código unívoco no SIGEF/INCRA e averbação no Cartório de Registro de Imóveis (CRI).

2. **Limite Prudencial Estrito**:
   - $\text{Notional Emitido} \le \text{Valor de Avaliação Pericial da Terra Afetada}$.
   - Tentativas de emissão que ultrapassem o valor do laudo pericial são sumariamente bloqueadas.

3. **Incomunicabilidade e Anti-Dupla Afetação**:
   - A fração afetada constitui patrimônio separado e não responde por outras dívidas do emitente.
   - Nenhuma fração de imóvel pode ser vinculada simultaneamente a mais de uma CIR ativa.

4. **Liquidação e Excussão**:
   - **Liquidação Regular (DvP)**: Transferência de recursos do emitente ao credor via SPB/STR com soma-zero contábil.
   - **Excussão Fiduciária em Caso de Inadimplência**: Leilão da fração afetada, recuperação do saldo devedor ao credor e devolução compulsória de eventual excedente financeiro ao emitente.

---

## 3. Leis Formais de Invariantes

- **LAW-103A (Inviolabilidade e Limite Prudencial do Patrimônio de Afetação)**:
  $$\forall c \in \text{CIRs}, \quad \text{Notional}(c) \le \text{ValorAvaliação}(\text{Parcela}(c))$$
- **LAW-103B (Unicidade de Matrícula e Fração Georreferenciada / Anti-Dupla Afetação)**:
  Nenhuma fração certificada no SIGEF pode estar vinculada a mais de uma CIR nos estados `REGISTERED` ou `ACTIVE`.
- **LAW-103C (Liquidação Financeira Bilateral Soma-Zero)**:
  $$\sum \text{Débito Emitente} = \sum \text{Crédito Credor} \quad \text{centavo a centavo}$$
