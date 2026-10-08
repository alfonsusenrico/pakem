# Throwaway deployment host for tests/test_deploy_sandbox.py: sshd with a non-root
# deploy user, Docker in Docker for the deployed containers, and sudo limited to the
# installed release_host.py (the --owner root mode).
FROM docker:29.4.3-dind@sha256:685b91dca8eab7de1dce1c303dbb7a763e4082d6a60db10968adf3295fbd2495
RUN apk add --no-cache openssh-server python3 sudo socat \
    && (addgroup -S docker 2> /dev/null || true) \
    && adduser -D -s /bin/sh deploy \
    && addgroup deploy docker \
    && sed -i 's/^deploy:!:/deploy:*:/' /etc/shadow \
    && install -d -m 0700 -o deploy -g deploy /home/deploy/.ssh /home/deploy/apps /home/deploy/apps/sample \
    && install -d -m 0755 -o root -g root /srv/sample /usr/local/libexec/af-deploy \
    && ssh-keygen -A
COPY sshd_config /etc/ssh/sshd_config
COPY --chown=deploy:deploy --chmod=0600 authorized_keys /home/deploy/.ssh/authorized_keys
COPY --chmod=0644 release_host.py /usr/local/libexec/af-deploy/release_host.py
COPY --chmod=0440 sudoers /etc/sudoers.d/af-deploy
COPY --chmod=0755 entrypoint.sh /usr/local/bin/af-sandbox-entrypoint
ENTRYPOINT ["/usr/local/bin/af-sandbox-entrypoint"]
