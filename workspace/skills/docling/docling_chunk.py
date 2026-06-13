#!/usr/bin/env python3
import argparse
import json

from docling.chunking import HierarchicalChunker
from docling.document_converter import DocumentConverter


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--mode", choices=["hierarchical"], default="hierarchical")
    args = parser.parse_args()

    result = DocumentConverter().convert(args.source)
    chunker = HierarchicalChunker()
    chunks = list(chunker.chunk(result.document))
    out = []
    for chunk in chunks[:200]:
        out.append(
            {
                "text": getattr(chunk, "text", "")[:2000],
                "meta": getattr(chunk, "meta", None).model_dump() if getattr(chunk, "meta", None) else None,
            }
        )
    print(json.dumps({"count": len(chunks), "chunks": out}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
