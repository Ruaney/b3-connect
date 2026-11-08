# SRO — Sistema de Registro de Operações (B3 Seguros / SUSEP)
## Fluxo FL-107: Registro de Apólices, Cosseguro, Endossos e Liquidação de Sinistros

---

### 1. Visão Geral e Fundamentação Regulatória
O **Sistema de Registro de Operações (SRO)** é a infraestrutura de mercado regulada pela **SUSEP (Superintendência de Seguros Privados)**, na qual a B3 atua como Entidade Registradora credenciada para coletar, validar, certificar e armazenar as operações das companhias seguradoras brasileiras.

* **Resoluções CNSP nº 382/2020 e nº 399/2020:** Estabelecem a obrigatoriedade de registro das apólices, bilhetes, certificados e contratos de cosseguro e resseguro.
* **Circulares SUSEP nº 605/2020, 612/2020 e 642/2021:** Detalham as tabelas de domínio, padrões de integração e a arquitetura tecnológica **SRO V3**.
* **Invariante LAW-113:** Conservação de Cosseguro (Partição estrita em 100%) e Inviolabilidade do Limite Máximo de Garantia (LMG).

---

### 2. Ramos Homologados
* **Ramo 0531:** Automóvel (Casco e RCF-V)
* **Ramo 0118:** Patrimonial Compreensivo Empresarial
* **Ramo 0993:** Vida Individual e Coletivo (VGBL / AP)
* **Ramo 1101:** Rural e Agrícola (Grãos, Pecuária e Floresta)
* **Ramo 0622:** Transporte Nacional de Cargas
* **Ramo 0351:** Responsabilidade Civil de Administradores (D&O)
* **Ramo 0775:** Garantia Seguradora (Garantia Judicial e Contratual)

---

### 3. Ciclo Operacional Passo a Passo
```
[Companhia Seguradora] 
         │ 
         │ 1. Emissão e Envio do Registro de Apólice (Proposta, Coberturas, LMG, Prêmio, Cosseguro)
         ▼
[B3 SRO Engine (V3)] ─── Checagem LAW-113:
         │                ├─> Σ(Quotas de Cosseguro) == 100.00%?
         │                └─> Prêmio Total == Prêmio Líquido + IOF?
         │
         ├───> [NÃO] ───> REJEIÇÃO SUSEP (Erro de Partição de Risco)
         │
         └───> [SIM] ───> Geração de Hash Criptográfico SUSEP & Certificação
                                    │
                                    ▼ (Ocorrência de Evento Coberto)
                         2. Aviso e Regulação de Sinistro
                                    │
                                    ▼ 
                         3. Liquidação e Pagamento de Indenização
                                    │
                         Checagem: Σ(Sinistros Liquidados) <= LMG (LAW-113)
```

---

### 4. Invariante Formal: LAW-113
$$\sum_{c \in \text{Cosseguradores}} \text{Quota}(c) = 100.00\%$$
$$\sum_{s \in \text{Sinistros}(A)} \text{ValorLiquidado}(s) \le \text{LMG}(A)$$

Assegura que a partição de risco entre cosseguradores seja exaustiva e que nenhuma seguradora pague indenização cumulativa que viole o teto contratual estabelecido na apólice.
