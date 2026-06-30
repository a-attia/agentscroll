# Pending CI workflow

`ci.yml` here is the GitHub Actions workflow (ruff + pytest on Python
3.10-3.13, plus a wheel/sdist build check). It lives here, rather than under
`.github/workflows/`, only because the initial push was made with a token
that lacked the `workflow` OAuth scope.

To enable CI, grant the scope once and move the file into place:

```bash
gh auth refresh -h github.com -s workflow   # one-time browser confirm
mkdir -p .github/workflows
git mv ci-pending/ci.yml .github/workflows/ci.yml
git rm ci-pending/README.md
git commit -m "Enable GitHub Actions CI"
git push
```

After that you can re-add the CI badge to `README.md`:

```markdown
[![CI](https://github.com/a-attia/agentscroll/actions/workflows/ci.yml/badge.svg)](https://github.com/a-attia/agentscroll/actions/workflows/ci.yml)
```
