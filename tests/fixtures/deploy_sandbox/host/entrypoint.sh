#!/bin/sh
# Start Docker, stand-ins for the public ingress, and sshd in the sandbox host.
set -eu
dind dockerd --host=unix:///var/run/docker.sock --group docker > /var/log/dockerd.log 2>&1 &
# The applications bind to loopback only; these forwarders play the tunnel or reverse
# proxy that makes them public (the test publishes 9080/9081 on the machine's loopback).
socat TCP-LISTEN:9080,fork,reuseaddr TCP:127.0.0.1:18080 &
socat TCP-LISTEN:9081,fork,reuseaddr TCP:127.0.0.1:18081 &
exec /usr/sbin/sshd -D -e
