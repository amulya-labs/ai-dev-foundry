#!/bin/bash
set -e

# AI Dev Foundry Config Manager
# Copy this script to your project and use it to install/update AI agent configs
#
# Source: https://github.com/amulya-labs/ai-dev-foundry
# License: MIT (https://opensource.org/licenses/MIT)
#
# Usage:
#   ./scripts/manage-ai-configs.sh claude install                        # First-time setup (includes Claude workflows)
#   ./scripts/manage-ai-configs.sh claude install --with-gha-workflows   # Also install extra workflow templates
#   ./scripts/manage-ai-configs.sh claude update                         # Pull latest config (includes Claude workflows)
#   ./scripts/manage-ai-configs.sh claude update --with-gha-workflows    # Update including extra workflow templates
#
# Multi-provider usage (comma-separated or individual flags):
#   ./scripts/manage-ai-configs.sh claude install --gemini               # Claude + Gemini workflows
#   ./scripts/manage-ai-configs.sh claude install --ai claude,gemini     # Same as above
#   ./scripts/manage-ai-configs.sh claude update --ai gemini             # Update Gemini workflow only (no Claude .claude/ dir)

REPO="amulya-labs/ai-dev-foundry"
BRANCH="main"
CLAUDE_DIR=".claude"
API_BASE="https://api.github.com/repos/$REPO/contents"
RAW_BASE="https://raw.githubusercontent.com/$REPO/$BRANCH"
WITH_GHA_WORKFLOWS=false

# Provider registry — to add a new provider, add 3 lines here only
# (Variables are referenced via indirect expansion in download_provider_workflows(); SC2034 is suppressed per-variable)
# shellcheck disable=SC2034
PROVIDER_CLAUDE_WORKFLOWS="claude.yml claude-code-review.yml"
# shellcheck disable=SC2034
PROVIDER_CLAUDE_SECRET="CLAUDE_CODE_OAUTH_TOKEN"
# shellcheck disable=SC2034
PROVIDER_CLAUDE_LABEL="Claude"

# shellcheck disable=SC2034
PROVIDER_GEMINI_WORKFLOWS="gemini-code-review.yml"
# shellcheck disable=SC2034
PROVIDER_GEMINI_SECRET="GEMINI_API_KEY"
# shellcheck disable=SC2034
PROVIDER_GEMINI_LABEL="Gemini"

# Populated by flag parsing; positional 'claude' arg also adds "claude" here
PROVIDERS_ENABLED=()

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}==>${NC} $1"; }
warn() { echo -e "${YELLOW}==>${NC} $1"; }
error() { echo -e "${RED}==>${NC} $1"; exit 1; }

# Check if we're in a git repo
check_git() {
    if ! git rev-parse --is-inside-work-tree > /dev/null 2>&1; then
        error "Not inside a git repository"
    fi

    # Make sure we're at repo root
    cd "$(git rev-parse --show-toplevel)"
}

contains() {
    local needle="$1"; shift
    local item
    for item in "$@"; do [[ "$item" == "$needle" ]] && return 0; done
    return 1
}

# Fetch list of files from a GitHub directory
get_files_in_dir() {
    local dir="$1"
    local response
    response=$(curl -fsSL "$API_BASE/$dir?ref=$BRANCH" 2>/dev/null) || {
        warn "Failed to fetch file list from $dir"
        return 1
    }

    # Extract filenames from JSON response (works without jq)
    echo "$response" | grep -o '"name": *"[^"]*"' | sed 's/"name": *"\([^"]*\)"/\1/'
}

# Download files from a directory
download_dir() {
    local remote_dir="$1"
    local local_dir="$2"

    mkdir -p "$local_dir"

    info "Fetching $remote_dir..."
    local files
    files=$(get_files_in_dir "$remote_dir") || return 1

    if [ -z "$files" ]; then
        warn "No files found in $remote_dir"
        return 0
    fi

    local success=0
    local failed=0

    while IFS= read -r file; do
        local url="$RAW_BASE/$remote_dir/$file"
        local dest="$local_dir/$file"

        if curl -fsSL "$url" -o "$dest" 2>/dev/null; then
            info "  Downloaded $file"
            ((++success))
        else
            warn "  Failed to download $file"
            ((++failed))
        fi
    done <<< "$files"

    echo "  $success files downloaded"
    if [ $failed -gt 0 ]; then
        warn "  $failed files failed"
    fi
}

download_provider_workflows() {
    local provider="$1"
    local upper
    upper=$(printf '%s' "$provider" | tr '[:lower:]' '[:upper:]')
    local workflows_var="PROVIDER_${upper}_WORKFLOWS"
    local secret_var="PROVIDER_${upper}_SECRET"
    local label_var="PROVIDER_${upper}_LABEL"

    if [[ -z "${!workflows_var+x}" ]]; then
        warn "Unknown provider '$provider' — no registry entry; skipping"
        return 0
    fi

    local label="${!label_var}"
    local secret="${!secret_var}"
    local workflows="${!workflows_var}"
    local -a wf_list
    read -ra wf_list <<< "$workflows"

    info "Fetching ${label} GitHub Actions workflows..."
    local workflow_dir=".github/workflows"
    mkdir -p "$workflow_dir"
    for wf in "${wf_list[@]}"; do
        local url="$RAW_BASE/.github/workflows/$wf"
        if curl -fsSL "$url" -o "$workflow_dir/$wf" 2>/dev/null; then
            info "  Downloaded $wf"
        else
            warn "  Failed to download $wf"
        fi
    done
    warn "Requires ${secret} secret in your repo settings"
}

download_all() {
    # .claude/ dir is Claude-specific — only download when claude is enabled
    if contains "claude" "${PROVIDERS_ENABLED[@]}"; then
        mkdir -p "$CLAUDE_DIR"
        download_dir ".claude/agents" "$CLAUDE_DIR/agents"
        download_dir ".claude/hooks" "$CLAUDE_DIR/hooks"
        if [ -d "$CLAUDE_DIR/hooks" ]; then
            chmod +x "$CLAUDE_DIR/hooks/"*.sh 2>/dev/null || true
            chmod +x "$CLAUDE_DIR/hooks/"*.py 2>/dev/null || true
        fi
        info "Fetching settings.json..."
        if curl -fsSL "$RAW_BASE/.claude/settings.json" -o "$CLAUDE_DIR/settings.json" 2>/dev/null; then
            info "  Downloaded settings.json"
        else
            warn "  settings.json not found (optional)"
        fi
    fi

    for provider in "${PROVIDERS_ENABLED[@]}"; do
        download_provider_workflows "$provider"
    done

    if $WITH_GHA_WORKFLOWS; then
        download_gha_workflow_templates
    fi
}

download_gha_workflow_templates() {
    info "Fetching additional workflow templates..."
    # TODO: remove this guard when github-workflow-templates/ is populated in the source repo
    warn "No extra workflow templates available yet — coming in a future release"
    # download_dir "github-workflow-templates" ".github/workflows"
}

install_config() {
    check_git

    if [ -d "$CLAUDE_DIR" ] && [ "$(ls -A "$CLAUDE_DIR" 2>/dev/null)" ]; then
        error "Directory $CLAUDE_DIR already exists and is not empty. Use 'update' instead."
    fi

    info "Installing claude-code config to $CLAUDE_DIR..."
    echo ""
    download_all

    if contains "claude" "${PROVIDERS_ENABLED[@]}"; then
        git add "$CLAUDE_DIR"
    fi
    git add .github/workflows/ 2>/dev/null || true

    echo ""
    info "Done! Config installed."
    for provider in "${PROVIDERS_ENABLED[@]}"; do
        local upper
        upper=$(printf '%s' "$provider" | tr '[:lower:]' '[:upper:]')
        local label_var="PROVIDER_${upper}_LABEL"
        local secret_var="PROVIDER_${upper}_SECRET"
        info "${!label_var} workflows installed in .github/workflows/"
        warn "  → Requires ${!secret_var} secret in your repo settings"
    done
    if $WITH_GHA_WORKFLOWS; then
        info "Extra workflow templates installed to .github/workflows/"
    fi
    echo ""
    echo "Next steps:"
    echo "  git commit -m 'Add claude-code config'"
    echo "  git push"
}

update_config() {
    check_git

    if contains "claude" "${PROVIDERS_ENABLED[@]}" && [ ! -d "$CLAUDE_DIR" ]; then
        error "Directory $CLAUDE_DIR not found. Use 'install' first."
    fi

    info "Updating claude-code config..."
    echo ""
    download_all

    if contains "claude" "${PROVIDERS_ENABLED[@]}"; then
        git add "$CLAUDE_DIR"
    fi
    git add .github/workflows/ 2>/dev/null || true

    echo ""
    info "Done! Config updated."
    for provider in "${PROVIDERS_ENABLED[@]}"; do
        local upper
        upper=$(printf '%s' "$provider" | tr '[:lower:]' '[:upper:]')
        local label_var="PROVIDER_${upper}_LABEL"
        local secret_var="PROVIDER_${upper}_SECRET"
        info "${!label_var} workflows updated in .github/workflows/"
        warn "  → Requires ${!secret_var} secret in your repo settings"
    done
    if $WITH_GHA_WORKFLOWS; then
        info "Extra workflow templates updated in .github/workflows/"
    fi
    echo ""
    echo "Next steps:"
    echo "  git diff --cached  # review changes"
    echo "  git commit -m 'Update claude-code config'"
    echo "  git push"
}


usage_claude() {
    echo "Usage: $0 claude <command> [options]"
    echo ""
    echo "Commands:"
    echo "  install   Add .claude config to your project (first-time setup)"
    echo "  update    Pull the latest config (agents, hooks, settings)"
    echo ""
    echo "Options:"
    echo "  --gemini               Also install Gemini PR review workflow"
    echo "  --ai <providers>       Comma-separated provider list (e.g. claude,gemini)"
    echo "  --with-gha-workflows   Also install extra workflow templates from"
    echo "                         github-workflow-templates/ in the source repo"
    echo ""
    echo "This downloads:"
    echo "  .claude/agents/   - Reusable Claude Code agents"
    echo "  .claude/hooks/    - PreToolUse hooks (e.g., bash validation)"
    echo "  .claude/settings.json - Hook configuration"
    echo "  .github/workflows/claude.yml             - @claude mention handler"
    echo "  .github/workflows/claude-code-review.yml - Auto PR review"
    echo "  (requires CLAUDE_CODE_OAUTH_TOKEN secret in repo)"
    echo ""
    echo "With --gemini or --ai claude,gemini, also downloads:"
    echo "  .github/workflows/gemini-code-review.yml - Gemini PR review (Flash + Pro)"
    echo "  (requires GEMINI_API_KEY secret in repo)"
    echo ""
    echo "With --with-gha-workflows, also downloads:"
    echo "  Extra workflow templates from github-workflow-templates/"
}

usage_main() {
    echo "AI Dev Foundry Config Manager"
    echo ""
    echo "Usage: $0 <agent> <command> [options]"
    echo ""
    echo "Agents:"
    echo "  claude    Manage Claude Code agents, hooks, and GitHub Actions workflows"
    echo ""
    echo "Run '$0 <agent>' for agent-specific usage."
}

# --- Main ---

AGENT="${1:-}"
shift || true

# Parse flags from remaining args
_shifted=()
for arg in "$@"; do
    case "$arg" in
        --with-gha-workflows) WITH_GHA_WORKFLOWS=true ;;
        --gemini)             PROVIDERS_ENABLED+=("gemini") ;;
        --ai)
            # --ai requires the next argument; handle below via index tracking
            # We use a sentinel so the next iteration picks up the value
            _shifted+=("$arg")
            ;;
        --ai=*)
            # --ai=claude,gemini style
            _ai_val="${arg#--ai=}"
            IFS=',' read -ra _providers <<< "$_ai_val"
            for _p in "${_providers[@]}"; do
                _upper_p=$(printf '%s' "$_p" | tr '[:lower:]' '[:upper:]')
                _wf_var="PROVIDER_${_upper_p}_WORKFLOWS"
                if [[ -n "${!_wf_var+x}" ]]; then
                    PROVIDERS_ENABLED+=("$_p")
                else
                    warn "Unknown provider '$_p' in --ai; ignoring"
                fi
            done
            ;;
        *) _shifted+=("$arg") ;;
    esac
done
set -- "${_shifted[@]+"${_shifted[@]}"}"

# Second pass: handle --ai <value> (space-separated form)
_final=()
_i=0
_args=("$@")
while [ $_i -lt ${#_args[@]} ]; do
    arg="${_args[$_i]}"
    if [ "$arg" = "--ai" ]; then
        _i=$((_i + 1))
        if [ $_i -lt ${#_args[@]} ]; then
            _ai_val="${_args[$_i]}"
            IFS=',' read -ra _providers <<< "$_ai_val"
            for _p in "${_providers[@]}"; do
                _upper_p=$(printf '%s' "$_p" | tr '[:lower:]' '[:upper:]')
                _wf_var="PROVIDER_${_upper_p}_WORKFLOWS"
                if [[ -n "${!_wf_var+x}" ]]; then
                    PROVIDERS_ENABLED+=("$_p")
                else
                    warn "Unknown provider '$_p' in --ai; ignoring"
                fi
            done
        else
            error "--ai requires a value (e.g. --ai claude,gemini)"
        fi
    else
        _final+=("$arg")
    fi
    _i=$((_i + 1))
done
set -- "${_final[@]+"${_final[@]}"}"

case "$AGENT" in
    claude)
        # If --ai was not given, default to claude-only.
        # If --ai was given, treat it as authoritative (supports "claude update --ai gemini" = Gemini-only).
        if [ ${#PROVIDERS_ENABLED[@]} -eq 0 ]; then
            PROVIDERS_ENABLED=("claude")
        fi
        # Deduplicate (preserve order, first occurrence wins)
        _deduped=()
        for _p in "${PROVIDERS_ENABLED[@]}"; do
            if ! contains "$_p" "${_deduped[@]}"; then _deduped+=("$_p"); fi
        done
        PROVIDERS_ENABLED=("${_deduped[@]}")
        case "${1:-}" in
            install) install_config ;;
            update)  update_config ;;
            *)       usage_claude; exit 1 ;;
        esac
        ;;
    *)
        usage_main
        exit 1
        ;;
esac
