#!/usr/bin/env bash
# Claude Code status line for the devcontainer.
#
# Renders: current path, git branch, model name, and context-window usage as
# tokens-used / window-size (percentage), e.g.
# "~/myproject  ⎇ main  Opus 4.8  730.0k/1.0M (73%)". The usage figures turn
# yellow at >=70% and red at >=90%, with a warning marker once usage crosses
# 200k tokens.
#
# Claude Code feeds a JSON blob on stdin describing the current session. The
# fields used here (context_window.*, exceeds_200k_tokens, model.display_name,
# workspace.current_dir/cwd) are documented at
# https://code.claude.com/docs/en/statusline. The script re-runs on every
# render, so it stays fast and dependency-light: jq plus a cheap git call. A
# missing context_window (older Claude versions) degrades to the leading
# segments rather than erroring.

set -uo pipefail

input="$(cat)"

model="$(jq -r '.model.display_name // "claude"' <<<"$input" 2>/dev/null)"
cwd="$(jq -r '.workspace.current_dir // .cwd // empty' <<<"$input" 2>/dev/null)"
project_dir="$(jq -r '.workspace.project_dir // empty' <<<"$input" 2>/dev/null)"
pct="$(jq -r '.context_window.used_percentage // 0' <<<"$input" 2>/dev/null)"
size="$(jq -r '.context_window.context_window_size // 0' <<<"$input" 2>/dev/null)"
exceeds="$(jq -r '.exceeds_200k_tokens // false' <<<"$input" 2>/dev/null)"

# Tokens currently occupying the context window. Prefer the dedicated field;
# fall back to summing current_usage (input + cache) when present; otherwise
# leave empty so it can be derived from the percentage below.
used="$(jq -r '
  .context_window as $c
  | ($c.total_input_tokens
     // (if $c.current_usage then
           ($c.current_usage.input_tokens // 0)
           + ($c.current_usage.cache_creation_input_tokens // 0)
           + ($c.current_usage.cache_read_input_tokens // 0)
         else null end))
  // empty
' <<<"$input" 2>/dev/null)"

# Current git branch (or short SHA when detached) from the session directory.
# symbolic-ref/rev-parse are cheap, so no caching is needed. GIT_OPTIONAL_LOCKS=0
# keeps this read-only so it never contends for the index lock.
branch=""
if [ -n "$cwd" ]; then
  branch="$(GIT_OPTIONAL_LOCKS=0 git -C "$cwd" symbolic-ref --quiet --short HEAD 2>/dev/null \
            || GIT_OPTIONAL_LOCKS=0 git -C "$cwd" rev-parse --short HEAD 2>/dev/null || true)"
fi

# Compose the path segment. When cwd has drifted inside project_dir (e.g.
# the agent ran `cd subdir`), show "<project-basename> › <relative>" so the
# session root stays anchored and the drift is visible at a glance. Once the
# composed segment exceeds MAX_PATH_LEN characters, collapse the relative
# half to "…/<leaf>" so the rest of the bar stays readable. When the cwd
# has not drifted or has left the project tree, show the abbreviated cwd.
# NB: escape the ~ replacement, else bash tilde-expands it.
MAX_PATH_LEN=60
home="${HOME:-}"
abbrev() { local p="$1"; [ -n "$home" ] && p="${p/#$home/\~}"; printf '%s' "$p"; }
path_display=""
if [ -n "$cwd" ]; then
  if [ -n "$project_dir" ] && [ "$cwd" != "$project_dir" ] && [ "${cwd#$project_dir/}" != "$cwd" ]; then
    relative="${cwd#$project_dir/}"
    root_base="$(basename "$project_dir")"
    path_display="${root_base} › ${relative}"
    if [ "${#path_display}" -gt "$MAX_PATH_LEN" ] && [[ "$relative" == */* ]]; then
      path_display="${root_base} › …/$(basename "$relative")"
    fi
  else
    path_display="$(abbrev "$cwd")"
  fi
fi

# Normalize numeric inputs.
pct="${pct%.*}"; [[ "$pct" =~ ^[0-9]+$ ]] || pct=0; [ "$pct" -gt 100 ] && pct=100
[[ "$size" =~ ^[0-9]+$ ]] || size=0
# If no token count was reported, approximate it from the percentage.
[[ "$used" =~ ^[0-9]+$ ]] || used=$(( size * pct / 100 ))

# Format a token count to one decimal: 1500000 -> 1.5M, 15500 -> 15.5k, 512 -> 512.
fmt() {
  local n=$1
  if   [ "$n" -ge 1000000 ]; then awk -v n="$n" 'BEGIN{printf "%.1fM", n/1000000}'
  elif [ "$n" -ge 1000 ];    then awk -v n="$n" 'BEGIN{printf "%.1fk", n/1000}'
  else echo "$n"
  fi
}

reset=$'\033[0m'
dim=$'\033[2m'
cyan=$'\033[36m'
magenta=$'\033[35m'

line=""
[ -n "$path_display" ] && line+="${cyan}${path_display}${reset}  "
[ -n "$branch" ] && line+="${magenta}⎇ ${branch}${reset}  "
line+="${dim}${model}${reset}"

if [ "$size" -gt 0 ]; then
  # Color the usage figures: green <70, yellow 70-89, red >=90.
  if   [ "$pct" -ge 90 ]; then color=$'\033[31m'   # red
  elif [ "$pct" -ge 70 ]; then color=$'\033[33m'   # yellow
  else                          color=$'\033[32m'   # green
  fi

  warn=""
  [ "$exceeds" = "true" ] && warn=" ⚠"

  line+="  ${color}$(fmt "$used")/$(fmt "$size") (${pct}%)${reset}${warn}"
fi

printf '%s\n' "$line"
