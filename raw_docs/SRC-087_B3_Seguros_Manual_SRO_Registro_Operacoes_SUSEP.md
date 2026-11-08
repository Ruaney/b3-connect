# SRC-087: B3 Seguros — Manual do Sistema de Registro de Operações (SRO V3) SUSEP
## Código Fonte Normativo: B3-SRO-MAN-SUSEP-2026-V3.2
### Categoria: REGULATORY_CIRCULARS (Entidade Registradora SUSEP) / B3 Seguros

---

### Resumo Executivo e Escopo
Especificação técnica e diretrizes de integração para companhias seguradoras homologadas perante o Sistema de Registro de Operações (SRO), operado pela B3 sob credenciamento da SUSEP. Disciplina o envio granular, em lote ou via API REST, de apólices, certificados, bilhetes, endossos, cosseguros, sinistros e cancelamentos.

### Fundamentos Regulamentares
1. **Resolução CNSP nº 382/2020 e nº 399/2020:** Estabelece os princípios de conduta, transparência e obrigatoriedade de registro das operações de seguros, previdência aberta e capitalização.
2. **Circular SUSEP nº 642/2021 (SRO V3):** Arquitetura técnica unificada para modernização e recepção de dados em formato JSON com validação semântica e assinatura digital.
3. **Open Insurance Brasil:** Padrões de interoperabilidade e compartilhamento seguro de apólices com consentimento do segurado.

### Mensageria e Ciclo de Vida
* **REG-APOLICE:** Registro granular da emissão de apólice com identificador único, ramo, prêmio líquido, IOF, LMG e cosseguro.
* **REG-ENDOSSO:** Averbação de endossos de cobrança, cancelamento ou alteração de importância segurada.
* **REG-SINISTRO-AVISO:** Comunicação tempestiva de sinistro com estimativa de perda.
* **REG-SINISTRO-LIQ:** Registro do pagamento de indenização com quitação dos valores aos beneficiários.

### Invariantes Formais do Sistema
* **LAW-113 (Cosseguro 100% e Teto LMG):** A distribuição percentual entre cosseguradores deve somar exatamente 100.00%, e o volume acumulado de liquidações não pode ultrapassar o Limite Máximo de Garantia (LMG) da apólice.
