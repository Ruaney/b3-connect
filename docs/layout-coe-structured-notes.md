# Especificação Técnica: Certificado de Operações Estruturadas — COE (FL-058)

## 1. Visão Geral e Contexto Regulatório

O **Certificado de Operações Estruturadas (COE)** é um instrumento híbrido regulamentado pela **Resolução CMN nº 4.263/2013**, **Instrução CVM nº 569/2015** e **Resolução CVM nº 8/2020**, registrado no **Balcão NoMe B3**. Combina elementos de renda fixa com instrumentos derivativos complexos (opções de compra/venda, barreiras, spreads e cestas de ativos).

### Modalidades de Proteção do Principal
1. **Valor Nominal Protegido (Protected Capital)**: Garantia de restituição de no mínimo 100% do valor nominal investido na data de vencimento.
2. **Valor Nominal em Risco (At-Risk Capital)**: Possibilidade de perda do principal até um piso previamente pactuado no DIE (Documento de Informações Essenciais) ou até 100% do capital investido.

### Estruturas de Payoff Suportadas
- **Bull Spread (Alta com Ganho Limitado)**: Participação de 100% ou alavancada na valorização do indexador, limitada a um ganho máximo (*Cap*).
- **Digital Binary (Retorno Fixo Condicional)**: Pagamento de cupom pré-definido caso o ativo-objeto esteja igual ou acima do preço inicial na data de observação.
- **Autocallable (Saída Antecipada Automática)**: Verificação periódica de barreira; se o ativo estiver no nível de disparo, o COE encerra antecipadamente pagando o cupom acumulado.
- **Participation with Cap**: Percentual de participação com teto máximo de remuneração.

---

## 2. Leis Formais de Conservação e Invariantes

1. **Lei da Proteção de Capital**:
   $$\text{Total Gross} \ge \text{Principal Investido} \quad \forall \text{ COE com modalidade } \texttt{PROTECTED\_CAPITAL}$$
2. **Lei do Teto de Retorno (Cap Invariant)**:
   $$\text{Total Gross} \le \text{Principal Investido} \times (1 + \text{Cap Gain Pct})$$
3. **Fechamento Contábil e Retenção Regressiva de IRRF**:
   $$\text{Líquido} = \text{Principal} + (\text{Rendimento Bruto} - \text{IRRF}), \quad \text{IRRF} = \text{Rendimento Bruto} \times \text{Alíquota Regressiva}$$

---

## 3. Interfaces do Motor

- Módulo Python: `src/coe_structured_notes.py`
- Fixture Sintética: `fixtures/coe_structured_notes.json`
- Testes Unitários: `tests/test_coe_structured_notes.py`
