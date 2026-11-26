# Maintainer publication checklist — Alpha 2 (not yet published)

This standalone checkout continues the existing public `main` history at Alpha 1.
Never push from the experimental NeuroOS checkout or merge its private history.
No GitHub remote is configured in this candidate; no publication is done by
preparing it. Add the existing repository as `origin` only after publication approval.

1. Review LICENSE/NOTICE, third-party notices, file audit and checksums. Copyright
   2026 DeAtH de FraG was explicitly authorized by the owner; do not infer identity
   from Git. Do not rewrite Alpha 1 history/tag.
2. Review VALIDATION.md. The available environment had no QEMU/KVM clean Debian VM; do
   not describe package installation or upgrade/remove as clean-VM verified.
3. After separate final authorization, from this release checkout publish the
   continuation commit and tag (commands below are instructions only):

```sh
git switch main
git remote add origin https://github.com/FernarDeLeye/NeuroOS.git
git push -u origin main
git push origin refs/tags/v0.1.0-alpha.2
```

4. Inspect the hosted commit/tag and files. Do not change repository visibility or
   create a GitHub Release until separately approved.
5. After authorization, create a draft prerelease from the pushed tag and attach the
   source archive, Debian package and checksum/report files. For example:

```sh
gh release create v0.1.0-alpha.2 --verify-tag --draft --prerelease \
  --title 'NeuroOS 0.1.0-alpha.2' --notes-file RELEASE_NOTES.md \
  /PATH/TO/neuroos-0.1.0-alpha.2-source.tar.gz \
  /PATH/TO/neuroos_0.1.0~alpha2-1_amd64.deb \
  /PATH/TO/SHA256SUMS /PATH/TO/VALIDATION_REPORT.json
```
