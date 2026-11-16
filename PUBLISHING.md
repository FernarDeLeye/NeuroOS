# Maintainer publication checklist — no publication has been performed

Only use this standalone repository. Never set a public remote on the experimental
checkout, push --mirror, or merge its old history. There is no remote configured.

1. Review LICENSE/NOTICE and replace only the explicitly pending copyright holder
   with the owner's authorized public attribution. Do not infer identity from Git.
   Commit the attribution, rebuild artifacts from that commit, update the release
   tag and rerun audit/checksums before any upload. Do not rewrite a published tag.
2. Complete or explicitly retain the clean-Debian qualification caveat. Inspect
   artifact manifests and audit report, source archive and .deb contents. Check
   SHA256SUMS. Do not upload the old private package or developer diagnostic bundle.
3. Obtain the owner's final authorization for external repository/upload/visibility.
   The commands below are instructions only; preparation does not execute them.
4. On GitHub create an EMPTY repository in the owner's chosen account. Initially
   choose Private; do not initialize README/license/gitignore. Configure Issues
   and Discussions as desired. Review owner/account/visibility before changing it.
5. From this clean copy only, after authorization, replace OWNER/REPOSITORY below:

```
git remote add origin https://github.com/OWNER/REPOSITORY.git
git push -u origin main
git push origin refs/tags/v0.1.0-alpha.1
```

6. Review the hosted files and commit history while private. Only after final owner
   approval change visibility to Public in Settings. Enable private vulnerability
   reporting in Settings → Security and quality → Advanced Security; confirm the
   Security/Advisories private-report button exists. Do not promise an unconfigured
   contact route. Add a confirmed alternative private contact if desired.
7. Create a DRAFT PRE-RELEASE from the already pushed tag, not a new branch head.
   Attach the source archive, .deb only with its validation caveat, SHA256SUMS and
   VALIDATION_REPORT.json. Inspect the draft before publishing. For example:

```
gh release create v0.1.0-alpha.1 --verify-tag --draft --prerelease \
  --title 'NeuroOS 0.1.0-alpha.1' --notes-file RELEASE_NOTES.md \
  /PATH/TO/ARTIFACTS/neuroos-0.1.0-alpha.1-source.tar.gz \
  /PATH/TO/ARTIFACTS/neuroos_0.1.0~alpha1-1_amd64.deb \
  /PATH/TO/ARTIFACTS/SHA256SUMS /PATH/TO/ARTIFACTS/VALIDATION_REPORT.json
```

8. After owner approval publish the prerelease and fill ANNOUNCEMENT.md's repository
   link. Respect each community's rules; do not post automatically or claim that
   alpha safety/model quality/clean installation is established when it is not.

Official references checked:
- https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository
- https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/configure-for-a-repository
- https://cli.github.com/manual/gh_release_create
