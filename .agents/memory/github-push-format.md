---
name: GitHub push format
description: The bare-token URL form stops working intermittently; use the username-prefixed insteadOf form instead.
---

## Rule

Never use the bare `https://$GITHUB_TOKEN@github.com/…` push URL. It fails silently with "Repository not found" or "invalid credentials" even when the token itself is valid (curl to /user returns 200).

## Working form

```bash
git -c "url.https://gregoryharco84-ship-it:${GITHUB_TOKEN}@github.com/.insteadOf=https://github.com/" push origin main
```

**Why:** GitHub requires the username in the credential for private-token auth over HTTPS when the token doesn't have org-level access configured. The bare `$TOKEN@github.com` form omits the username, which causes auth to fail on some repo access patterns even though the token is otherwise valid.

**How to apply:** Use this exact command for every `git push` to the WOW-Dashboard repo. Repo: `gregoryharco84-ship-it/WOW-Dashboard`, branch: `main`.
