# Major 3 is the maintained image line (major 2 stopped at 2.1.2 and ships Python 3.14.0).
# Bookworm, not trixie: the docker-in-docker feature's install.sh refuses trixie with its default
# moby=true ("The 'moby' option is not supported"), so a trixie base fails every rebuild.
FROM mcr.microsoft.com/devcontainers/python:3-3.14-bookworm

# Yarn rotated its Debian apt signing key, which breaks `apt-get update` and any
# feature install that runs it (e.g. docker-in-docker). We use npm, not the yarn
# apt repo, so drop the repo definition before features are layered on top.
RUN rm -f /etc/apt/sources.list.d/yarn.list
