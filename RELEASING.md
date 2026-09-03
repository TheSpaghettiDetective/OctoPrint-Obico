# Releasing OctoPrint-Obico

OctoPrint-Obico releases are published as GitHub Releases. The plugin's
software-update hook checks GitHub Releases and installs the source archive for
the selected release tag. There is no PyPI publication or separate release
artifact upload.

## Prepare the release

1. Choose the versions for the release commands:

   ```bash
   previous_release_version=2.6.2
   release_version=2.7.0
   ```

2. Start from `dev` and update the remote references:

   ```bash
   git switch dev
   git fetch origin --prune --tags
   git pull --ff-only origin dev
   ```

3. Confirm that `dev` contains the intended release changes, that `master` can
   be fast-forwarded to it, and that the new tag does not already exist:

   ```bash
   git log --oneline "${previous_release_version}"..HEAD
   git merge-base --is-ancestor origin/master HEAD
   if git rev-parse -q --verify "refs/tags/${release_version}"; then
     echo "Tag ${release_version} already exists"
     exit 1
   fi
   ```

4. Update `project.version` in `pyproject.toml`. Do not change the version in
   `package.json`; that package is used only for frontend build dependencies.

5. Run the unit tests and compile the frontend assets:

   ```bash
   docker compose exec -T op python3 -m unittest discover -s tests -v
   npm run build
   ```

## Publish the release

1. Review the complete diff and commit the version bump:

   ```bash
   git status --short --branch
   git diff
   git add pyproject.toml RELEASING.md
   git commit -m "Bump version ${release_version}"
   ```

   Include updates to this document in the same commit when applicable.

2. Build from the exact committed source in a temporary directory, then verify
   its metadata and OctoPrint entry point. Building from a Git archive prevents
   ignored development files from leaking into the verification package.

   ```bash
   release_check_dir=$(mktemp -d)
   git archive HEAD | tar -x -C "${release_check_dir}"
   uvx --from build pyproject-build --sdist --wheel "${release_check_dir}" \
     --outdir "${release_check_dir}/dist"

   release_wheel="${release_check_dir}/dist/octoprint_obico-${release_version}-py3-none-any.whl"
   unzip -p "${release_wheel}" \
     "octoprint_obico-${release_version}.dist-info/METADATA"
   unzip -p "${release_wheel}" \
     "octoprint_obico-${release_version}.dist-info/entry_points.txt"
   ```

   The metadata version must match the release tag, and the entry-point output
   must contain:

   ```text
   [octoprint.plugin]
   obico = octoprint_obico
   ```

3. Push `dev`, then fast-forward `master` to the same commit:

   ```bash
   git push origin dev
   git push origin dev:master
   ```

   A non-fast-forward rejection must be investigated rather than overridden.

4. Write release notes that summarize the changes since the previous tag, then
   create the GitHub Release. Version tags do not use a `v` prefix.

   ```bash
   gh release create "${release_version}" \
     --repo TheSpaghettiDetective/OctoPrint-Obico \
     --target master \
     --title "${release_version}" \
     --notes-file /path/to/release-notes.md
   ```

   `gh release create` creates the lightweight version tag automatically. Do
   not upload the locally built wheel or source distribution; OctoPrint installs
   GitHub's source archive for the tag.

## Verify the release

1. Confirm the release is published and targets the expected commit:

   ```bash
   gh release view "${release_version}" \
     --repo TheSpaghettiDetective/OctoPrint-Obico \
     --json tagName,targetCommitish,isDraft,isPrerelease,url
   git ls-remote origin refs/heads/master "refs/tags/${release_version}"
   ```

2. Open the release page and confirm the title, tag, notes, and source archives:

   ```text
   https://github.com/TheSpaghettiDetective/OctoPrint-Obico/releases/tag/RELEASE_VERSION
   ```

3. In an OctoPrint installation running the previous plugin version, refresh
   Software Update and confirm that the new version is offered. Install it and
   verify that OctoPrint restarts with the new plugin version.
