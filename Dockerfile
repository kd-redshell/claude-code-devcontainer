# Claude Code Devcontainer — Base Image
# Based on Microsoft devcontainer image for better devcontainer integration
#
# Multi-stage build layout:
#   1. "uv"            — grab the uv binary from Astral's official image
#   2. "claude-install" — install the Claude Code binary in a lightweight
#                         stage so profile images don't OOM during buildx
#   3. final stage      — general-purpose devcontainer; copies the Claude
#                         binary in via COPY --from
#
# Profile Dockerfiles (Dockerfile.android, etc.) extend this base image.
# Build this first and tag it before building any profile:
#   docker build -t claude-devcontainer-base:latest -f Dockerfile .

# ---------------------------------------------------------------------------
# Stage: uv binary
# ---------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:0.10@sha256:10902f58a1606787602f303954cea099626a4adb02acbac4c69920fe9d278f82 AS uv

# ---------------------------------------------------------------------------
# Stage: Claude Code installer
# ---------------------------------------------------------------------------
# Runs in its own stage so BuildKit doesn't have heavy layers loaded when
# this process allocates memory.  Only the resulting binary (~240 MB at
# ~/.local/bin/claude) is carried forward via COPY --from.
#
# NOTE: plugins are NOT installed here — they live under ~/.claude/plugins/,
# which is mounted as a Docker named volume at runtime.  Named volumes only
# copy image contents on first creation, so plugin changes in the Dockerfile
# would silently be ignored on rebuilds.  Instead, plugins are installed in
# postCreateCommand (post_install.py) where they always run against the live
# volume.
# ---------------------------------------------------------------------------
FROM mcr.microsoft.com/devcontainers/base:ubuntu24.04@sha256:4bcb1b466771b1ba1ea110e2a27daea2f6093f9527fb75ee59703ec89b5561cb AS claude-install

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

USER vscode
ENV PATH="/home/vscode/.local/bin:$PATH"

# Download and run the native installer, then stage the resolved binary at a
# known location for COPY --from.  The installer places a *symlink* at
# ~/.local/bin/claude pointing into ~/.claude/, which is a Docker named
# volume in the final container — COPY would preserve the symlink and it
# would dangle.  cp -L dereferences it so we carry the real binary forward.
RUN curl -fsSL https://claude.ai/install.sh | bash \
 && mkdir -p /tmp/claude-stage \
 && cp -L /home/vscode/.local/bin/claude /tmp/claude-stage/claude

# ---------------------------------------------------------------------------
# Final stage: base image
# ---------------------------------------------------------------------------
FROM mcr.microsoft.com/devcontainers/base:ubuntu24.04@sha256:4bcb1b466771b1ba1ea110e2a27daea2f6093f9527fb75ee59703ec89b5561cb

ARG TZ
ENV TZ="$TZ"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install additional system packages (base image already includes git, curl, sudo, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
  # Sandboxing support for Claude Code
  bubblewrap \
  socat \
  # Modern CLI tools
  fd-find \
  ripgrep \
  tmux \
  zsh \
  # Build tools
  build-essential \
  # Utilities
  jq \
  nano \
  vim \
  unzip \
  zip \
  # Network tools (for security testing)
  dnsutils \
  ipset \
  iptables \
  iproute2 \
  && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install git-delta
# renovate: datasource=github-releases depName=dandavison/delta
ARG GIT_DELTA_VERSION=0.18.2
RUN ARCH=$(dpkg --print-architecture) && \
  curl -fsSL "https://github.com/dandavison/delta/releases/download/${GIT_DELTA_VERSION}/git-delta_${GIT_DELTA_VERSION}_${ARCH}.deb" -o /tmp/git-delta.deb && \
  dpkg -i /tmp/git-delta.deb && \
  rm /tmp/git-delta.deb

# Install uv (Python package manager) via multi-stage copy
COPY --from=uv /uv /usr/local/bin/uv

# Install fzf from GitHub releases (newer than apt, includes built-in shell integration)
# renovate: datasource=github-releases depName=junegunn/fzf
ARG FZF_VERSION=0.70.0
RUN ARCH=$(dpkg --print-architecture) && \
  case "${ARCH}" in \
    amd64) FZF_ARCH="linux_amd64" ;; \
    arm64) FZF_ARCH="linux_arm64" ;; \
    *) echo "Unsupported architecture: ${ARCH}" && exit 1 ;; \
  esac && \
  curl -fsSL "https://github.com/junegunn/fzf/releases/download/v${FZF_VERSION}/fzf-${FZF_VERSION}-${FZF_ARCH}.tar.gz" | tar -xz -C /usr/local/bin

# Create directories and set ownership (combined for fewer layers)
RUN mkdir -p /commandhistory /workspace /home/vscode/.claude /opt && \
  touch /commandhistory/.bash_history && \
  touch /commandhistory/.zsh_history && \
  chown -R vscode:vscode /commandhistory /workspace /home/vscode/.claude /opt

# Set environment variables
ENV DEVCONTAINER=true
ENV SHELL=/bin/zsh
ENV EDITOR=vim
ENV VISUAL=vim

WORKDIR /workspace

# Switch to non-root user for remaining setup
USER vscode

# Set PATH early so claude and other user-installed binaries are available
ENV PATH="/home/vscode/.local/bin:$PATH"

# ---------------------------------------------------------------------------
# Copy the Claude Code binary from the isolated builder stage.
# Placed in ~/.local/bin/ which is on PATH (not under ~/.claude/ which is
# a Docker named volume and would shadow build-time files).
# Plugins are installed at container creation time by postCreateCommand
# (see post_install.py) so they always reflect the current config.
# ---------------------------------------------------------------------------
COPY --from=claude-install --chown=vscode:vscode \
  /tmp/claude-stage/claude /home/vscode/.local/bin/claude

# Install Python 3.13 via uv (fast binary download, not source compilation)
RUN uv python install 3.13 --default

# Install ast-grep (AST-based code search)
RUN uv tool install ast-grep-cli

# Install fnm (Fast Node Manager) and Node 22
ARG NODE_VERSION=22
ENV FNM_DIR="/home/vscode/.fnm"
RUN curl -fsSL https://fnm.vercel.app/install | bash -s -- --install-dir "$FNM_DIR" --skip-shell && \
  export PATH="$FNM_DIR:$PATH" && \
  eval "$(fnm env)" && \
  fnm install ${NODE_VERSION} && \
  fnm default ${NODE_VERSION}

# Install Oh My Zsh
# renovate: datasource=github-releases depName=deluan/zsh-in-docker
ARG ZSH_IN_DOCKER_VERSION=1.2.1
RUN sh -c "$(curl -fsSL https://github.com/deluan/zsh-in-docker/releases/download/v${ZSH_IN_DOCKER_VERSION}/zsh-in-docker.sh)" -- \
  -p git \
  -x

# Copy zsh configuration
COPY --chown=vscode:vscode .zshrc /home/vscode/.zshrc.custom

# Append custom zshrc to the main one
RUN echo 'source ~/.zshrc.custom' >> /home/vscode/.zshrc

# Copy post_install script
COPY --chown=vscode:vscode post_install.py /opt/post_install.py
