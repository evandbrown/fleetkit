# Stub guest for proving the host daemon's docker backend without Chromium.
# Build from harness/host:  docker build -t fleetkit-guest-stub:dev tests/stubguest
ARG ALPINE_IMAGE=alpine:3.22@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8
FROM ${ALPINE_IMAGE}
RUN apk add --no-cache python3
COPY stubguestd.py /usr/local/bin/stubguestd.py
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
ENTRYPOINT ["python3", "/usr/local/bin/stubguestd.py"]
