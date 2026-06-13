#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption


def build_converter(args: argparse.Namespace) -> DocumentConverter:
    if args.pipeline == "standard":
        opts = PdfPipelineOptions(do_ocr=args.ocr, do_table_structure=args.tables)
        return DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )
    return DocumentConverter()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--to", choices=["md", "json", "text", "html"], default="md")
    parser.add_argument("--pipeline", choices=["standard", "auto"], default="standard")
    parser.add_argument("--ocr", dest="ocr", action="store_true")
    parser.add_argument("--no-ocr", dest="ocr", action="store_false")
    parser.set_defaults(ocr=True)
    parser.add_argument("--tables", dest="tables", action="store_true")
    parser.add_argument("--no-tables", dest="tables", action="store_false")
    parser.set_defaults(tables=True)
    parser.add_argument("--output")
    args = parser.parse_args()

    conv = build_converter(args)
    result = conv.convert(args.source)
    if args.to == "json":
        text = json.dumps(result.document.export_to_dict(), ensure_ascii=False, indent=2)
    elif args.to == "html":
        text = result.document.export_to_html()
    elif args.to == "text":
        text = result.document.export_to_text()
    else:
        text = result.document.export_to_markdown()

    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
