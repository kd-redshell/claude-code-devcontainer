# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A sandboxed devcontainer for running Claude Code with `bypassPermissions` safely enabled. Designed for security audit workflows and untrusted repository exploration. The container isolates filesystem access while providing a full development toolchain.

## Key Commands

```bash
# Build the container image (from host)
devc .                        # install template + start (base profile)
devc . --target android       # install template + start (android profile)
devc up                       # start devcontainer
devc rebuild                  # rebuild preserving volumes
devc destroy                  # tear down all resources (containers, volumes, images)

# Inside the container
uv run --no-project /opt/post_install.py   # re-run post-install setup

# Docker image build (standalone, no devcontainer CLI)
docker build -t claude-devcontainer .                               # base
docker build -t claude-devcontainer-base:latest . && \
  docker build -f Dockerfile.android -t claude-devcontainer-android .  # android
```

There is no test suite or linter configuration. This is a configuration/tooling project.

## Architecture

### Build Profiles

The project supports multiple build profiles via separate Dockerfiles:

| Profile | Dockerfile | Use Case | Extra Size |
|---------|------------|----------|------------|
| `base` (default) | `Dockerfile` | General development, security audits | -- |
| `android` | `Dockerfile.android` | Android SDK/NDK, emulator, appium | ~8 GB |

Profile Dockerfiles extend the base image (`FROM claude-devcontainer-base:latest`). When building a non-base profile, `install.sh` automatically pre-builds and tags the base image first.

Select a profile with `devc . --target android` or `devc template --target android`. The default is `base`.

### Dockerfile (base image, multi-stage build)
1. **`uv` stage** -- copies the uv binary from Astral's image.
2. **Final stage** -- Ubuntu 24.04 devcontainer base with system packages, Python 3.13, Node.js (via fnm), and CLI tools. The Claude Code binary is installed via `curl … | bash`, with the symlink dereferenced in-place so the binary survives the `~/.claude/` named volume mount at runtime. `post_install.py` and `statusline.sh` are copied to `/opt/` (outside the volume, so they refresh on rebuild).

### Dockerfile.android (profile)
Extends the base image with Android SDK/NDK, emulator system images, JDK 17, appium, mitmproxy, and frida-tools.

### post_install.py (container creation hook)
Runs via `postCreateCommand` after the container starts. Execution order matters:
1. **Plugin installation** -- runs first, before auth state exists. Iterates `CLAUDE_PLUGINS` list and calls `claude plugin marketplace add` for each.
2. **Onboarding bypass** -- seeds `~/.claude.json` with auth state when `CLAUDE_CODE_OAUTH_TOKEN` is set. Workaround for Claude Code issue #8938.
3. **Claude settings** -- sets `permissions.defaultMode = "bypassPermissions"`, and seeds `statusLine` to point at `/opt/statusline.sh` (via `setdefault`, so a user-configured status line is preserved).
4. **Tmux config** -- 200k scrollback, mouse support, vi keys.
5. **Directory ownership** -- fixes mounted volumes that may have root ownership.
6. **Git config** -- global gitignore, git-delta pager integration.
7. **Per-project hook** -- if `.devcontainer/post-install.local.{py,sh}` exists, run it. Dispatch by extension (`.py` via `uv run --no-project`, `.sh` via `bash`). Errors are logged but do not fail `postCreateCommand`.

### install.sh (host-side CLI, `devc` command)
Bash script providing 16 subcommands for container lifecycle management. Notable behaviors:
- `devc mount` preserves custom mounts across `devc .` (template reinstall) by extracting and re-merging them into devcontainer.json.
- `devc sync` copies `.claude/projects/` session data from devcontainers to the host for `/insights` integration.
- `devc destroy` discovers all associated Docker resources (containers, volumes, images) by label before removal.
- `devc list` enumerates all devcontainers (running and stopped) by the `devcontainer.local_folder` label and renders a table of name, status, container ID, image, and host folder.

## Volume Architecture

`~/.claude/` inside the container is a Docker named volume (defined in devcontainer.json `mounts`), not part of the image filesystem. This has critical implications:

- Image-layer contents under `/home/vscode/.claude/` are only copied on first volume creation. Subsequent `devc rebuild` keeps the old volume data.
- Plugin installs, settings writes, and auth state must happen at container creation time (in post_install.py), not in the Dockerfile.
- The Claude binary is placed at `~/.local/bin/claude` (on PATH) specifically to avoid the named volume at `~/.claude/`.

Other named volumes: `/commandhistory` (shell history), `~/.config/gh` (GitHub CLI auth).

## Configuration Files

Two user-facing config files live under `.devcontainer/`, both surfaced inside the container via the existing read-only `.devcontainer/` bind mount:

- **`env`** (build-time) -- key=value pairs consumed as Docker build args. `install.sh:cmd_template` whitelists `NODE_VERSION` (base image) and `ANDROID_*` (android profile) and injects them into `devcontainer.json` `build.args` (for the profile build) or passes them to `docker build` in `maybe_build_base` (for the base image build). Edit + `devc rebuild` to apply. Shipped as a template with sensible defaults.
- **`runtime.env`** (runtime) -- key=value pairs sourced into every interactive zsh session via `.zshrc` using `set -a` / `source` / `set +a`. Edit + open a new shell to apply; no rebuild needed. Created empty (by `install.sh:cmd_template` for new installs and `initializeCommand` for pre-existing installs) and intended to be `.gitignore`'d since it typically holds secrets. Only interactive zsh shells pick it up — not `devc exec <cmd>` or non-zsh sessions. Neither `install.sh` nor `initializeCommand` ever writes *content* to this file (both only `touch` it); its contents are entirely user-managed. `.zshrc` also sources every `*.env` in the optional `runtime.env.d/` directory after `runtime.env` (alphabetical, later-wins, `.d/` overrides the base), letting env be composed from multiple files (e.g. committed shared config + gitignored secrets). The `(N)` glob qualifier makes the `.d/` lookup a no-op when the directory is absent.

## Security-Relevant Mount Setup

`devcontainer.json` runs `initializeCommand` on the host before container start to create sanitized copies of git config and hooks (`.git/config.devc`, `.git/hooks.devc/`, `~/.gitconfig.devc`). These are bind-mounted read-only into the container, preventing the container from modifying the host's git configuration. This is a mitigation for container escape via git hooks. `initializeCommand` also `touch`es `.devcontainer/runtime.env` so the file always exists before container start, even on installs predating that feature.

## Modifying Plugins

Edit the `CLAUDE_PLUGINS` list at the top of `post_install.py`. Changes take effect on the next container rebuild without needing to delete the `~/.claude` Docker volume.
