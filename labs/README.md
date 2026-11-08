# 🧪 B3 Integration Atlas — Laboratórios Práticos de Engenharia (`labs/`)

> **Um projeto de engenharia open-source mantido com o apoio da [Órbita Tecnologia (Orbita AI)](https://orbita-lovat.vercel.app) e integrado à [B3 Enterprise Platform](https://b3-enterprise-platform.vercel.app).**

Esta pasta contém **4 laboratórios executáveis passo a passo** (100% Python Standard Library, zero dependências externas) projetados para engenheiros de software, arquitetos de bolsa, quants e analistas de backoffice aprenderem na prática os protocolos e invariantes formais da B3.

---

## 🚀 Como Executar os Laboratórios

Na raiz do repositório `b3-integration-atlas`, execute qualquer laboratório diretamente com `python3`:

```bash
python3 labs/lab_01_puma_sbe_and_moc_auction.py
python3 labs/lab_02_imbarq_custody_reconciliation.py
python3 labs/lab_03_sng_gravames_and_sro_susep.py
python3 labs/lab_04_core_margin_and_waterfall.py
```

---

## 📚 Catálogo dos Laboratórios

| Lab | Arquivo | Tópicos Técnicos & Fluxos B3 | Leis Formais Provadas |
| :--- | :--- | :--- | :--- |
| **Lab 01** | `labs/lab_01_puma_sbe_and_moc_auction.py` | Framing Binário PUMA SBE v2 Little-Endian (28 bytes zero-copy via C-ABI) + Algoritmo de Leilão de Fechamento MOC (4 critérios de desempate) | `LAW-001`, `LAW-092`, `LAW-101` |
| **Lab 02** | `labs/lab_02_imbarq_custody_reconciliation.py` | Conciliação de Custódia Pós-Trade (Extratos Posicionais `IMBARQ006` / `IMBARQ012`) + Detecção Determinística de Quebras Contábeis | `LAW-008` |
| **Lab 03** | `labs/lab_03_sng_gravames_and_sro_susep.py` | SNG B3 Financiamentos (Ciclo de Vida de Gravames Veiculares CONTRAN 807 / Lei 14.711) + SRO B3 Seguros SUSEP V3 (Cosseguro 100% e Trava de LMG) | `LAW-112`, `LAW-113` |
| **Lab 04** | `labs/lab_04_core_margin_and_waterfall.py` | Metodologia CORE da Câmara B3 (VaR 99,5%, Expected Shortfall Cornish-Fisher, Haircuts de TPF no Selic) + Cascata de Salvaguardas CCP (Default Waterfall) | `LAW-003`, `LAW-006`, `LAW-105A`, `LAW-106B` |

---

## 🔗 Guias Técnicos Associados (B3 Enterprise Platform)

- [Como Integrar ao PUMA Trading System da B3: FIX 4.4 vs SBE Binário e Leilão MOC](https://b3-enterprise-platform.vercel.app/artigos/como-integrar-puma-trading-system-b3-fix-sbe.html)
- [Conciliação Automática de Custódia e Posições B3 (IMBARQ006, IMBARQ012 e iMercado)](https://b3-enterprise-platform.vercel.app/artigos/conciliacao-automatica-imbarq006-imbarq012-b3.html)
- [Integração SNG B3: Automação de Inclusão e Baixa de Gravames Veiculares](https://b3-enterprise-platform.vercel.app/artigos/integracao-sng-b3-gravames-veiculos-detran-api.html)
- [SRO B3 e SUSEP Circular 642: Registro Automático de Apólices, Endossos e Sinistros](https://b3-enterprise-platform.vercel.app/artigos/registro-apolices-sro-susep-b3-seguradoras.html)
- [Metodologia CORE da Câmara B3: Cálculo de Margem Intradiária, Haircuts de TPF e Waterfall](https://b3-enterprise-platform.vercel.app/artigos/calculo-margem-core-b3-default-waterfall-risco.html)
