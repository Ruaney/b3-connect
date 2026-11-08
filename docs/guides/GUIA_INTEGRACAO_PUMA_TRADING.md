# Guia de Integração — PUMA Trading System B3
## Conexão FIX/SBE, Envio de Ordens e Leilão de Fechamento MOC (FL-001, FL-002, FL-098)

---

### 1. Visão Geral da Plataforma PUMA Trading
O **PUMA Trading System** é a plataforma de negociação eletrônica de alta frequência e ultra-baixa latência da B3. Desenvolvido sobre arquiteturas distribuídas e determinísticas, o PUMA processa todos os mercados listados no Brasil:
* **Mercado à Vista de Renda Variável:** Ações (Equities), BDRs, FIIs e ETFs.
* **Derivativos Financeiros e de Commodities:** Futuros de DI, Dólar (DOL/WDO), Ibovespa (IND/WIN), Opções e Commodities (Boi Gordo, Milho, Café).
* **Renda Fixa e Títulos Públicos:** Negociação de Debêntures, CRIs, CRAs e Títulos Públicos Federais (TPF).

A conectividade com o PUMA é segmentada em dois planos operacionais:
1. **Order Routing & Entry:** Envio, alteração e cancelamento de ordens e cotações através dos protocolos **FIX 4.4 / FIX 5.0 SP2** e **PUMA SBE v2 (Simple Binary Encoding)**.
2. **Market Data Feed:** Difusão multicast de cotações, profundidade de livro L2/L3 e trades via **UMDF 2.0 (Unified Market Data Feed)** com codificação SBE.

---

### 2. Protocolos de Comunicação: FIX vs. SBE

| Atributo | Protocolo FIX (4.4 / 5.0 SP2) | Protocolo PUMA SBE v2 |
|---|---|---|
| **Formato de Serialização** | Texto baseado em Tags delimitadas por SOH (`\x01`) | Binário puro (Little-Endian, structs de tamanho fixo/variável) |
| **Casos de Uso Primários** | Algoritmos institucionais, SORs, OMS, conexão via DMA1/DMA2 | Market Makers (PMM), High-Frequency Trading (HFT), DMA3/DMA4 |
| **Latência Típica de Parsing** | 1.5 a 4.0 microssegundos | Sub-microssegundo (150 a 450 nanossegundos) |
| **Sessão e Transporte** | TCP/IP sobre rede dedicada B3 (Rede de Acesso B3 - RA B3) | TCP/IP otimizado com kernel bypass (Solarflare OpenOnload / DPDK) |
| **Drop Copy e Clearing** | Drop Copy FIX padrão (MsgType=8) | Suportado via canais dedicados de supervisão |

---

### 3. Estabelecimento de Sessão FIX (Logon Handshake)

A conexão institucional exige autenticação mútua via TLS 1.3 ou VPN IPsec dedicada com IP público homologado na B3.

#### 3.1 Sequência de Conexão
1. **TCP Handshake:** Conexão no socket IP e Porta fornecidos pelo portal de conectividade da B3.
2. **Logon Message (MsgType = `A`):** O participante deve enviar imediatamente a mensagem de Logon como a primeira mensagem de sequência `MsgSeqNum = 1`.
3. **Logon Confirmation:** O PUMA responde com MsgType `A`. O participante está apto para negociar.
4. **Heartbeat (MsgType = `0`):** Troca periódica a cada `HeartBtInt` (padrão: 30 segundos).

#### 3.2 Estrutura da Mensagem de Logon FIX
```fix
8=FIX.4.4|9=112|35=A|49=CORRETORA_BR|56=PUMA_PROD|34=1|52=20261108-13:30:00.000|98=0|108=30|141=Y|553=USER_DMA|554=PASSWD_HASH|10=182|
```

* Campos Críticos:
  * `49 (SenderCompID)`: Identificador único do participante credenciado pela B3.
  * `56 (TargetCompID)`: Identificador do gateway PUMA (`PUMA_PROD` ou `PUMA_CERT`).
  * `34 (MsgSeqNum)`: Número sequencial da mensagem (estritamente crescente).
  * `98 (EncryptMethod)`: `0` (None / criptografia provida no nível de transporte TLS).
  * `108 (HeartBtInt)`: Intervalo em segundos para keepalive.
  * `141 (ResetSeqNumFlag)`: `Y` para reinício de sessão diária, `N` em reconexões intraday.

---

### 4. Envio e Ciclo de Vida de Ordens (New Order Single - MsgType = `D`)

O ciclo de vida da ordem é governado por confirmações determinísticas de execução (*Execution Reports* - MsgType `8`).

```
[Participante OMS / Algoritmo]
               │
               │  1. New Order Single (35=D, ClOrdID="ORD-2026-001")
               ▼
      [Gateway PUMA Trading]
               │
               ├─> [Sucesso] ──> Execution Report (35=8, ExecType=0, OrdStatus=0) [NEW]
               │                         │
               │                         ▼ (Casamento no Book)
               │                 Execution Report (35=8, ExecType=F, OrdStatus=2) [FILLED]
               │
               └─> [Erro / Risco] -> Execution Report (35=8, ExecType=8, OrdStatus=8) [REJECTED]
```

#### 4.1 Estrutura de Ordem Limitada Padrão (Exemplo Compra de PETR4)
```fix
8=FIX.4.4|9=178|35=D|49=CORRETORA_BR|56=PUMA_PROD|34=2|52=20261108-13:30:05.120|11=ORD-2026-001|1=CONTA_INVESTIDOR|55=PETR4|54=1|60=20261108-13:30:05.118|38=1000|40=2|44=38.50|59=0|10=220|
```

* Tags Essenciais:
  * `11 (ClOrdID)`: Identificador único do cliente para a ordem.
  * `1 (Account)`: Código da conta de custódia na B3 (CSD).
  * `55 (Symbol)`: Ticker negociado (ex: `PETR4`, `VALE3`, `WINZ26`).
  * `54 (Side)`: `1` = Compra (Buy), `2` = Venda (Sell).
  * `38 (OrderQty)`: Quantidade de ativos (deve respeitar o lote padrão, ex: 100 para ações).
  * `40 (OrdType)`: `1` = Market, `2` = Limit, `3` = Stop, `4` = Stop Limit.
  * `44 (Price)`: Preço unitário da ordem limitada (em BRL).
  * `59 (TimeInForce)`: `0` = Day, `1` = Good-Till-Cancel (GTC), `3` = Immediate-or-Cancel (IOC), `4` = Fill-or-Kill (FOK), `7` = At the Close.

---

### 5. Leilão de Fechamento (Closing Cross) e Ordens MOC/LOC (FL-098)

O Leilão de Fechamento da B3 apura o **Preço Teórico de Fechamento (PTP)**, utilizado mundialmente como referência para balanço patrimonial de fundos, cálculo de margens e liquidação de contratos futuros.

#### 5.1 Tipos de Ordens do Fechamento

1. **Market-On-Close (MOC):**
   * Ordem de mercado com validade restrita ao call de fechamento (`OrdType = 1`, `TimeInForce = 7`).
   * Não possui preço limite estipulado.
   * **Prioridade Absoluta:** As ordens MOC recebem prioridade máxima de casamento sobre quaisquer ordens com preço no mesmo sentido, garantindo execução preferencial.
2. **Limit-On-Close (LOC):**
   * Ordem com preço estipulado (`OrdType = 2`, `TimeInForce = 7`).
   * Executa apenas se o PTP apurado for igual ou mais favorável que seu preço limite.

#### 5.2 Janela de Congelamento (Freeze Window) e Proteção Anti-Manipulação (LAW-104A)
Nos últimos minutos do leilão (janela regulatória de 5 minutos antes da prorrogação randômica):
* **Proibição Estrita de Cancelamento:** Mensagens de cancelamento (`Order Cancel Request` - MsgType `F`) são **rejeitadas imediatamente** pelo motor com código `PumaFreezeWindowViolationError`.
* **Proibição de Piora de Condições:** Modificações que reduzam o volume da ordem ou piorem as condições de liquidez são bloqueadas. Apenas aumentos de quantidade são admitidos.
* **Prorrogação Randômica (Random Extension):** Duração variável de 0 a 180 segundos determinada aleatoriamente pelo algoritmo do PUMA para impedir que robôs de alta frequência executem spoofing no último milissegundo do fechamento.

#### 5.3 Algoritmo de Determinação do Preço Teórico (PTP)
Para cada nível de preço elegível, o motor calcula:
1. **Critério 1 — Maximização do Volume:** O PTP selecionado é aquele que maximiza o volume de ações transacionadas $\min(Q_{\text{buy}}(P), Q_{\text{sell}}(P))$.
2. **Critério 2 — Minimização do Desequilíbrio (Imbalance):** Havendo empate, escolhe-se o preço que minimiza o desbalanceamento $|Q_{\text{buy}}(P) - Q_{\text{sell}}(P)|$.
3. **Critério 3 — Proximidade do Preço de Referência:** Persistindo o empate, escolhe-se o preço mais próximo da última cotação ou preço de referência da sessão.

---

### 6. Exemplo de Implementação em Python (Client PUMA FIX)

```python
"""Exemplo de Envio de Ordem MOC e Acompanhamento de Execução no PUMA."""
from datetime import datetime, timezone
from decimal import Decimal
import socket
import time

class PumaFixClient:
    SOH = "\x01"

    def __init__(self, sender_comp_id: str, target_comp_id: str = "PUMA_PROD"):
        self.sender_comp_id = sender_comp_id
        self.target_comp_id = target_comp_id
        self.seq_num = 1

    def _build_fix_message(self, msg_type: str, fields: dict) -> str:
        header = [
            f"8=FIX.4.4",
            f"35={msg_type}",
            f"49={self.sender_comp_id}",
            f"56={self.target_comp_id}",
            f"34={self.seq_num}",
            f"52={datetime.now(timezone.utc).strftime('%Y%m%d-%H:%M:%S.%f')[:-3]}",
        ]
        body = [f"{tag}={val}" for tag, val in fields.items()]
        raw_msg = self.SOH.join(header + body) + self.SOH
        body_len = len(raw_msg.replace("8=FIX.4.4" + self.SOH, ""))
        raw_with_len = f"8=FIX.4.4{self.SOH}9={body_len}{self.SOH}" + raw_msg[raw_msg.index("35="):]
        
        # Checksum calculation (Tag 10)
        csum = sum(ord(c) for c in raw_with_len) % 256
        full_msg = f"{raw_with_len}10={csum:03d}{self.SOH}"
        self.seq_num += 1
        return full_msg

    def create_moc_order(self, cl_ord_id: str, symbol: str, side: int, qty: int, account: str) -> str:
        """Cria uma ordem Market-On-Close (MOC) para o Leilão de Fechamento B3."""
        fields = {
            11: cl_ord_id,
            1: account,
            55: symbol,
            54: side,  # 1=Buy, 2=Sell
            38: qty,
            40: 1,     # 1 = Market
            59: 7,     # 7 = At the Close (MOC)
            60: datetime.now(timezone.utc).strftime('%Y%m%d-%H:%M:%S.%f')[:-3],
        }
        return self._build_fix_message("D", fields)

# Demonstração
if __name__ == "__main__":
    client = PumaFixClient(sender_comp_id="BR_QUANT_PROP")
    moc_msg = client.create_moc_order(
        cl_ord_id="MOC-PETR4-20261108-001",
        symbol="PETR4",
        side=1,          # Compra
        qty=50000,       # 50.000 ações
        account="8819203"
    )
    print("Mensagem FIX MOC Gerada:")
    print(moc_msg.replace("\x01", " | "))
```

---

### 7. Salvaguardas Operacionais e Invariantes Formais
* **LAW-001 / LAW-002:** Conservação de Prioridade Price-Time no Book contínuo.
* **LAW-104A:** Inviolabilidade da Janela de Congelamento do Leilão de Fechamento (nenhum cancelamento aceito no período de restrição).
* **LAW-104B:** Prioridade Estrita de Ordem MOC sobre Ordens com Preço Limite no Cruzamento.
* **Cancel on Disconnect (COD):** Em caso de perda de heartbeat ou queda do socket TCP por mais de 3 segundos, o PUMA cancela automaticamente todas as ordens abertas da sessão, ressalvadas as ordens bloqueadas na Janela de Congelamento.
