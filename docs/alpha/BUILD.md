# Build and provenance

Build on Debian 13 amd64 as an ordinary user with debhelper, dh-python, dpkg-dev and
Python installed. From the clean release checkout run:

```sh
packaging/build.sh v0.1.0-alpha.2 /tmp/neuroos-alpha2-build
sha256sum /tmp/neuroos-alpha2-build/*.deb
```

The script exports committed source into a fresh directory, records the exact Git
commit in release metadata, sets SOURCE_DATE_EPOCH and uses dpkg-buildpackage. Use a
fresh output path for each build. Byte identity is qualified only for the recorded
toolchain, not arbitrary builders. No model weights, backend binary or developer
state are included. Run the included clean-VM script only inside a disposable Debian
13 VM after reading it; its apt lifecycle checks require sudo inside that VM.
