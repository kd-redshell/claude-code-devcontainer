# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A sandboxed devcontainer for running Claude Code with `bypassPermissions` safely enabled. Designed for security audit workflows and untrusted repository exploration. The container isolates filesystem access while providing a full development toolchain.

## Key Commands

```bash
# Build the container image (from host)
devc up                       # start devcontainer
devc rebuild                  # rebuild preserving volumes
devc destroy                  # tear down all resources (containers, volumes, images)

# Inside the container
uv run --no-project /opt/post_install.py   # re-run post-install setup

# Docker image build (standalone, no devcontainer CLI)
docker build -t claude-devcontainer .
```

There is no test suite or linter configuration. This is a configuration/tooling project.

## Architecture

Three files define the entire system:

### Dockerfile (multi-stage build)
1. **`uv` stage** -- copies the uv binary from Astral's image.
2. **`claude-install` stage** -- downloads the Claude Code binary in isolation. This stage exists so the ~8 GB Android SDK layers aren't in memory during the download (fixes OOM during `docker buildx`). The binary is staged at `/tmp/claude-stage/claude` via `cp -L` to dereference the symlink.
3. **Final stage** -- Ubuntu 24.04 devcontainer base with system packages, Android SDK/NDK, Python 3.13, Node.js 22, and CLI tools. The Claude binary is copied in via `COPY --from=claude-install`.

### post_install.py (container creation hook)
Runs via `postCreateCommand` after the container starts. Execution order matters:
1. **Plugin installation** -- runs first, before auth state exists. Iterates `CLAUDE_PLUGINS` list and calls `claude plugin marketplace add` for each.
2. **Onboarding bypass** -- seeds `~/.claude.json` with auth state when `CLAUDE_CODE_OAUTH_TOKEN` is set. Workaround for Claude Code issue #8938.
3. **Claude settings** -- sets `permissions.defaultMode = "bypassPermissions"`.
4. **Tmux config** -- 200k scrollback, mouse support, vi keys.
5. **Directory ownership** -- fixes mounted volumes that may have root ownership.
6. **Git config** -- global gitignore, git-delta pager integration.

### install.sh (host-side CLI, `devc` command)
859-line bash script providing 15 subcommands for container lifecycle management. Notable behaviors:
- `devc mount` preserves custom mounts across `devc .` (template reinstall) by extracting and re-merging them into devcontainer.json.
- `devc sync` copies `.claude/projects/` session data from devcontainers to the host for `/insights` integration.
- `devc destroy` discovers all associated Docker resources (containers, volumes, images) by label before removal.

## Volume Architecture

`~/.claude/` inside the container is a Docker named volume (defined in devcontainer.json `mounts`), not part of the image filesystem. This has critical implications:

- Image-layer contents under `/home/vscode/.claude/` are only copied on first volume creation. Subsequent `devc rebuild` keeps the old volume data.
- Plugin installs, settings writes, and auth state must happen at container creation time (in post_install.py), not in the Dockerfile.
- The Claude binary is placed at `~/.local/bin/claude` (on PATH) specifically to avoid the named volume at `~/.claude/`.

Other named volumes: `/commandhistory` (shell history), `~/.config/gh` (GitHub CLI auth).

## Security-Relevant Mount Setup

`devcontainer.json` runs `initializeCommand` on the host before container start to create sanitized copies of git config and hooks (`.git/config.devc`, `.git/hooks.devc/`, `~/.gitconfig.devc`). These are bind-mounted read-only into the container, preventing the container from modifying the host's git configuration. This is a mitigation for container escape via git hooks.

## Modifying Plugins

Edit the `CLAUDE_PLUGINS` list at the top of `post_install.py`. Changes take effect on the next container rebuild without needing to delete the `~/.claude` Docker volume.
