#!/usr/bin/env sh
case "$1" in
  *Username*) printf '%s\n' 'x-access-token' ;;
  *Password*) printf '%s\n' "${ASSISTANT_GITHUB_TOKEN:?missing GitHub token}" ;;
  *) exit 1 ;;
esac
