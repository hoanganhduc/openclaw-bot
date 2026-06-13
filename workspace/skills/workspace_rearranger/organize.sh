#!/usr/bin/env bash
set -euo pipefail

MODE="dry-run"
SCOPE="workspace"
MOVE_POLICY="report-only"
ROOTS_CSV=""
FOCUS_OVERRIDE=""
CONFIRM_APPLY="no"
WORKSPACE_ROOT="/workspace"

usage() {
  cat <<'USAGE'
Usage:
  organize.sh [options]

Options:
  --workspace-root PATH     Workspace root inside sandbox. Default: /workspace
  --mode MODE               dry-run | apply | status | undo-last
  --scope SCOPE             staging | workspace | custom
  --move-policy POLICY      report-only | safe | expanded
  --roots CSV               Comma-separated custom roots (for scope=custom)
  --focus NAME              Focus override for this run
  --confirm-apply yes       Required for mode=apply and mode=undo-last
  --help                    Show this help
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --workspace-root) WORKSPACE_ROOT="$2"; shift 2 ;;
    --mode) MODE="$2"; shift 2 ;;
    --scope) SCOPE="$2"; shift 2 ;;
    --move-policy) MOVE_POLICY="$2"; shift 2 ;;
    --roots) ROOTS_CSV="$2"; shift 2 ;;
    --focus) FOCUS_OVERRIDE="$2"; shift 2 ;;
    --confirm-apply) CONFIRM_APPLY="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
done

case "$MODE" in dry-run|apply|status|undo-last) ;; *) echo "Invalid --mode: $MODE" >&2; exit 1 ;; esac
case "$SCOPE" in staging|workspace|custom) ;; *) echo "Invalid --scope: $SCOPE" >&2; exit 1 ;; esac
case "$MOVE_POLICY" in report-only|safe|expanded) ;; *) echo "Invalid --move-policy: $MOVE_POLICY" >&2; exit 1 ;; esac

if { [ "$MODE" = "apply" ] || [ "$MODE" = "undo-last" ]; } && [ "$CONFIRM_APPLY" != "yes" ]; then
  echo "Refusing to proceed: mode=$MODE requires --confirm-apply yes" >&2
  exit 2
fi

if [ ! -d "$WORKSPACE_ROOT" ]; then
  echo "Workspace root does not exist: $WORKSPACE_ROOT" >&2
  exit 1
fi

SKILL_BASE="$(cd "$(dirname "$0")" && pwd -P)"
CONTROL_DIR="$WORKSPACE_ROOT/_control"
LOG_DIR="$WORKSPACE_ROOT/_logs/organizer"
TRIAGE_DIR="$WORKSPACE_ROOT/_triage"
CONFIG_FILE="$CONTROL_DIR/workspace_rearranger.conf"
LOCK_DIR="$CONTROL_DIR/workspace_rearranger.lock"
RUN_ID="$(date +%Y%m%d_%H%M%S)"
RUN_FILE="$LOG_DIR/runs/$RUN_ID.txt"
MANIFEST_FILE="$LOG_DIR/manifests/$RUN_ID.tsv"
LATEST_REPORT="$LOG_DIR/latest_report.txt"

mkdir -p "$CONTROL_DIR" "$LOG_DIR/runs" "$LOG_DIR/manifests" "$TRIAGE_DIR" "$WORKSPACE_ROOT/inbox" "$WORKSPACE_ROOT/unsorted" "$WORKSPACE_ROOT/shared/references" "$WORKSPACE_ROOT/projects"

ACTIVE_FOCUS=""
AUTO_SCORE=90
TRIAGE_SCORE=65
RELOCATION_MARGIN=20
TEXT_MAX_BYTES=65536
STAGING_ROOTS=("inbox" "unsorted")
PROTECTED_DIRS=("skills" "_control" "_logs" "_triage" "archive" ".git" ".venv" "venv" "node_modules" "__pycache__" ".mypy_cache" ".pytest_cache" "build" "dist")
SKIP_EXTS=("zip" "tar" "gz" "7z" "png" "jpg" "jpeg" "gif" "webp" "mp4" "mov" "avi" "mkv" "exe" "bin" "class" "o" "so" "dll")
TEXT_EXTS=("tex" "bib" "bbl" "cls" "sty" "py" "sage" "ipynb" "sh" "md" "txt" "org" "rst" "json" "yml" "yaml" "csv" "tsv")

if [ -f "$CONFIG_FILE" ]; then
  # shellcheck source=/dev/null
  source "$CONFIG_FILE"
fi
# Validate numeric config values
for var in AUTO_SCORE TRIAGE_SCORE RELOCATION_MARGIN TEXT_MAX_BYTES; do
  if ! [[ "${!var}" =~ ^[0-9]+$ ]]; then
    echo "ERROR: $var must be a positive integer, got '${!var}'" >&2
    exit 1
  fi
done

if [ -n "$FOCUS_OVERRIDE" ]; then
  ACTIVE_FOCUS="$FOCUS_OVERRIDE"
fi

cleanup_lock() {
  rmdir "$LOCK_DIR" 2>/dev/null || true
}

acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$RUN_ID" > "$LOCK_DIR/run_id"
    trap cleanup_lock EXIT INT TERM
    return 0
  fi
  # Check for stale lock (older than 1 hour)
  local lock_age=0
  if [ -f "$LOCK_DIR/run_id" ]; then
    local lock_mtime
    lock_mtime=$(stat -c %Y "$LOCK_DIR/run_id" 2>/dev/null || echo 0)
    local now
    now=$(date +%s)
    lock_age=$(( now - lock_mtime ))
  fi
  if [ "$lock_age" -gt 3600 ]; then
    echo "Removing stale lock (age: ${lock_age}s)" >&2
    rm -f "$LOCK_DIR/run_id"
    rmdir "$LOCK_DIR" 2>/dev/null || true
    if mkdir "$LOCK_DIR" 2>/dev/null; then
      printf '%s\n' "$RUN_ID" > "$LOCK_DIR/run_id"
      trap cleanup_lock EXIT INT TERM
      return 0
    fi
  fi
  echo "Another organizer run appears to be active. Lock: $LOCK_DIR" >&2
  exit 3
}

abs_real() {
  local p="$1"
  if [ -e "$p" ]; then
    (cd "$(dirname "$p")" && printf '%s/%s\n' "$(pwd -P)" "$(basename "$p")")
  else
    local parent
    parent="$(cd "$(dirname "$p")" && pwd -P)"
    printf '%s/%s\n' "$parent" "$(basename "$p")"
  fi
}

rel_from_workspace() {
  local abs="$1"
  abs="$(abs_real "$abs")"
  case "$abs" in
    "$WORKSPACE_ROOT") printf '.\n' ;;
    "$WORKSPACE_ROOT"/*) printf '%s\n' "${abs#$WORKSPACE_ROOT/}" ;;
    *) printf '%s\n' "$abs" ;;
  esac
}

lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

normalize_words() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr '_-/' '   ' | tr -cs '[:alnum:]' ' ' | sed 's/^ *//;s/ *$//'
}

is_hidden_component() {
  local rel="$1"
  IFS='/' read -r -a parts <<< "$rel"
  local part
  for part in "${parts[@]}"; do
    [ -z "$part" ] && continue
    case "$part" in
      .|..)
        continue ;;
      .* )
        return 0 ;;
    esac
  done
  return 1
}

is_protected_rel() {
  local rel="$1"
  local top="${rel%%/*}"
  local d
  for d in "${PROTECTED_DIRS[@]}"; do
    [ "$top" = "$d" ] && return 0
  done
  if is_hidden_component "$rel"; then
    return 0
  fi
  return 1
}

is_staging_rel() {
  local rel="$1"
  local s
  for s in "${STAGING_ROOTS[@]}"; do
    case "$rel" in
      "$s"|"$s"/*) return 0 ;;
    esac
  done
  return 1
}

is_supported_text_ext() {
  local ext="$1"
  local x
  for x in "${TEXT_EXTS[@]}"; do
    [ "$ext" = "$x" ] && return 0
  done
  return 1
}

is_skipped_ext() {
  local ext="$1"
  local x
  for x in "${SKIP_EXTS[@]}"; do
    [ "$ext" = "$x" ] && return 0
  done
  return 1
}

route_subdir() {
  local base_lc="$1"
  case "$base_lc" in
    *.tex|*.bib|*.bbl|*.cls|*.sty) printf 'tex\n' ;;
    *.py|*.sage|*.ipynb|*.sh) printf 'scripts\n' ;;
    *review*|*referee*|*comment*|*comments*|*rebuttal*) printf 'reviews\n' ;;
    *.csv|*.tsv|*.json|*.yml|*.yaml) printf 'data\n' ;;
    *.md|*.txt|*.org|*.rst) printf 'notes\n' ;;
    *.pdf) printf 'references\n' ;;
    *) printf 'notes\n' ;;
  esac
}

extract_text() {
  local file="$1"
  local ext="$2"
  local base="$3"
  local text
  if is_supported_text_ext "$ext"; then
    text=$(head -c "$TEXT_MAX_BYTES" "$file" 2>/dev/null || true)
  else
    text=""
  fi
  printf '%s %s\n' "$base" "$text" | tr '[:upper:]' '[:lower:]' | tr -cs '[:alnum:]' ' '
}

project_dirs() {
  find "$WORKSPACE_ROOT/projects" -mindepth 1 -maxdepth 1 -type d -print 2>/dev/null | sort
}

score_project() {
  local project="$1"
  local rel="$2"
  local text="$3"
  local score=0
  local reasons=()
  local pname tokens token focus_n
  pname="$(basename "$project")"
  tokens=$(normalize_words "$pname")
  for token in $tokens; do
    [ ${#token} -lt 3 ] && continue
    if printf '%s %s\n' "$rel" "$text" | grep -Fqi "$token"; then
      score=$((score + 12))
      reasons+=("token:$token")
    fi
  done
  focus_n="$(normalize_words "$ACTIVE_FOCUS")"
  if [ -n "$focus_n" ]; then
    if [ "$focus_n" = "$(normalize_words "$pname")" ]; then
      score=$((score + 28))
      reasons+=("focus:$pname")
    else
      for token in $focus_n; do
        [ ${#token} -lt 3 ] && continue
        if printf '%s\n' "$pname" | grep -Fqi "$token"; then
          score=$((score + 8))
          reasons+=("focus-token:$token")
        fi
      done
    fi
  fi
  printf '%s|%s\n' "$score" "$(IFS=,; echo "${reasons[*]:-}")"
}

current_project_from_rel() {
  local rel="$1"
  if [[ "$rel" =~ ^projects/([^/]+)/ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
  fi
}

unique_path() {
  local dest="$1"
  if [ ! -e "$dest" ]; then
    printf '%s\n' "$dest"
    return 0
  fi
  local dir base stem ext n candidate
  dir="$(dirname "$dest")"
  base="$(basename "$dest")"
  stem="$base"
  ext=""
  if [[ "$base" == *.* ]]; then
    stem="${base%.*}"
    ext=".${base##*.}"
  fi
  n=1
  while :; do
    candidate="$dir/${stem}__triage_${n}${ext}"
    if [ ! -e "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
    n=$((n + 1))
  done
}

append_manifest() {
  printf '%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$5" >> "$MANIFEST_FILE"
}

safe_dest_path() {
  local rel_dest="$1"
  case "$rel_dest" in
    /*|../*|*/../*|*'/..'|*'/../'*) return 1 ;;
  esac
  printf '%s/%s\n' "$WORKSPACE_ROOT" "$rel_dest"
}

write_report() {
  {
    echo "run_id=$RUN_ID"
    echo "mode=$MODE"
    echo "scope=$SCOPE"
    echo "move_policy=$MOVE_POLICY"
    echo "focus=${ACTIVE_FOCUS:-}"
    echo "scanned=$SCANNED"
    echo "would_move=$WOULD_MOVE"
    echo "moved=$MOVED"
    echo "would_triage=$WOULD_TRIAGE"
    echo "triaged=$TRIAGED"
    echo "skipped=$SKIPPED"
    echo "blocked=$BLOCKED"
    echo "collisions=$COLLISIONS"
    echo "manifest=$MANIFEST_FILE"
  } | tee "$RUN_FILE" > "$LATEST_REPORT"
}

status_mode() {
  echo "workspace_root=$WORKSPACE_ROOT"
  echo "skill_base=$SKILL_BASE"
  echo "config_file=$CONFIG_FILE"
  echo "active_focus=${ACTIVE_FOCUS:-}"
  echo "latest_report=$LATEST_REPORT"
  if [ -f "$LATEST_REPORT" ]; then
    echo "--- latest report ---"
    cat "$LATEST_REPORT"
  fi
  local pending=0 root abs
  for root in "${STAGING_ROOTS[@]}"; do
    abs="$WORKSPACE_ROOT/$root"
    [ -d "$abs" ] || continue
    while IFS= read -r -d '' _f; do pending=$((pending + 1)); done < <(find "$abs" -type f -print0 2>/dev/null)
  done
  echo "pending_staging_files=$pending"
}

undo_last() {
  acquire_lock
  local latest
  latest=$(ls -1t "$LOG_DIR/manifests"/*.tsv 2>/dev/null | head -n1 || true)
  if [ -z "$latest" ]; then
    echo "No manifest found to undo." >&2
    exit 4
  fi
  local undone=0 status src dest score reason
  while IFS=$'\t' read -r status src dest score reason; do
    case "$status" in
      completed-move|completed-triage)
        if { [ -e "$dest" ] || [ -L "$dest" ]; } && [ ! -e "$src" ] && [ ! -L "$src" ]; then
          mkdir -p "$(dirname "$src")"
          mv "$dest" "$src"
          undone=$((undone + 1))
        fi
        ;;
    esac
  done < <(grep -E '^(completed-move|completed-triage)\t' "$latest" | tac)
  echo "undone=$undone"
  echo "source_manifest=$latest"
}

acquire_lock_if_needed() {
  case "$MODE" in
    dry-run|apply) acquire_lock ;;
  esac
}

SCANNED=0
WOULD_MOVE=0
MOVED=0
WOULD_TRIAGE=0
TRIAGED=0
SKIPPED=0
BLOCKED=0
COLLISIONS=0

if [ "$MODE" = "status" ]; then
  status_mode
  exit 0
fi

if [ "$MODE" = "undo-last" ]; then
  undo_last
  exit 0
fi

acquire_lock_if_needed

{
  echo "# run_id=$RUN_ID"
  echo "# mode=$MODE"
  echo "# scope=$SCOPE"
  echo "# move_policy=$MOVE_POLICY"
  echo "# focus=${ACTIVE_FOCUS:-}"
  echo -e "status\tsource\tdestination\tscore\treason"
} > "$MANIFEST_FILE"

build_roots() {
  local roots=()
  case "$SCOPE" in
    staging)
      roots=("${STAGING_ROOTS[@]}") ;;
    workspace)
      roots=(".") ;;
    custom)
      IFS=',' read -r -a roots <<< "$ROOTS_CSV" ;;
  esac
  printf '%s\n' "${roots[@]}"
}

mapfile -t ROOTS < <(build_roots)

ALL_PROJECTS=()
while IFS= read -r proj; do
  [ -n "$proj" ] && ALL_PROJECTS+=("$proj")
done < <(project_dirs)

process_file() {
  local file="$1"
  local abs rel base base_lc ext current_project text best_proj="" best_score=-1 best_reason="" second_score=-1 current_score=0
  abs="$(abs_real "$file")"
  rel="$(rel_from_workspace "$abs")"

  case "$rel" in
    .|"") SKIPPED=$((SKIPPED + 1)); return ;;
  esac
  if is_protected_rel "$rel"; then
    SKIPPED=$((SKIPPED + 1))
    append_manifest "skipped-protected" "$abs" "" "0" "$rel"
    return
  fi
  if [ -L "$abs" ]; then
    SKIPPED=$((SKIPPED + 1))
    append_manifest "skipped-symlink" "$abs" "" "0" "$rel"
    return
  fi
  base="$(basename "$abs")"
  base_lc="$(lower "$base")"
  ext="${base_lc##*.}"
  if [ "$ext" = "$base_lc" ]; then ext=""; fi
  if is_skipped_ext "$ext"; then
    SKIPPED=$((SKIPPED + 1))
    append_manifest "skipped-extension" "$abs" "" "0" "$ext"
    return
  fi

  text="$(extract_text "$abs" "$ext" "$base")"
  current_project="$(current_project_from_rel "$rel" || true)"

  local proj raw score reason
  for proj in "${ALL_PROJECTS[@]}"; do
    raw="$(score_project "$proj" "$rel" "$text")"
    score="${raw%%|*}"
    reason="${raw#*|}"
    if [ -n "$current_project" ] && [ "$(basename "$proj")" = "$current_project" ]; then
      current_score="$score"
    fi
    if [ "$score" -gt "$best_score" ]; then
      second_score="$best_score"
      best_score="$score"
      best_proj="$proj"
      best_reason="$reason"
    elif [ "$score" -gt "$second_score" ]; then
      second_score="$score"
    fi
  done

  local route target_rel target_abs final_score decision_reason
  route="$(route_subdir "$base_lc")"
  target_rel=""
  decision_reason="$best_reason"
  final_score="$best_score"

  if [ -n "$best_proj" ] && [ "$best_score" -ge "$TRIAGE_SCORE" ]; then
    if [ -n "$current_project" ] && [ "$(basename "$best_proj")" != "$current_project" ]; then
      if [ $((best_score - current_score)) -lt "$RELOCATION_MARGIN" ]; then
        best_proj="$WORKSPACE_ROOT/projects/$current_project"
        final_score="$current_score"
        decision_reason="stay-put-margin"
      fi
    fi
    if [ "$route" = "references" ]; then
      target_rel="shared/references/$base"
    else
      target_rel="projects/$(basename "$best_proj")/$route/$base"
    fi
  else
    case "$base_lc" in
      *.pdf|*.md|*.txt)
        target_rel="shared/references/$base"
        final_score=50
        decision_reason="fallback-shared"
        ;;
    esac
  fi

  if [ -n "$target_rel" ] && [ "$rel" = "$target_rel" ]; then
    SKIPPED=$((SKIPPED + 1))
    append_manifest "already-placed" "$abs" "$WORKSPACE_ROOT/$target_rel" "$final_score" "$decision_reason"
    return
  fi

  local may_move=0
  if [ "$MODE" = "apply" ] && [ "$MOVE_POLICY" != "report-only" ]; then
    case "$MOVE_POLICY" in
      safe)
        if is_staging_rel "$rel"; then may_move=1; fi ;;
      expanded)
        may_move=1 ;;
    esac
  fi

  if [ -n "$target_rel" ] && [ "$final_score" -ge "$AUTO_SCORE" ]; then
    target_abs="$(safe_dest_path "$target_rel")"
    if [ "$MODE" = "dry-run" ] || [ "$MOVE_POLICY" = "report-only" ]; then
      WOULD_MOVE=$((WOULD_MOVE + 1))
      append_manifest "would-move" "$abs" "$target_abs" "$final_score" "$decision_reason"
      return
    fi
    if [ "$may_move" -ne 1 ]; then
      BLOCKED=$((BLOCKED + 1))
      append_manifest "blocked-policy" "$abs" "$target_abs" "$final_score" "$decision_reason"
      return
    fi
    if [ -e "$target_abs" ]; then
      COLLISIONS=$((COLLISIONS + 1))
      append_manifest "collision" "$abs" "$target_abs" "$final_score" "$decision_reason"
      return
    fi
    mkdir -p "$(dirname "$target_abs")"
    mv "$abs" "$target_abs"
    MOVED=$((MOVED + 1))
    append_manifest "completed-move" "$abs" "$target_abs" "$final_score" "$decision_reason"
    return
  fi

  if [ "$final_score" -ge "$TRIAGE_SCORE" ] || { [ -z "$target_rel" ] && is_staging_rel "$rel"; }; then
    if [ "$MODE" = "dry-run" ] || [ "$MOVE_POLICY" = "report-only" ]; then
      WOULD_TRIAGE=$((WOULD_TRIAGE + 1))
      append_manifest "would-triage" "$abs" "$TRIAGE_DIR/$base" "$final_score" "$decision_reason"
      return
    fi
    if [ "$may_move" -ne 1 ]; then
      BLOCKED=$((BLOCKED + 1))
      append_manifest "blocked-triage-policy" "$abs" "$TRIAGE_DIR/$base" "$final_score" "$decision_reason"
      return
    fi
    local triage_dest
    triage_dest="$(unique_path "$TRIAGE_DIR/$base")"
    mv "$abs" "$triage_dest"
    TRIAGED=$((TRIAGED + 1))
    append_manifest "completed-triage" "$abs" "$triage_dest" "$final_score" "$decision_reason"
    return
  fi

  SKIPPED=$((SKIPPED + 1))
  append_manifest "skipped-low-confidence" "$abs" "" "$final_score" "$decision_reason"
}

for root_rel in "${ROOTS[@]}"; do
  [ -z "$root_rel" ] && continue
  local_root="$WORKSPACE_ROOT/$root_rel"
  if [ "$root_rel" = "." ]; then
    local_root="$WORKSPACE_ROOT"
  fi
  [ -d "$local_root" ] || continue
  while IFS= read -r -d '' file; do
    SCANNED=$((SCANNED + 1))
    process_file "$file"
  done < <(find "$local_root" \( -type f -o -type l \) -print0 2>/dev/null)
done

write_report
cat "$LATEST_REPORT"
