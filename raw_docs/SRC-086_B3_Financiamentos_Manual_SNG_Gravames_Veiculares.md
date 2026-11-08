# SRC-086: B3 Financiamentos — Manual do Sistema Nacional de Gravames (SNG) e Integração com Detrans
## Código Fonte Normativo: B3-SNG-MAN-2026-V1.0
### Categoria: NOME_BALCAO (Garantias & Gravames) / B3 Financiamentos

---

### Resumo Executivo e Escopo
Manual técnico e operacional para instituições financeiras credoras e órgãos executivos de trânsito estaduais (Detrans) conveniados ao Sistema Nacional de Gravames (SNG), gerido pela B3 S.A. Disciplina a inserção, consulta, baixa (desalienação) e cancelamento de garantias reais veiculares em âmbito nacional.

### Fundamentos Regulamentares
1. **Resolução CONTRAN nº 807/2020:** Estabelece os prazos máximos para comunicação e averbação das restrições nos bancos de dados estaduais.
2. **Lei Federal nº 13.111/2015:** Princípio da publicidade e rastreabilidade cadastral de débitos e gravames sobre veículos automotores.
3. **Código Civil Brasileiro (Lei 10.406/2002, Art. 1.361):** Definição da alienação fiduciária em garantia de coisa móvel infungível.

### Mensageria e Ciclo de Vida
* **M01 - Inclusão de Gravame:** Envio de contrato de financiamento (CDC, Leasing ou Reserva de Domínio), com validação de Chassi (17 caracteres ISO 3779), Renavam e CNPJ do agente financeiro.
* **M02 - Consulta de Restrição:** Consulta do histórico e status do veículo (Ativo, Baixado, Cancelado).
* **M03 - Baixa de Gravame:** Comunicação eletrônica de liquidação de obrigações com desaverbação automática junto ao Detran em até 10 dias úteis.
* **M04 - Cancelamento de Inclusão:** Anulação tempestiva de inclusão indevida ou distrato.

### Invariantes Formais do Sistema
* **LAW-112 (Unicidade de Gravame Ativo):** Um chassi só pode conter no máximo 1 gravame ativo de 1º grau registrado no SNG, evitando sobreposição de direitos reais sobre o mesmo bem móvel.
