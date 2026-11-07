# EXEMPLOS DIDÁTICOS PRÁTICOS — B3 INTEGRATION ATLAS

Esta pasta contém scripts executáveis, didáticos e autocontidos que ilustram na prática o funcionamento técnico dos principais subsistemas da B3 S.A.

Todos os scripts utilizam **100% Python Standard Library** (zero dependências `pip`), executam estritamente em ambiente local e validam formalmente as leis de conservação matemática do Atlas.

---

## ÍNDICE DE EXEMPLOS

| Arquivo | Tema Abordado | Módulo B3 | Lei Formal |
| :--- | :--- | :--- | :--- |
| [`01_como_enviar_ordem_puma_fix.py`](file:///mnt/j/Documents/github/b3-integration-atlas/examples/01_como_enviar_ordem_puma_fix.py) | Envio de ordem FIX 4.4/5.0, túnel pré-trade e rejeição de Fat-Finger | PUMA Trading System | LAW-093, LAW-001 |
| [`02_como_funciona_o_balcao_nome.py`](file:///mnt/j/Documents/github/b3-integration-atlas/examples/02_como_funciona_o_balcao_nome.py) | Registro de CDB e trava de gravame em recebíveis de cartão (CMN 4.734) | Balcão NoMe / Cetip | LAW-011, LAW-018 |
| [`03_calculando_margem_core_na_pratica.py`](file:///mnt/j/Documents/github/b3-integration-atlas/examples/03_calculando_margem_core_na_pratica.py) | Metodologia CORE, estresse de preços, correlação e haircuts de colateral | Câmara B3 & Risco | LAW-005, LAW-006 |
| [`04_conciliacao_arquivos_imbarq.py`](file:///mnt/j/Documents/github/b3-integration-atlas/examples/04_conciliacao_arquivos_imbarq.py) | Leitura de IMBARQ012, incomunicabilidade patrimonial CPF e conciliação | Central Depositária (CSD) | LAW-008, LAW-034 |
| [`05_liquidacao_drex_com_contratos_htlc.py`](file:///mnt/j/Documents/github/b3-integration-atlas/examples/05_liquidacao_drex_com_contratos_htlc.py) | DvP Atômico Drex Fase 2: TPFt (ERC-1155) vs Real Digital via HTLC | SPB, BACEN & Drex | LAW-089A |

---

## COMO EXECUTAR CADA EXEMPLO

Você pode rodar qualquer um dos scripts diretamente no terminal a partir da raiz do repositório:

```bash
# Exemplo 1: PUMA FIX e Túneis de Preço
python3 examples/01_como_enviar_ordem_puma_fix.py

# Exemplo 2: Balcão NoMe e Recebíveis de Cartão
python3 examples/02_como_funciona_o_balcao_nome.py

# Exemplo 3: Risco CORE e Margens de Colateral
python3 examples/03_calculando_margem_core_na_pratica.py

# Exemplo 4: Conciliação com Arquivos IMBARQ da Depositária
python3 examples/04_conciliacao_arquivos_imbarq.py

# Exemplo 5: Liquidação Atômica Drex Fase 2 via HTLC
python3 examples/05_liquidacao_drex_com_contratos_htlc.py
```
