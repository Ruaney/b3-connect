# Guia de Integração — Sistema de Registro de Operações (SRO)
## Registro de Apólices SUSEP V3, Cosseguro, Aviso e Liquidação de Sinistros (FL-107)

---

### 1. Visão Geral do SRO (B3 Seguros)
O **Sistema de Registro de Operações (SRO)** é a infraestrutura do mercado segurador brasileiro regulada pela **SUSEP (Superintendência de Seguros Privados)**, na qual a B3 atua como Registradora de Operações autorizada. 

O SRO tem por finalidade registrar e certificar com validade jurídica todas as apólices, bilhetes, certificados de participantes, contratos de cosseguro, resseguro e eventos de sinistro emitidos por seguradoras autorizadas no país.

---

### 2. Arcabouço Normativo e Padrão SRO V3
* **Resoluções CNSP nº 382/2020 e nº 399/2020:** Fixam a obrigatoriedade do registro tempestivo de apólices e operações de seguros em entidades registradoras credenciadas.
* **Circulares SUSEP nº 605/2020, nº 612/2020 e nº 642/2021:** Detalham o dicionário de dados da arquitetura **SRO V3**, layout de interoperabilidade, mensageria JSON e regras de validação síncrona.
* **Invariante LAW-113 da B3:**
  1. **Conservação de Cosseguro:** A soma ponderada das quotas de risco assumidas pelos cosseguradores deve ser rigorosamente igual a $100{,}0000\%$.
  2. **Inviolabilidade do LMG:** A soma acumulada das indenizações de sinistros liquidados de uma apólice não pode ultrapassar o Limite Máximo de Garantia (LMG) contratado.

---

### 3. Ramos de Seguro Homologados no Padrão SUSEP V3

| Código SUSEP | Nome do Ramo | Descrição da Cobertura |
|---|---|---|
| `RAMO_0531` | Automóvel (Casco e RCF-V) | Cobertura compreensiva de veículos e responsabilidade civil facultativa. |
| `RAMO_0118` | Compreensivo Empresarial | Danos patrimoniais, incêndio, queda de raio e lucros cessantes corporativos. |
| `RAMO_0993` | Vida em Grupo / Acidentes Pessoais | Morte acidental, invalidez permanente e despesas médico-hospitalares. |
| `RAMO_1101` | Rural e Agrícola | Safras de grãos, pecuária intensiva, florestas e benfeitorias rurais. |
| `RAMO_0622` | Transporte Nacional de Cargas | Cobertura de mercadorias em trânsito rodoviário, ferroviário e aéreo. |
| `RAMO_0351` | D&O (Directors & Officers) | Responsabilidade civil para executivos e administradores de companhias abertas. |
| `RAMO_0775` | Garantia Seguradora | Garantias contratuais, judiciais e de participação em licitações públicas. |

---

### 4. Ciclo Operacional Passo a Passo

```
[Companhia Seguradora]
           │
           │  1. Registro de Apólice (POST /api/v1/sro/apolices)
           ▼
  [B3 SRO Engine V3]
           │
           ├───> [Validação LAW-113 Partição de Cosseguro]
           │     ∑(Quotas de Cosseguro) == 100.00%?
           │     Prêmio Total == Prêmio Líquido + IOF?
           │
           ├───> [NÃO] -> REJEIÇÃO 422 UNPROCESSABLE (Violação LAW-113)
           │
           └───> [SIM] -> Emissão de Hash Criptográfico SUSEP & Certificação
                                    │
                                    ▼ (Ocorrência do Risco Coberto)
  [Companhia Seguradora]
           │
           │  2. Aviso de Sinistro (POST /api/v1/sro/apolices/{id}/sinistros)
           ▼
  [B3 SRO Engine V3] -> Registro do Aviso & Bloqueio Cautelar do LMG
           │
           │  3. Liquidação e Pagamento de Indenização (POST .../liquidar)
           ▼
  [B3 SRO Engine V3]
           │
           ├───> [Validação LAW-113 Teto LMG]
           │     ∑(Indenizações Liquidadas) ≤ Limite Máximo de Garantia?
           │
           └───> [APROVADO] -> Atualização de Reserva e Liquidação Homologada
```

---

### 5. Padrões de Mensageria da API REST SRO V3

#### 5.1 Registro de Apólice com Cosseguro
* **Endpoint:** `POST /api/v1/sro/apolices`
* **Content-Type:** `application/json`

**Exemplo de Payload de Requisição:**
```json
{
  "numero_proposta": "PROP-AGRO-2026-8841",
  "ramo": "RAMO_1101",
  "seguradora_lider_cnpj": "33000118000179",
  "seguradora_lider_nome": "PORTO SEGURO COMPANHIA DE SEGUROS GERAIS",
  "segurado_documento": "12345678000195",
  "segurado_nome": "AGROPECUARIA VALE VERDE LTDA",
  "data_emissao": "2026-11-08",
  "inicio_vigencia": "2026-11-08",
  "fim_vigencia": "2027-11-08",
  "limite_maximo_garantia": 5000000.00,
  "premio_liquido": 120000.00,
  "iof_aliquota_pct": 7.38,
  "cosseguradores": [
    {
      "cnpj": "33000118000179",
      "razao_social": "PORTO SEGURO CIA DE SEGUROS",
      "percentual_quota": 60.00,
      "is_lider": true
    },
    {
      "cnpj": "61074175000138",
      "razao_social": "MAPFRE SEGUROS GERAIS S.A.",
      "percentual_quota": 40.00,
      "is_lider": false
    }
  ]
}
```

**Exemplo de Resposta (HTTP 201 Created):**
```json
{
  "apolice_id": "APOL-1101-2026-000491",
  "numero_registro_susep": "SUSEP-REG-20261108-9841",
  "hash_susep_sha256": "8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92",
  "status": "REGISTRADA",
  "limite_maximo_garantia": 5000000.00,
  "premio_total": 128856.00,
  "iof_recolhido": 8856.00,
  "data_registro": "2026-11-08T13:50:00Z"
}
```

#### 5.2 Aviso e Liquidação de Sinistro
* **Aviso de Sinistro:** `POST /api/v1/sro/apolices/{apolice_id}/sinistros`
  ```json
  {
    "numero_sinistro": "SIN-2026-001",
    "data_ocorrencia": "2026-11-08",
    "valor_estimado": 750000.00,
    "causa_evento": "GRANIZO_GEADA_SECA"
  }
  ```
* **Liquidação de Sinistro:** `POST /api/v1/sro/apolices/{apolice_id}/sinistros/{sinistro_id}/liquidar`
  ```json
  {
    "valor_indenizado": 720000.00,
    "data_pagamento": "2026-11-08",
    "comprovante_spb": "STR2026110899201"
  }
  ```

---

### 6. Implementação Completa em Python com o Engine B3 SRO

```python
"""Exemplo de Integração com o Motor B3 SRO e Validação de Cosseguro."""
from decimal import Decimal
from src.sro_seguros_engine import (
    SroSegurosEngine,
    RamoSeguro,
    CosseguradorShare,
    SroCosseguroParticaoError,
    SroExcessoLmgSinistroError,
)

def executar_ciclo_sro():
    engine = SroSegurosEngine()

    # Partição de Cosseguro: Líder (60%) e Co-participante (40%) = 100.00%
    cosseguradores = [
        CosseguradorShare(
            cnpj="33000118000179",
            razao_social="PORTO SEGURO CIA DE SEGUROS",
            percentual_quota=Decimal("60.00"),
            is_lider=True
        ),
        CosseguradorShare(
            cnpj="61074175000138",
            razao_social="MAPFRE SEGUROS GERAIS S.A.",
            percentual_quota=Decimal("40.00"),
            is_lider=False
        )
    ]

    print("-> Registrando apólice no SRO com partição de risco...")
    apolice = engine.registrar_apolice(
        numero_proposta="PROP-AGRO-2026-8841",
        ramo=RamoSeguro.RURAL_AGRICOLA,
        seguradora_lider_cnpj="33000118000179",
        seguradora_lider_nome="PORTO SEGURO",
        segurado_documento="12345678000195",
        segurado_nome="AGROPECUARIA VALE VERDE LTDA",
        data_emissao="2026-11-08",
        inicio_vigencia="2026-11-08",
        fim_vigencia="2027-11-08",
        limite_maximo_garantia=Decimal("5000000.00"),
        premio_liquido=Decimal("120000.00"),
        iof_aliquota_pct=Decimal("7.38"),
        cosseguradores=cosseguradores
    )
    print(f"✔ Apólice registrada: {apolice.apolice_id} | Hash SUSEP: {apolice.hash_susep_sha256[:16]}...")

    # Registro de Aviso de Sinistro
    print("-> Registrando aviso de sinistro por evento climático...")
    sinistro = engine.registrar_sinistro(
        apolice_id=apolice.apolice_id,
        numero_sinistro="SIN-2026-001",
        data_ocorrencia="2026-11-08",
        valor_estimado=Decimal("800000.00"),
        causa_evento="TEMPESTADE_GRANIZO"
    )
    print(f"✔ Sinistro avisado com status: {sinistro.status.value}")

    # Liquidação do Sinistro
    print("-> Liquidando sinistro e debitando limite disponível...")
    sinistro_liquidado = engine.liquidar_sinistro(
        apolice_id=apolice.apolice_id,
        sinistro_id=sinistro.sinistro_id,
        valor_indenizado=Decimal("780000.00"),
        data_pagamento="2026-11-08"
    )
    print(f"✔ Sinistro liquidado! Valor pago: R$ {sinistro_liquidado.valor_indenizado}")

    # Testando barreira de contenção LAW-113: tentar liquidar sinistro superior ao LMG restante
    try:
        sinistro_excesso = engine.registrar_sinistro(
            apolice_id=apolice.apolice_id,
            numero_sinistro="SIN-FRAUD-002",
            data_ocorrencia="2026-11-08",
            valor_estimado=Decimal("6000000.00"),
            causa_evento="INCENDIO"
        )
        engine.liquidar_sinistro(
            apolice_id=apolice.apolice_id,
            sinistro_id=sinistro_excesso.sinistro_id,
            valor_indenizado=Decimal("6000000.00"),
            data_pagamento="2026-11-08"
        )
    except SroExcessoLmgSinistroError as e:
        print(f"🛡️ Defesa Sistêmica LAW-113 Ativada (Teto LMG Violado): {e}")

    # Verificação formal das leis de conservação
    inv = engine.verify_invariants()
    print("Relatório de Invariantes SRO:", inv)

if __name__ == "__main__":
    executar_ciclo_sro()
```

---

### 7. Invariante de Integridade LAW-113
$$\sum_{c \in \text{Cosseguradores}} \text{Quota}(c) = 100{,}0000\% \quad \land \quad \sum_{s \in \text{Sinistros Liquidados}} \text{Valor}(s) \le \text{LMG}$$

Essa relação de conservação assegura perante a SUSEP que 100% do risco está formalmente transferido para seguradoras autorizadas e solventes, sem lacunas de cobertura nem dupla securitização indevida.
