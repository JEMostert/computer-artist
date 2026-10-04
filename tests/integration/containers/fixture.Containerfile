# Small native Wayland fixture image for Computer Artist environment tests.
FROM docker.io/library/alpine:3.22
RUN apk add --no-cache foot font-dejavu python3 py3-pyside6 mesa-dri-gallium dbus
