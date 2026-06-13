#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
usage: run_deep_research_workflow.sh <doctor|init> [args...]

Subcommands:
  doctor
      verify the deep-research templates exist

  init [--dir DIR] [--subdir NAME] [--force]
      initialize scaffold files:
        <DIR>/<NAME>/sources.md
        <DIR>/<NAME>/analysis.md
        <DIR>/<NAME>/report.md
EOF
}

ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
TEMPLATE_DIR="$ROOT/templates"

sources_tpl="$TEMPLATE_DIR/deep-research-sources.md"
analysis_tpl="$TEMPLATE_DIR/deep-research-analysis.md"
report_tpl="$TEMPLATE_DIR/deep-research-report.md"

cmd="${1:-}"
if [[ -z "$cmd" ]]; then
  usage >&2
  exit 1
fi
shift || true

case "$cmd" in
  doctor)
    missing=0
    for f in "$sources_tpl" "$analysis_tpl" "$report_tpl"; do
      if [[ -f "$f" ]]; then
        printf 'OK\t%s\n' "$f"
      else
        printf 'MISSING\t%s\n' "$f" >&2
        missing=1
      fi
    done
    exit "$missing"
    ;;

  init)
    target_dir="."
    subdir="research"
    force=0
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --dir)
          target_dir="${2:?--dir requires a value}"
          shift 2
          ;;
        --subdir)
          subdir="${2:?--subdir requires a value}"
          shift 2
          ;;
        --force)
          force=1
          shift
          ;;
        -h|--help)
          usage
          exit 0
          ;;
        *)
          echo "unknown argument: $1" >&2
          usage >&2
          exit 1
          ;;
      esac
    done

    out_dir="$target_dir/$subdir"
    mkdir -p "$out_dir"

    declare -A mapping=(
      ["$sources_tpl"]="$out_dir/sources.md"
      ["$analysis_tpl"]="$out_dir/analysis.md"
      ["$report_tpl"]="$out_dir/report.md"
    )

    for src in "$sources_tpl" "$analysis_tpl" "$report_tpl"; do
      dst="${mapping[$src]}"
      if [[ ! -f "$src" ]]; then
        echo "missing template: $src" >&2
        exit 1
      fi
      if [[ -e "$dst" && "$force" -ne 1 ]]; then
        echo "refusing to overwrite existing file without --force: $dst" >&2
        exit 1
      fi
      cp "$src" "$dst"
      printf 'WROTE\t%s\n' "$dst"
    done
    ;;

  -h|--help)
    usage
    ;;

  *)
    echo "unknown subcommand: $cmd" >&2
    usage >&2
    exit 1
    ;;
esac
