# Especificação Técnica — Empréstimo Eletrônico Registrado de Ativos Estruturados (FL-094)

## 1. Visão Geral e Arcabouço Regulatório

O fluxo **FL-094** normatiza e operacionaliza o serviço institucional de empréstimo eletrônico registrado de títulos corporativos privados e ativos estruturados (**Debêntures, CRI, CRA e Letras Financeiras**) na Registradora e Central Depositária B3 (**Balcão NoMe**).

### 1.1 Normativos Aplicáveis
- **Resolução CMN nº 4.952/2021**: Registro e controle de contratos derivativos e aluguel de títulos privados.
- **Circular BCB nº 3.978/2020**: Requisitos de identificação de contrapartes e compliance na custódia e empréstimo.
- **Instrução Normativa RFB nº 1.585/2015**: Tratamento tributário da remuneração de empréstimo e neutralidade fiscal no repasse compulsório de proventos (*pass-through*).

---

## 2. Leis Formais de Invariantes

- **LAW-099A (Conservação de Saldo Físico)**: A quantidade de títulos debitada do doador deve ser estritamente igual à creditada ao tomador, e vice-versa na devolução.
- **LAW-099B (Repasse Integral de Rendimentos)**: Todos os juros remuneratórios e amortizações gerados pelo ativo durante a vigência do contrato devem ser integralmente repassados ao doador.
- **LAW-099C (Não-Negatividade de Custódia e Garantias)**: Nenhum saldo de custódia ou garantia pode assumir valor negativo.
