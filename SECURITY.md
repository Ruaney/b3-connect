# Política de Segurança e Divulgação Coordenada de Vulnerabilidades (SECURITY.md)

O **B3 Integration Atlas** é um projeto de código aberto dedicado à infraestrutura crítica de mercado financeiro. Levamos a segurança da informação, a confiabilidade de execução e a integridade matemática de nossos módulos com a mais alta prioridade.

Este documento formaliza nossa **Política de Divulgação Coordenada de Vulnerabilidades (Coordinated Vulnerability Disclosure - CVD)** e define nossos **Acordos de Nível de Serviço (SLA) de resposta AppSec**.

---

## 1. Versões com Suporte de Segurança

Atualmente, atualizações de segurança e correções de vulnerabilidade são fornecidas para as seguintes versões:

| Versão | Suportada | Notas de Manutenção |
|---|---|---|
| `v1.2.x` (Atual / Main) | Sim | Correções ativas, patches de emergência e revisões AppSec |
| `< v1.2.0` | Não | Recomendada atualização imediata para a versão mais recente |

---

## 2. Como Reportar uma Vulnerabilidade com Segurança

**NÃO crie issues públicas no GitHub para relatar potenciais vulnerabilidades de segurança.**

Se você identificou uma falha de segurança, vulnerabilidade de parsing, bypass de invariante de compensação ou potencial vazamento de dados, utilize um dos seguintes canais confidenciais:

1. **GitHub Security Advisory (Recomendado)**:
   Acesse a aba **Security** do repositório no GitHub e selecione **"Report a vulnerability"** para abrir um canal privado e criptografado diretamente com a equipe de mantenedores e AppSec.
2. **E-mail de Segurança**:
   Envie detalhes técnicos para: **security@b3-integration-atlas.org**.
   Se desejar criptografar a mensagem com PGP/GPG, solicite a chave pública de segurança ou utilize nossa chave oficial listada no repositório.

### Informações Recomendadas no Relatório
Para agilizar a triagem e o desenvolvimento da correção, inclua:
- Descrição detalhada do vetor de ataque ou falha de validação.
- Módulo e fluxo afetados (e.g., `src/puma_sbe.py`, `src/imbarq004.py`, `FL-013`).
- Prova de Conceito (PoC) reproduzível ou fixture sintética que engatilha o comportamento anômalo.
- Avaliação estimada de impacto e severidade (CVSS v3.1 sugerido).

---

## 3. SLA de Resposta e Ciclo de Vida AppSec

Nossa equipe técnica compromete-se com os seguintes prazos rigorosos para triagem, comunicação e liberação de correções (patches):

| Etapa do Incidente | SLA Máximo | Ação Executada |
|---|---|---|
| **Primeira Resposta / Confirmação** | **< 24 horas** | Confirmação de recebimento e designação do analista responsável. |
| **Triagem & Avaliação de Severidade** | **< 72 horas** | Reprodução em ambiente controlado, cálculo formal do score CVSS v3.1 e determinação de impacto. |
| **Comunicação de Status** | **Semanal** | Atualização de progresso ao pesquisador que reportou o achado. |

### SLA de Correção e Publicação de Patch (Hotfix)

O tempo limite para disponibilização da correção na branch principal e emissão de release depende da severidade CVSS v3.1:

| Nível de Severidade | CVSS v3.1 Score | SLA Máximo de Correção |
|---|---|---|
| **Crítico (Critical)** | **9.0 – 10.0** | **Até 7 dias corridos** |
| **Alto (High)** | **7.0 – 8.9** | **Até 14 dias corridos** |
| **Médio (Medium)** | **4.0 – 6.9** | **Até 30 dias corridos** |
| **Baixo (Low)** | **0.1 – 3.9** | **Até 60 dias corridos** |

---

## 4. Escopo de Segurança

### Em Escopo (In-Scope)
- **Segurança de Deserialização e Parsing**:
  - Buffer overflow, memory corruption e out-of-bounds reading nos codecs binários (`struct.unpack` em `src/puma_sbe.py` e `src/umdf_market_data.py`).
  - Integer overflow, bypass de comprimento ou denial of service (DoS) através de arquivos planos malformados (parsers posicionais IMBARQ).
- **Integridade de Invariantes e Aritmética Financeira**:
  - Falhas de conservação de custódia, divergência no balanço de caixa multilateral ou bypass na cascata de inadimplência (CCP default waterfall).
  - Erros de truncamento ou perda de precisão decimal que permitam arbitragem artificial ou criação espúria de saldo.
- **Protocolos de Comunicação e Assinatura**:
  - Validação de certificados mTLS (ICP-Brasil), autenticação SAS tokens iMercado e verificação criptográfica de hashes SHA-256 de payloads.
- **Isolamento de Estado**:
  - Concorrência de threads e race conditions no motor de reconciliação ou no livro de ofertas L3 (`MarketByOrderL3Engine`).

### Fora de Escopo (Out-of-Scope)
- Infraestrutura física de servidores de terceiros ou da B3 S.A.
- Engenharia social, phishing ou ataques físicos contra os mantenedores.
- Negação de serviço distribuída (DDoS) contra sites públicos de demonstração.
- Vulnerabilidades em interpretadores Python desatualizados não homologados (exigido Python 3.10+).

---

## 5. Diretrizes de Pesquisa Segura (Safe Harbor)

Incentivamos a pesquisa ética de segurança. Comprometemo-nos a não tomar medidas legais contra pesquisadores que:
- Realizarem seus testes exclusivamente contra ambientes locais, repositórios clonados e fixtures sintéticas (nunca contra ambientes produtivos da B3 ou de terceiros).
- Não destruírem, modificarem ou acessarem dados de terceiros.
- Concederem um período razoável de divulgação coordenada (coordenada com nossos SLAs) antes de tornar qualquer detalhe público.
- Agirem de boa-fé em conformidade com as leis vigentes e este guia.
