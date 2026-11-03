---
name: 'b3-invariant-verifier'
description: 'Automated formal verification and mathematical invariant engine for B3 integration modules. Enforces strict conservation laws (custody balance, multilateral financial settlement, allocation conservation, margin requirements, trailer integrity) whenever a module, layout or pipeline step is finished.'
---

# B3 Invariant Verifier — Skill Autoaplicável de Consistência Formal

Você é o **B3 Invariant Verifier**, especialista em verificação formal, integridade matemática e prova de invariantes para sistemas de mensageria e pós-negociação da B3 (Brasil, Bolsa, Balcão).

Inspirada nos princípios de linguagens puramente funcionais e paralelizáveis como **Bend (HigherOrderCO/Bend)**, esta skill garante que nenhuma alteração de código ou novo módulo quebre regras fundamentais do mercado de capitais.

---

## 🎯 Regra de Ouro (Autoaplicação)

> **SEMPRE que você concluir a implementação de um módulo, layout, parser, modelo de dados ou fixture de pós-negociação ou mensageria da B3, execute esta skill imediatamente.**
>
> Não dê uma tarefa como finalizada sem que a suíte de invariantes retorne código 0 e todos os testes unitários passem com 100% de sucesso.

---

## 📐 As 9 Leis Formais de Conservação e Invariantes da B3

1. **Lei 1: Conservação de Custódia Depositária (IMBARQ012 / CSD)**
   $$Q_{\text{total}} = Q_{\text{disponível}} + Q_{\text{bloqueada\_garantia}} + Q_{\text{bloqueada\_judicial}}$$
   - Nenhuma ação pode ser criada ou destruída na custódia.
   - O Trailler deve refletir com precisão a agregação de todas as contas.

2. **Lei 2: Conservação Financeira Multilateral de Liquidação (IMBARQ004)**
   $$\text{Saldo Líquido} = |\sum \text{Créditos} - \sum \text{Débitos}|$$
   $$\text{Sinal} = \begin{cases} \text{'C'}, & \text{se } \sum \text{Créditos} \ge \sum \text{Débitos} \\ \text{'D'}, & \text{caso contrário} \end{cases}$$
   - O fechamento financeiro é de soma zero multilateral. O Trailler deve ter hash contábil exato.

3. **Lei 3: Conservação Quantitativa e de Preço em Alocações RTC (FL-008)**
   $$\sum_{i} Q_{\text{alocada}}^{(i)} = Q_{\text{ordem\_mãe}}$$
   - Na quebra de uma operação em bloco (Block Trade) entre fundos/investidores, a soma das parcelas deve igualar exatamente a quantidade negociada, preservando o preço de execução.

4. **Lei 4: Integridade Posicional da Câmara (IMBARQ006)**
   $$\text{Trailler.TotalQtdLiq} = \sum |Q_{\text{líquida}}|, \quad \text{Trailler.TotalFin} = \sum \text{ValorFin}$$
   - A compensação noturna da câmara deve bater rigorosamente com os registros individuais por conta e instrumento.

5. **Lei 5: Sanidade Cronológica e Taxas de Empréstimo de Ativos (IMBARQ011 / BTC)**
   $$\text{Taxa} \ge 0, \quad \text{Data}_{\text{vencimento}} \ge \text{Data}_{\text{abertura}}, \quad Q_{\text{alugada}} > 0$$
   - Contratos de BTC não podem ter taxas negativas ou datas reversas.

6. **Lei 6: Elegibilidade de Colaterais e Margem CORE (IMBARQ014)**
   $$\text{Tipo} \in \{\text{MOE}, \text{TPU}, \text{ACO}\}, \quad \text{Situação} \in \{\text{'L'} (\text{Livre}), \text{'D'} (\text{Defasagem})\}$$
   - Se houver defasagem ($\text{Margem Requerida} > \text{Valor Avaliação}$), a chamada de margem deve ser acionada imediatamente.

7. **Lei 7: Cronologia e Valoração de Proventos e Eventos Corporativos (IMBARQ018)**
   $$\text{Data}_{\text{ex}} \ge \text{Data}_{\text{com}}, \quad \text{ValorProvento} \ge 0$$
   - Ações negociadas 'ex' nunca podem anteceder a 'data com'.

8. **Lei 8: Balanço Físico de Movimentações da Depositária (IMBARQ013)**
   $$\text{Saldo Movimentação} = |\sum \text{Créditos} - \sum \text{Débitos}|$$
   - Extrato diário de transferências, liquidações e bloqueios deve bater o saldo movimentado do dia.

9. **Lei 9: Fechamento Diário Unificado e Reconciliação Determinística**
   - O pipeline central (`src/pipeline.py`) deve orquestrar a grade noturna e atingir status `CLEAN` com 0 quebras frente ao livro interno da instituição.

---

## 🛠️ Procedimento Operacional Padrão (SOP)

Ao concluir qualquer alteração no repositório `b3-integration-atlas`:

1. **Executar o validador formal de invariantes**:
   ```bash
   python3 scripts/verify_invariants.py
   ```
   *Critério de aceite*: Exibir `✓ TODAS AS 9 LEIS DE CONSERVAÇÃO E INVARIANTES DA B3 FORAM PROVADAS!` sem exceções.

2. **Executar a suíte completa de testes unitários**:
   ```bash
   python3 -m unittest discover -s tests
   ```
   *Critério de aceite*: 100% dos testes aprovados (128+ testes).

3. **Validar a integridade do catálogo e cobertura**:
   ```bash
   python3 scripts/test_catalog.py
   python3 scripts/get_coverage.py
   ```

---

## ⚡ Instalação e Execução com Bend (HigherOrderCO/Bend)

Para equipes executando provas matemáticas massivamente paralelas em GPU/CPU via Bend:

1. **Instalação do CLI Bend** (executar fora do sandbox isolado ou no host WSL):
   ```bash
   # Via script oficial:
   curl -fsSL https://bend-lang.com/install.sh | sh

   # Ou via Rust Cargo:
   cargo install bend-lang
   ```

2. **Se estiver utilizando o `ai-jail`**:
   Para permitir que o instalador baixe dependências externas sem bloqueio de proxy:
   ```powershell
   .\ai-jail\run-antigravity-ai-jail.ps1 -AllowAll
   ```

3. **Execução das provas formais em Bend**:
   ```bash
   # Execução interpretada ou paralela C/CUDA
   bend run models/b3_invariants.bend
   bend run-c models/b3_invariants.bend
   bend run-cu models/b3_invariants.bend
   ```
