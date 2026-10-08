FROM busybox:1.37.0@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e
ARG AF_REVISION=unknown
COPY message.txt /message.txt
RUN mkdir -p /www \
    && printf '{"revision": "%s", "message": "%s"}\n' "$AF_REVISION" "$(cat /message.txt)" > /www/release.json \
    && echo ok > /www/ok.txt
USER 65534
EXPOSE 8000
CMD ["httpd", "-f", "-p", "8000", "-h", "/www"]
