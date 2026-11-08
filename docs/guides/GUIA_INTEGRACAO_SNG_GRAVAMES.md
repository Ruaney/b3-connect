# Guia de Integração — Sistema Nacional de Gravames (SNG)
## Inclusão, Consulta e Baixa de Gravames Veiculares junto aos DETRANs (FL-106)

---

### 1. Visão Geral do SNG (B3 Financiamentos)
O **Sistema Nacional de Gravames (SNG)** é a infraestrutura tecnológica mantida pela B3 que centraliza e gerencia a anotação, consulta e liberação de garantias reais incidentes sobre veículos automotores em todo o território nacional.

O SNG atua como um barramento de integração crítica interligando:
* **Credores:** Instituições financeiras, bancos de montadoras, sociedades de crédito direto (SCD), administradoras de consórcios e cooperativas de crédito.
* **Órgãos Executivos de Trânsito:** Departamentos Estaduais de Trânsito (**DETRANs**) das 27 Unidades da Federação.
* **Bases Centrais Governamentais:** RENAVAM (Registro Nacional de Veículos Automotores) e SENATRAN (Secretaria Nacional de Trânsito).

---

### 2. Base Regulatória e Normas Aplicáveis
* **Resolução CONTRAN nº 807/2020:** Regulamenta o registro de contratos de garantias com cláusula de alienação fiduciária, arrendamento mercantil, reserva de domínio ou penhor nos órgãos executivos de trânsito.
* **Artigo 1.361 do Código Civil Brasileiro:** Institui o regime de alienação fiduciária em garantia sobre coisas móveis infungíveis.
* **Lei Federal nº 13.111/2015:** Disciplina a obrigatoriedade da prestação de informações ao consumidor relativas à situação cadastral, débitos tributários e gravames do veículo.
* **Lei Federal nº 14.711/2023 (Marco Legal das Garantias):** Permite a gestão eficiente de garantias e execução extrajudicial simplificada.
* **Invariante LAW-112 da B3:** Unicidade de Gravame Ativo de 1º Grau por Veículo (Inviolabilidade contra sobreposição de gravames conflitantes).

---

### 3. Modalidades Contratuais Homologadas

| Modalidade | Código SNG | Descrição Jurídica | Propriedade no Documento |
|---|---|---|---|
| **Alienação Fiduciária (CDC)** | `ALIENACAO_FIDUCIARIA` | Financiamento direto ao consumidor onde a propriedade resolúvel é transferida ao credor até a quitação integral. | Credor Fiduciário (Propriedade Resolúvel) / Devedor (Posse Direta) |
| **Arrendamento Mercantil (Leasing)** | `ARRENDAMENTO_MERCANTIL` | Aluguel mercantil do veículo com opção irrevogável de compra ou devolução ao final do prazo contratual. | Arrendadora (Propriedade) / Arrendatário (Posse Direta) |
| **Reserva de Domínio** | `RESERVA_DOMINIO` | Cláusula contratual de compra e venda onde o vendedor retém o domínio do bem até o pagamento total do preço. | Vendedor (Domínio) / Comprador (Posse) |
| **Penhor Veicular** | `PENHOR` | Vinculação material de veículo livre para garantia de dívida civil ou comercial averbada. | Devedor (Propriedade com Ônus) |

---

### 4. Ciclo Operacional Completo Passo a Passo

```
[Instituição Financeira / Credor]
                │
                │  1. Inclusão de Gravame (REST POST /api/v1/sng/gravames)
                ▼
      [B3 SNG Engine / Barramento]
                │
                ├───> [Checagem LAW-112] Chassi já possui gravame ATIVO?
                │            │
                │            ├──> [SIM] -> REJEIÇÃO 409 CONFLICT (Violação LAW-112)
                │            │
                │            └──> [NÃO] -> Registro Local & Emissão de Protocolo B3
                │                                    │
                │                                    ▼
                │                  [Barramento Mensageria DETRAN]
                │                                    │
                │                                    ▼
                │                      [DETRAN Estadual de Emplacamento]
                │                      (Anotação no CRLV-e / Bloqueio de Venda)
                │
                │  2. Quitação do Financiamento pelo Cliente
                ▼
      [Instituição Financeira / Credor]
                │
                │  3. Baixa de Gravame (REST POST /api/v1/sng/gravames/{id}/baixa)
                ▼
      [B3 SNG Engine]
                │
                └───> [Transição: ATIVO -> BAIXADO]
                                │
                                ▼
                      [Comunicação Imediata DETRAN]
                      (Desalienação no CRLV-e / Liberação para Transferência)
```

---

### 5. Padrões de Mensageria da API REST SNG

#### 5.1 Inclusão de Gravame (Registro de CDC)
* **Endpoint:** `POST /api/v1/sng/gravames`
* **Content-Type:** `application/json`

**Exemplo de Payload de Requisição:**
```json
{
  "contrato_numero": "FIN-2026-994821",
  "modalidade": "ALIENACAO_FIDUCIARIA",
  "credor_cnpj": "60701190000104",
  "credor_nome": "BANCO ITAU CONSIGNADO S.A.",
  "devedor_documento": "12345678909",
  "devedor_nome": "CARLOS EDUARDO ALMEIDA",
  "veiculo": {
    "chassi": "9BWAA05U1HP001234",
    "renavam": "11984726351",
    "placa": "BRA2E19",
    "uf_emplacamento": "SP",
    "marca_modelo": "VW POLO HIGHLINE 200 TSI",
    "ano_fabricacao": 2024,
    "ano_modelo": 2025
  },
  "valor_financiamento": 85000.00,
  "quantidade_parcelas": 48,
  "taxa_juros_anual_pct": 14.85
}
```

**Exemplo de Resposta de Sucesso (HTTP 201 Created):**
```json
{
  "gravame_id": "GRV-SP-20261108-001234",
  "protocolo_b3": "B3-SNG-20261108-4829104",
  "status": "ATIVO",
  "chassi": "9BWAA05U1HP001234",
  "renavam": "11984726351",
  "data_inclusao": "2026-11-08T13:45:10Z",
  "detran_status": "NOTIFICADO",
  "mensagem": "Gravame incluído e anotado com sucesso no SNG B3 Financiamentos."
}
```

#### 5.2 Baixa de Gravame (Desalienação Comercial)
* **Endpoint:** `POST /api/v1/sng/gravames/{gravame_id}/baixa`
* **Prazo Regulatório:** De acordo com o Art. 9º da Resolução CONTRAN 807/2020, o credor deve promover a baixa no prazo impreterível de **até 10 (dez) dias corridos** contados da quitação das obrigações contratuais.

**Exemplo de Payload de Baixa:**
```json
{
  "motivo_baixa": "QUITACAO_INTEGRAL_CONTRATO",
  "data_quitacao": "2026-11-08T10:00:00Z",
  "operador_matricula": "OP88192"
}
```

---

### 6. Implementação Completa em Python com o Engine B3

```python
"""Exemplo de Integração Direta com o Motor de Gravames B3 SNG."""
from decimal import Decimal
from src.sng_gravames_engine import (
    SngGravamesEngine,
    ModalidadeGravame,
    VeiculoIdentificacao,
    SngGravameDuplicadoError,
)

def executar_ciclo_sng():
    # Inicializa o motor institucional
    engine = SngGravamesEngine()

    veiculo = VeiculoIdentificacao(
        chassi="9BWAA05U1HP001234",
        renavam="11984726351",
        placa="BRA2E19",
        uf_emplacamento="SP",
        marca_modelo="VW POLO HIGHLINE 200 TSI",
        ano_fabricacao=2024,
        ano_modelo=2025
    )

    # 1. Registro do Gravame de Financiamento
    print("-> Registrando gravame de Alienação Fiduciária...")
    gravame = engine.registrar_gravame(
        contrato_numero="FIN-2026-994821",
        modalidade=ModalidadeGravame.ALIENACAO_FIDUCIARIA,
        credor_cnpj="60701190000104",
        credor_nome="BANCO ITAU S.A.",
        devedor_documento="12345678909",
        devedor_nome="CARLOS EDUARDO ALMEIDA",
        veiculo=veiculo,
        valor_financiamento=Decimal("85000.00"),
        quantidade_parcelas=48,
        taxa_juros_anual_pct=Decimal("14.85")
    )
    print(f"✔ Gravame registrado com ID: {gravame.gravame_id} | Protocolo: {gravame.protocolo_b3}")

    # 2. Tentativa de inclusão de segundo gravame ativo no mesmo chassi (Testando LAW-112)
    try:
        engine.registrar_gravame(
            contrato_numero="FIN-FRAUD-001",
            modalidade=ModalidadeGravame.ALIENACAO_FIDUCIARIA,
            credor_cnpj="00000000000191",
            credor_nome="BANCO CONCORRENTE S.A.",
            devedor_documento="12345678909",
            devedor_nome="CARLOS EDUARDO ALMEIDA",
            veiculo=veiculo,
            valor_financiamento=Decimal("50000.00"),
            quantidade_parcelas=36,
            taxa_juros_anual_pct=Decimal("15.50")
        )
    except SngGravameDuplicadoError as e:
        print(f"🛡️ Defesa Sistêmica LAW-112 Ativada: {e}")

    # 3. Baixa de Gravame após liquidação da dívida
    print("-> Realizando baixa do gravame no SNG...")
    gravame_baixado = engine.baixar_gravame(
        gravame_id=gravame.gravame_id,
        motivo="QUITACAO_TOTAL"
    )
    print(f"✔ Gravame baixado com sucesso! Status final: {gravame_baixado.status.value}")

    # 4. Verificação de Invariantes Formais
    relatorio_invariantes = engine.verify_invariants()
    print("Relatório de Conformidade Sistêmica:", relatorio_invariantes)

if __name__ == "__main__":
    executar_ciclo_sng()
```

---

### 7. Invariante de Integridade LAW-112
$$\forall v \in \text{Veículos}, \quad \sum_{g \in \text{Gravames}(v)} \mathbb{I}(\text{status}(g) = \text{ATIVO}) \le 1$$

Nenhum veículo registrado na base nacional pode conter simultaneamente mais de uma garantia ativa de primeiro grau. Violações dessa regra são rejeitadas em nível atômico pela B3 antes de qualquer notificação aos Detrans, prevenindo fraudes de financiamento duplicado e garantindo a higidez do Sistema Financeiro Nacional.
