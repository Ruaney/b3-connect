# SNG — Sistema Nacional de Gravames (B3 Financiamentos)
## Fluxo FL-106: Registro, Consulta, Baixa e Cancelamento de Restrições Veiculares junto aos Detrans

---

### 1. Visão Geral e Fundamentação Regulatória
O **Sistema Nacional de Gravames (SNG)** é a plataforma central operada pela B3 para gerenciar o ciclo de vida de garantias reais sobre a frota automotiva brasileira. Ele conecta eletronicamente instituições credoras (bancos, financeiras, administradoras de consórcio e cooperativas de crédito) aos Departamentos Estaduais de Trânsito (**Detrans**) de todas as 27 Unidades Federativas.

* **Resolução CONTRAN nº 807/2020:** Estabelece os requisitos e prazos para anotação e baixa de gravames nos órgãos executivos de trânsito.
* **Art. 1.361 do Código Civil:** Disciplina a constituição da propriedade fiduciária e o desdobramento da posse direta e indireta.
* **Lei Federal nº 13.111/2015:** Obriga a declaração prévia e formal de restrições financeiras e tributárias sobre veículos comercializados.
* **Invariante LAW-112:** Unicidade de Gravame Ativo de 1º Grau por Chassi/Renavam (Inviolabilidade contra sobreposição de alienações conflitantes).

---

### 2. Modalidades de Gravame Homologadas
1. **Alienação Fiduciária:** Transferência da propriedade resolúvel do veículo ao credor fiduciário como garantia do pagamento da dívida financiada (CDC).
2. **Arrendamento Mercantil (Leasing):** O veículo permanece na propriedade da sociedade de arrendamento mercantil, concedendo o direito de opção de compra ao arrendatário no término contratual.
3. **Reserva de Domínio:** O vendedor reserva para si a propriedade do bem até o integral cumprimento das obrigações pecuniárias.
4. **Penhor Veicular:** Penhor civil ou mercantil averbado para fins de garantia especial.

---

### 3. Ciclo Operacional Passo a Passo
```
[Instituição Financeira] 
         │ 
         │ 1. Inclusão de Gravame (Chassi, Renavam, CNPJ Credor, Valor, Parcelas)
         ▼
[B3 SNG Engine] ─── Checagem LAW-112 (Chassi já possui gravame ativo?)
         │
         ├───> [SIM] ───> REJEIÇÃO IMEDIATA (Violação LAW-112)
         │
         └───> [NÃO] ───> Emissão de Protocolo B3 & Notificação DETRAN Estadual
                                    │
                                    ▼
                         [CRV / CRLV com Restrição Financeira]
                                    │
                                    ▼ (Quitação do Contrato)
                         2. Baixa de Gravame (Desalienação)
                                    │
                                    ▼
                         [Liberação para Transferência do Veículo]
```

---

### 4. Invariante Formal: LAW-112
$$\forall v \in \text{Veiculos}, \quad \sum_{g \in \text{Gravames}(v)} \mathbb{I}(\text{status}(g) = \text{ATIVO}) \le 1$$

Nenhum chassi ou renavam pode apresentar mais de uma garantia real ativa simultaneamente no banco de dados da B3 e dos Detrans, prevenindo fraudes de duplo financiamento do mesmo bem.
