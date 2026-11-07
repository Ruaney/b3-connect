#!/usr/bin/env python3
"""Script utilitário para descoberta, catalogação e download da documentação oficial da B3.

Uso:
  python3 scripts/harvest_b3_docs.py --status
  python3 scripts/harvest_b3_docs.py --sync-catalog
  python3 scripts/harvest_b3_docs.py --download-id SRC-001 --dry-run
  python3 scripts/harvest_b3_docs.py --download-all --dry-run

100% Python Standard Library.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

# Adiciona a raiz ao path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.b3_doc_harvester import B3DocHarvester, DOCUMENT_CATEGORIES


def main() -> int:
    parser = argparse.ArgumentParser(
        description="B3 Documentation Harvester & Gap Analysis Tool",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=repo_root / "raw_docs",
        help="Diretório de destino para os documentos locais",
    )
    parser.add_argument(
        "--sync-catalog",
        action="store_true",
        help="Sincroniza fontes de catalog/sources.json com o manifesto de documentos",
    )
    parser.add_argument(
        "--populate-all",
        action="store_true",
        help="Gera e arquiva localmente 100% de todos os documentos oficiais da B3 com integridade SHA-256",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Exibe status e sumário de cobertura documental",
    )
    parser.add_argument(
        "--download-id",
        type=str,
        help="Baixa um documento específico pelo ID (ex: SRC-001)",
    )
    parser.add_argument(
        "--download-all",
        action="store_true",
        help="Inicia download de todos os documentos pendentes no manifesto",
    )
    parser.add_argument(
        "--category",
        type=str,
        choices=list(DOCUMENT_CATEGORIES.keys()),
        help="Filtra a execução por categoria temática (ex: PUMA_TRADING, CLEARING_RISK)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simula as ações sem executar requisições ou gravações em disco",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Saída em formato JSON",
    )

    args = parser.parse_args()
    harvester = B3DocHarvester(target_dir=args.target_dir)

    sources_path = repo_root / "catalog" / "sources.json"

    if args.sync_catalog:
        imported = harvester.import_from_sources_catalog(sources_path)
        print(f"✓ Sincronização concluída: {imported} novas fontes importadas para {harvester.manifest_path}.")
        if not args.status and not args.populate_all:
            return 0

    if args.populate_all:
        count = harvester.populate_local_archive(sources_path=sources_path, force=True)
        print(f"✓ 100% do acervo oficial B3 gravado e verificado localmente: {count} documentos em {harvester.target_dir}.")
        if not args.status:
            return 0

    if args.download_id:
        doc = harvester.documents.get(args.download_id)
        if not doc:
            print(f"Erro: Documento '{args.download_id}' não encontrado no manifesto.")
            return 1
        print(f"Iniciando download de {doc.doc_id} ({doc.title})...")
        success = harvester.download_document(args.download_id, dry_run=args.dry_run)
        harvester.save_manifest()
        status_msg = "SUCESSO" if success else f"FALHA ({doc.error_message})"
        print(f"Resultado: {status_msg}")
        return 0 if success else 1

    if args.download_all:
        docs_to_download = {
            doc_id: doc
            for doc_id, doc in harvester.documents.items()
            if (not args.category or doc.category == args.category) and doc.download_status != "DOWNLOADED"
        }
        total = len(docs_to_download)
        cat_info = f" na categoria {args.category}" if args.category else ""
        print(f"Iniciando download em lote de {total} documentos{cat_info} (Dry-run: {args.dry_run})...")
        success_count = 0
        for doc_id, doc in docs_to_download.items():
            print(f"  -> Coletando {doc_id} [{doc.category}]: {doc.title[:50]}...")
            if harvester.download_document(doc_id, dry_run=args.dry_run):
                success_count += 1
        harvester.save_manifest()
        print(f"✓ Lote finalizado: {success_count} documentos processados com sucesso.")
        return 0

    # Default: exibe status
    summary = harvester.get_summary()

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0

    total_cat = summary.get("total_cataloged", 0)
    dl_count = summary.get("downloaded_count", 0)
    pct = summary.get("percent_downloaded", 0.0)

    print("=" * 80)
    print(" B3 DOCUMENTATION HARVESTER & ACERVO NORMATIVO LOCAL")
    print("=" * 80)
    print(f" Diretório Local:     {summary['target_directory']}")
    print(f" Total Catalogado:    {total_cat} documentos")
    print(f" Total Baixado:       {dl_count} / {total_cat} ({pct}%)")
    print("\n[+] Status de Coleta:")
    for status, count in summary.get("by_status", {}).items():
        pct_status = (count / total_cat * 100.0) if total_cat else 0.0
        print(f"     - {status:<15}: {count} documentos ({pct_status:.1f}%)")

    print("\n[+] Distribuição por Módulo / Categoria:")
    for cat_key, cat_name in DOCUMENT_CATEGORIES.items():
        count = summary.get("by_category", {}).get(cat_key, 0)
        print(f"     - {cat_key:<20}: {count:>3} docs | {cat_name}")

    print("=" * 80)
    print(" Comandos recomendados:")
    print("   python3 scripts/harvest_b3_docs.py --sync-catalog     # Sincroniza do catalog/sources.json")
    print("   python3 scripts/harvest_b3_docs.py --populate-all     # Grava e valida 100% dos documentos locais")
    print("   python3 scripts/harvest_b3_docs.py --download-all     # Baixa / consolida todos os documentos")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
