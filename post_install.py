#!/usr/bin/env python3
"""Post-install configuration for Claude Code devcontainer.

Runs on container creation (via postCreateCommand) to set up:
- Claude Code marketplace plugins
- Onboarding bypass (when CLAUDE_CODE_OAUTH_TOKEN is set)
- Claude settings (bypassPermissions mode)
- Tmux configuration (200k history, mouse support)
- Directory ownership fixes for mounted volumes
- Per-project post-install hook (.devcontainer/post-install.local.{py,sh})

Plugin installation lives here (not in the Dockerfile) because ~/.claude/ is
mounted as a Docker named volume, which only copies image contents on *first
creation*. Subsequent Docker rebuilds silently keep the old volume data, so we
now install plugins after Docker image creation.
"""

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path


############################
# Marketplace plugin list  #
############################
# Edit this list to add/remove plugins.  Changes take effect on the next
# container rebuild (postCreateCommand re-runs) without needing to delete
# the ~/.claude Docker volume.
CLAUDE_PLUGINS = [
    "anthropics/skills",
    "trailofbits/skills",
    "trailofbits/skills-curated",
]


def install_claude_plugins():
    """Install Claude Code marketplace plugins into the live ~/.claude volume.

    Runs `claude plugin marketplace add <name>` for each plugin in
    CLAUDE_PLUGINS. This is idempotent; re-adding an already-installed
    plugin is a nop on Claude's side.

    This must happen at container creation time (not in the Dockerfile)
    because ~/.claude/ is a Docker named volume.  See module docstring
    for the full explanation.
    """
    for plugin in CLAUDE_PLUGINS:
        print(
            f"[post_install] Installing Claude plugin: {plugin}",
            file=sys.stderr,
        )
        try:
            result = subprocess.run(
                ["claude", "plugin", "marketplace", "add", plugin],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                print(
                    f"[post_install] Warning: failed to install plugin {plugin}: "
                    f"{result.stderr.strip()}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"[post_install] Installed plugin: {plugin}",
                    file=sys.stderr,
                )
        except subprocess.TimeoutExpired:
            print(
                f"[post_install] Warning: timed out installing plugin {plugin}",
                file=sys.stderr,
            )
        except (FileNotFoundError, OSError) as e:
            # claude binary not found — skip all remaining plugins
            print(
                f"[post_install] Warning: could not run claude ({e}) — "
                "plugin installation skipped",
                file=sys.stderr,
            )
            break


def setup_onboarding_bypass():
    """Bypass the interactive onboarding wizard when CLAUDE_CODE_OAUTH_TOKEN is set.

    Runs `claude -p` to seed ~/.claude.json with auth state. The subprocess
    writes the config file during startup before the API call completes, so
    a timeout is expected and acceptable. After the subprocess finishes (or
    times out), we check whether ~/.claude.json was populated and only then
    set hasCompletedOnboarding.

    Workaround for https://github.com/anthropics/claude-code/issues/8938.
    """
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if not token:
        print(
            "[post_install] No CLAUDE_CODE_OAUTH_TOKEN set, skipping onboarding bypass",
            file=sys.stderr,
        )
        return

    # When `CLAUDE_CONFIG_DIR` is set, as is done in `devcontainer.json`, `claude` unexpectedly 
    # looks for `.claude.json` in *that* folder, instead of in `~`, contradicting the documentation.
    #  See https://github.com/anthropics/claude-code/issues/3833#issuecomment-3694918874
    claude_json_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home()))
    claude_json = claude_json_dir / ".claude.json"

    print("[post_install] Running claude -p to populate auth state...", file=sys.stderr)
    try:
        result = subprocess.run(
            ["claude", "-p", "ok"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            print(
                f"[post_install] claude -p exited {result.returncode}: "
                f"{result.stderr.strip()}",
                file=sys.stderr,
            )
    except subprocess.TimeoutExpired:
        print(
            "[post_install] claude -p timed out (expected on cold start)",
            file=sys.stderr,
        )
    except (FileNotFoundError, OSError) as e:
        print(
            f"[post_install] Warning: could not run claude ({e}) — "
            "onboarding bypass skipped",
            file=sys.stderr,
        )
        return

    if not claude_json.exists():
        print(
            f"[post_install] Warning: {claude_json} not created by claude -p — "
            "onboarding bypass skipped",
            file=sys.stderr,
        )
        return

    config: dict = {}
    try:
        config = json.loads(claude_json.read_text())
    except json.JSONDecodeError as e:
        print(
            f"[post_install] Warning: {claude_json} has invalid JSON ({e}), "
            "starting fresh",
            file=sys.stderr,
        )

    config["hasCompletedOnboarding"] = True

    claude_json.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(
        f"[post_install] Onboarding bypass configured: {claude_json}", file=sys.stderr
    )


def setup_claude_settings():
    """Configure Claude Code with bypassPermissions enabled."""
    claude_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
    claude_dir.mkdir(parents=True, exist_ok=True)

    settings_file = claude_dir / "settings.json"

    # Load existing settings or start fresh
    settings = {}
    if settings_file.exists():
        with contextlib.suppress(json.JSONDecodeError):
            settings = json.loads(settings_file.read_text())

    # Set bypassPermissions mode
    if "permissions" not in settings:
        settings["permissions"] = {}
    settings["permissions"]["defaultMode"] = "bypassPermissions"

    # Point the status line at the script baked into the image. Use setdefault
    # so a user who configured their own statusLine (persisted on the ~/.claude
    # volume) keeps it; we only seed ours when none is set.
    settings.setdefault(
        "statusLine",
        {"type": "command", "command": "/opt/statusline.sh"},
    )

    settings_file.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    print(
        f"[post_install] Claude settings configured: {settings_file}", file=sys.stderr
    )


def setup_tmux_config():
    """Configure tmux with 200k history, mouse support, and vi keys."""
    tmux_conf = Path.home() / ".tmux.conf"

    if tmux_conf.exists():
        print("[post_install] Tmux config exists, skipping", file=sys.stderr)
        return

    config = """\
# 200k line scrollback history
set-option -g history-limit 200000

# Enable mouse support
set -g mouse on

# Use vi keys in copy mode
setw -g mode-keys vi

# Start windows and panes at 1, not 0
set -g base-index 1
setw -g pane-base-index 1

# Renumber windows when one is closed
set -g renumber-windows on

# Faster escape time for vim
set -sg escape-time 10

# True color support
set -g default-terminal "tmux-256color"
set -ag terminal-overrides ",xterm-256color:RGB"

# Terminal features (ghostty, cursor shape in vim)
set -as terminal-features ",xterm-ghostty:RGB"
set -as terminal-features ",xterm*:RGB"
set -ga terminal-overrides ",xterm*:colors=256"
set -ga terminal-overrides '*:Ss=\\E[%p1%d q:Se=\\E[ q'

# Status bar
set -g status-style 'bg=#333333 fg=#ffffff'
set -g status-left '[#S] '
set -g status-right '%Y-%m-%d %H:%M'
"""
    tmux_conf.write_text(config, encoding="utf-8")
    print(f"[post_install] Tmux configured: {tmux_conf}", file=sys.stderr)


def fix_directory_ownership():
    """Fix ownership of mounted volumes that may have root ownership."""
    uid = os.getuid()
    gid = os.getgid()

    dirs_to_fix = [
        Path.home() / ".claude",
        Path("/commandhistory"),
        Path.home() / ".config" / "gh",
    ]

    for dir_path in dirs_to_fix:
        if dir_path.exists():
            try:
                # Use sudo to fix ownership if needed
                stat_info = dir_path.stat()
                if stat_info.st_uid != uid:
                    subprocess.run(
                        ["sudo", "chown", "-R", f"{uid}:{gid}", str(dir_path)],
                        check=True,
                        capture_output=True,
                    )
                    print(
                        f"[post_install] Fixed ownership: {dir_path}", file=sys.stderr
                    )
            except (PermissionError, subprocess.CalledProcessError) as e:
                print(
                    f"[post_install] Warning: Could not fix ownership of {dir_path}: {e}",
                    file=sys.stderr,
                )


def setup_global_gitignore():
    """Set up global gitignore and local git config.

    Since ~/.gitconfig is mounted read-only from host, we create a local
    config file that includes the host config and adds container-specific
    settings like core.excludesfile and delta configuration.

    GIT_CONFIG_GLOBAL env var (set in devcontainer.json) points git to this
    local config as the "global" config.
    """
    home = Path.home()
    gitignore = home / ".gitignore_global"
    local_gitconfig = home / ".gitconfig.local"
    host_gitconfig = home / ".gitconfig"

    # Create global gitignore with common patterns
    patterns = """\
# Claude Code
.claude/

# macOS
.DS_Store
.AppleDouble
.LSOverride
._*

# Python
*.pyc
*.pyo
__pycache__/
*.egg-info/
.eggs/
*.egg
.venv/
venv/
.mypy_cache/
.ruff_cache/

# Node
node_modules/
.npm/

# Editors
*.swp
*.swo
*~
.idea/
.vscode/
*.sublime-*

# Misc
*.log
.env.local
.env.*.local
"""
    gitignore.write_text(patterns, encoding="utf-8")
    print(f"[post_install] Global gitignore created: {gitignore}", file=sys.stderr)

    # Create local git config that includes host config and sets excludesfile + delta
    # Delta config is included here so it works even if host doesn't have it configured
    local_config = f"""\
# Container-local git config
# Includes host config (mounted read-only) and adds container settings

[include]
    path = {host_gitconfig}

[core]
    excludesfile = {gitignore}
    pager = delta

[interactive]
    diffFilter = delta --color-only

[delta]
    navigate = true
    light = false
    line-numbers = true
    side-by-side = false

[merge]
    conflictstyle = diff3

[diff]
    colorMoved = default

[gpg "ssh"]
    program = /usr/bin/ssh-keygen
"""
    local_gitconfig.write_text(local_config, encoding="utf-8")
    print(
        f"[post_install] Local git config created: {local_gitconfig}", file=sys.stderr
    )


def run_post_install_hook():
    """Run a per-project post-install hook, if one is present.

    Convention-based: if `.devcontainer/post-install.local.py` or
    `.devcontainer/post-install.local.sh` exists in the project (surfaced
    inside the container via the read-only `.devcontainer/` bind mount), run
    it as the final post-install step. Dispatch is by extension:
    `.py` via `uv run --no-project`, `.sh` via `bash`.

    This runs last so the hook sees a fully configured environment: claude on
    PATH, base plugins installed, settings written, and ~/.claude/ set up.

    The hook can read and execute files under `.devcontainer/` but cannot
    write back (the mount is read-only); persistent state must go elsewhere
    (~/.claude/, ~/.config/, etc.).

    Failures are non-fatal: a non-zero exit or a missing interpreter logs a
    warning and returns, so postCreateCommand still succeeds.
    """
    hook_py = Path("/workspace/.devcontainer/post-install.local.py")
    hook_sh = Path("/workspace/.devcontainer/post-install.local.sh")

    if hook_py.exists() and hook_sh.exists():
        print(
            "[post_install] Warning: both post-install.local.py and "
            "post-install.local.sh exist; refusing to run either. Pick one.",
            file=sys.stderr,
        )
        return
    elif hook_py.exists():
        cmd = ["uv", "run", "--no-project", str(hook_py)]
        hook = hook_py
    elif hook_sh.exists():
        cmd = ["bash", str(hook_sh)]
        hook = hook_sh
    else:
        print("[post_install] No local hook found, skipping.", file=sys.stderr)
        return

    print(f"[post_install] Running local hook: {hook}", file=sys.stderr)
    try:
        # Inherit stdout/stderr (do not capture) so the user sees progress
        # live in the postCreate log. No timeout: hooks may be long-running.
        result = subprocess.run(cmd, check=False)
    except (FileNotFoundError, OSError) as e:
        print(
            f"[post_install] Warning: could not run local hook ({e})",
            file=sys.stderr,
        )
        return

    if result.returncode != 0:
        print(
            f"[post_install] Warning: local hook exited {result.returncode}",
            file=sys.stderr,
        )
    else:
        print(f"[post_install] Local hook complete: {hook}", file=sys.stderr)


def main():
    """Run all post-install configuration."""
    print("[post_install] Starting post-install configuration...", file=sys.stderr)

    # Plugins first: setup_onboarding_bypass() writes auth state into
    # ~/.claude.json (which lives on the named volume), and we don't want
    # plugin installation to depend on, or be disrupted by, that state.
    # The only prerequisite is the claude binary on PATH (provided by the
    # COPY --from=claude-install stage in the Dockerfile).
    install_claude_plugins()

    setup_onboarding_bypass()
    setup_claude_settings()
    setup_tmux_config()
    fix_directory_ownership()
    setup_global_gitignore()

    # Last: run the per-project hook, after base setup is fully in place.
    run_post_install_hook()

    print("[post_install] Configuration complete!", file=sys.stderr)


if __name__ == "__main__":
    main()
