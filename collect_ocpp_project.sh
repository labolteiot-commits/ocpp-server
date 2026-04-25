#!/bin/bash
# ============================================================
#  collect_ocpp_project.sh
#  Collecte tous les fichiers de code du projet OCPP Server
#  et les compile dans un seul fichier .txt pour partage.
#
#  Usage :
#    chmod +x collect_ocpp_project.sh
#    ./collect_ocpp_project.sh [CHEMIN_DU_PROJET]
#
#  Si aucun chemin n'est fourni, le répertoire courant est utilisé.
# ============================================================

set -euo pipefail

# ── Configuration ────────────────────────────────────────────
PROJECT_DIR="${1:-$(pwd)}"
OUTPUT_FILE="$HOME/ocpp_project_snapshot_$(date +%Y%m%d_%H%M%S).txt"

# Extensions à inclure (pas .txt pour éviter d'inclure les anciens snapshots)
INCLUDE_EXTENSIONS=("py" "js" "ts" "html" "css" "json" "yaml" "yml" "toml" "ini" "cfg" "sh" "md" "sql")

# Dossiers / fichiers à exclure
EXCLUDE_DIRS=(".git" "__pycache__" ".venv" "venv" "env" "node_modules" ".mypy_cache" ".pytest_cache" "dist" "build" ".eggs" "*.egg-info")

# Fichiers spécifiques à exclure (ex: secrets, anciens snapshots)
EXCLUDE_FILES=(".env" "*.log" "*.pyc" "*.pyo" "*.pyd" "*.db" "*.sqlite" "*.sqlite3" "ocpp_project_snapshot_*.txt" "*_snapshot_*.txt")

# ── Couleurs terminal ────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'

# ── Fonctions ────────────────────────────────────────────────
log()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()     { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()   { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()  { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# Construire les arguments find pour l'exclusion des dossiers
build_exclude_args() {
    local args=()
    for dir in "${EXCLUDE_DIRS[@]}"; do
        args+=(-path "*/${dir}" -prune -o)
    done
    echo "${args[@]}"
}

# Vérifier si un fichier doit être exclu
is_excluded_file() {
    local filename
    filename=$(basename "$1")
    for pattern in "${EXCLUDE_FILES[@]}"; do
        # shellcheck disable=SC2254
        case "$filename" in
            $pattern) return 0 ;;
        esac
    done
    return 1
}

# ── Vérifications préliminaires ──────────────────────────────
if [ ! -d "$PROJECT_DIR" ]; then
    error "Répertoire introuvable : $PROJECT_DIR"
    exit 1
fi

PROJECT_DIR=$(realpath "$PROJECT_DIR")
log "Répertoire du projet : $PROJECT_DIR"
log "Fichier de sortie    : $OUTPUT_FILE"
echo

# ── En-tête du fichier de sortie ────────────────────────────
{
    echo "================================================================"
    echo "  OCPP PROJECT SNAPSHOT"
    echo "  Généré le : $(date '+%Y-%m-%d %H:%M:%S')"
    echo "  Hôte      : $(hostname)"
    echo "  Projet    : $PROJECT_DIR"
    echo "  Git branch: $(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'N/A')"
    echo "  Git commit: $(git -C "$PROJECT_DIR" rev-parse --short HEAD 2>/dev/null || echo 'N/A')"
    echo "================================================================"
    echo
} > "$OUTPUT_FILE"

# ── Arborescence du projet ───────────────────────────────────
{
    echo "================================================================"
    echo "  ARBORESCENCE DU PROJET"
    echo "================================================================"
    echo
    if command -v tree &>/dev/null; then
        tree "$PROJECT_DIR" \
            --noreport \
            -I "$(IFS='|'; echo "${EXCLUDE_DIRS[*]}")" \
            2>/dev/null || find "$PROJECT_DIR" -type f | sort
    else
        find "$PROJECT_DIR" -type f | \
            grep -vE "(/__pycache__|/\.git/|/venv/|/\.venv/|/node_modules/)" | \
            sort | sed "s|$PROJECT_DIR/||"
    fi
    echo
} >> "$OUTPUT_FILE"

# ── Collecte des fichiers ────────────────────────────────────
FILE_COUNT=0
SKIPPED_COUNT=0

# Construire l'expression find
FIND_CMD=(find "$PROJECT_DIR" -type f)

# Ajouter les exclusions de dossiers
for dir in "${EXCLUDE_DIRS[@]}"; do
    FIND_CMD+=(-not -path "*/${dir}/*")
    FIND_CMD+=(-not -path "*/${dir}")
done

# Exécuter find et filtrer par extension
log "Scan des fichiers..."
echo

while IFS= read -r filepath; do
    filename=$(basename "$filepath")
    ext="${filename##*.}"
    rel_path="${filepath#$PROJECT_DIR/}"

    # Vérifier extension
    ext_ok=false
    for valid_ext in "${INCLUDE_EXTENSIONS[@]}"; do
        if [ "$ext" = "$valid_ext" ]; then
            ext_ok=true
            break
        fi
    done

    if ! $ext_ok; then
        continue
    fi

    # Vérifier fichiers exclus
    if is_excluded_file "$filepath"; then
        warn "Exclu (secret/log) : $rel_path"
        ((SKIPPED_COUNT++)) || true
        continue
    fi

    # Vérifier que le fichier est lisible et non vide
    if [ ! -r "$filepath" ]; then
        warn "Non lisible : $rel_path"
        continue
    fi

    # Taille du fichier
    FILE_SIZE=$(wc -c < "$filepath" 2>/dev/null || echo 0)
    if [ "$FILE_SIZE" -eq 0 ]; then
        warn "Fichier vide ignoré : $rel_path"
        continue
    fi

    # Écrire le fichier dans le snapshot
    {
        echo "================================================================"
        echo "  FICHIER : $rel_path"
        echo "  Taille  : ${FILE_SIZE} octets"
        echo "  Modifié : $(stat -c '%y' "$filepath" 2>/dev/null | cut -d. -f1 || stat -f '%Sm' "$filepath" 2>/dev/null)"
        echo "================================================================"
        echo
        cat "$filepath"
        echo
        echo
    } >> "$OUTPUT_FILE"

    ok "Ajouté : $rel_path (${FILE_SIZE} octets)"
    ((FILE_COUNT++)) || true

done < <("${FIND_CMD[@]}" | sort)

# ── Pied de page ─────────────────────────────────────────────
{
    echo "================================================================"
    echo "  FIN DU SNAPSHOT"
    echo "  Fichiers inclus : $FILE_COUNT"
    echo "  Fichiers exclus : $SKIPPED_COUNT"
    echo "  Taille totale   : $(wc -c < "$OUTPUT_FILE") octets"
    echo "================================================================"
} >> "$OUTPUT_FILE"

# ── Résumé ───────────────────────────────────────────────────
echo
echo -e "${GREEN}================================================${NC}"
echo -e "${GREEN}  Snapshot généré avec succès !${NC}"
echo -e "${GREEN}================================================${NC}"
echo -e "  Fichiers inclus : ${CYAN}$FILE_COUNT${NC}"
echo -e "  Fichiers exclus : ${YELLOW}$SKIPPED_COUNT${NC}"
echo -e "  Sortie          : ${CYAN}$OUTPUT_FILE${NC}"
echo -e "  Taille          : ${CYAN}$(du -h "$OUTPUT_FILE" | cut -f1)${NC}"
echo

ok "Snapshot disponible : $OUTPUT_FILE"
