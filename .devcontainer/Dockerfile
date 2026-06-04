FROM mcr.microsoft.com/devcontainers/python:1-3.13-bookworm

# Yarn rotated its Debian apt signing key, which breaks `apt-get update` and any
# feature install that runs it (e.g. docker-in-docker). We use npm, not the yarn
# apt repo, so drop the repo definition before features are layered on top.
RUN rm -f /etc/apt/sources.list.d/yarn.list
