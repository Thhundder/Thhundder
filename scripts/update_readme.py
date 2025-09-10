#!/usr/bin/env python3
"""
Update README projects section from projects.json and ensure repositories exist.

- Reads projects.json (list of objects with {name, language, repo})
- Normalizes language to a known marker key
- Generates badges under the correct <!-- PROJECTS:<key>:START/END --> markers
- Checks if repo https://github.com/<GH_OWNER>/<repo> exists
- Creates the repo via GitHub API if it doesn't (requires GH_PAT with public_repo or repo scope)

Environment:
  GH_OWNER: GitHub username/owner (e.g., "Thhundder")
  GH_PAT:   Personal Access Token (used only for repo existence/creation)

Exit codes:
  0 on success, non-zero on fatal errors (missing files, invalid JSON, etc.)
"""

import json
import os
import re
import sys
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

README_PATH = "README.md"
PROJECTS_JSON = "projects.json"

GH_OWNER = os.getenv("GH_OWNER", "").strip() or "Thhundder"
GH_PAT = os.getenv("GH_PAT", "").strip()

# Language normalization: alias -> (DisplayName, marker_key)
LANG_MAP = {
    # C
    "c": ("C", "C"),
    "c++": ("C++", "cpp"),
    "cpp": ("C++", "cpp"),
    "python": ("Python", "python"),
    "py": ("Python", "python"),
    "typescript": ("TypeScript", "typescript"),
    "ts": ("TypeScript", "typescript"),
    "shell": ("Shell", "shell"),
    "bash": ("Shell", "shell"),
    "sh": ("Shell", "shell"),
    "zsh": ("Shell", "shell"),
    "docker": ("Docker", "docker"),
    "git": ("Git", "git"),
}

# Regex to find any PROJECTS:<key> marker pair in README
MARKER_PAIR_RE = re.compile(
    r"(<!--\s*PROJECTS:([A-Za-z0-9\+\-]+):START\s*-->)(.*?)(<!--\s*PROJECTS:\2:END\s*-->)",
    flags=re.DOTALL,
)


def eprint(*args):
    print(*args, file=sys.stderr)


def normalize_language(raw):
    if not raw:
        return None
    key = str(raw).strip().lower()
    return LANG_MAP.get(key)  # -> (DisplayName, marker_key) or None


def build_badge_md(name, owner, repo):
    """
    Build the fixed-format badge (message = name, URL-encoded).
    """
    encoded = quote(str(name), safe="")
    img = (
        f"https://img.shields.io/static/v1?label=&message={encoded}"
        f"&color=000605&logo=github&logoColor=FFFFFF&labelColor=000605"
    )
    href = f"https://github.com/{owner}/{repo}"
    return f"[![{name}]({img})]({href})"


def replace_between_markers(readme_text, marker_key, inner_text):
    """
    Replace content between:
      <!-- PROJECTS:{marker_key}:START -->...<!-- PROJECTS:{marker_key}:END -->
    WITHOUT inserting any newlines (keeps everything inline for table cells).
    If markers are missing, returns the original text.
    """
    start = f"<!-- PROJECTS:{marker_key}:START -->"
    end = f"<!-- PROJECTS:{marker_key}:END -->"
    pattern = re.compile(re.escape(start) + r"(.*?)" + re.escape(end), flags=re.DOTALL)
    if not pattern.search(readme_text):
        return readme_text
    replacement = f"{start}{inner_text}{end}"
    return pattern.sub(replacement, readme_text, count=1)



def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def github_repo_exists(owner, repo, token):
    """
    Return True if repo exists under owner, False if 404, raise on other errors.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}"
    req = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"token {token}" if token else "",
            "User-Agent": "gh-readme-updater",
        },
    )
    try:
        with urlopen(req) as resp:
            return 200 <= resp.status < 300
    except HTTPError as err:
        if err.code == 404:
            return False
        eprint(f"[error] GET {url} failed: HTTP {err.code}")
        raise
    except URLError as err:
        eprint(f"[error] GET {url} failed: {err}")
        raise


def github_create_repo(owner, repo, token, private=False, description=""):
    """
    Create a repository under the authenticated user account.
    Requires a PAT with public_repo (public) or repo (private) scope.

    Note: The default branch will follow the user’s GitHub settings. If your default
    is 'main', GitHub will create 'main' when auto_init=True.
    """
    url = "https://api.github.com/user/repos"
    body = json.dumps(
        {
            "name": repo,
            "private": bool(private),
            "description": description,
            "auto_init": True,  # creates initial commit with README.md
        }
    ).encode("utf-8")

    req = Request(
        url,
        data=body,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"token {token}",
            "Content-Type": "application/json",
            "User-Agent": "gh-readme-updater",
        },
        method="POST",
    )

    try:
        with urlopen(req) as resp:
            if 200 <= resp.status < 300:
                return True
            eprint(f"[error] POST {url} unexpected status: {resp.status}")
            return False
    except HTTPError as err:
        if err.code == 422:
            # Validation failed (e.g., name already exists) — treat as non-fatal
            eprint(f"[warn] create repo '{repo}' returned 422 (possibly exists/already taken).")
            return False
        eprint(f"[error] POST {url} failed: HTTP {err.code} – {err.read().decode('utf-8', 'ignore')}")
        return False
    except URLError as err:
        eprint(f"[error] POST {url} failed: {err}")
        return False


def collect_readme_marker_keys(readme_text):
    """
    Scan README for all marker keys present and return a set of keys.
    This allows us to clear cells with no projects (avoid stale badges).
    """
    keys = set()
    for _full, key, _inner, _end in MARKER_PAIR_RE.findall(readme_text):
        keys.add(key)
    return keys


def main():
    # 1) Load projects.json
    try:
        data = load_json(PROJECTS_JSON)
    except FileNotFoundError:
        eprint(f"[error] '{PROJECTS_JSON}' not found.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        eprint(f"[error] Invalid JSON in '{PROJECTS_JSON}': {e}")
        sys.exit(1)

    if not isinstance(data, list):
        eprint("[error] projects.json must be a list of objects.")
        sys.exit(1)

    # 2) Read README
    try:
        with open(README_PATH, "r", encoding="utf-8") as f:
            readme = f.read()
    except FileNotFoundError:
        eprint(f"[error] '{README_PATH}' not found.")
        sys.exit(1)

    # 3) Determine which marker keys exist in README (so we can clear empty ones)
    marker_keys_in_readme = collect_readme_marker_keys(readme)
    if not marker_keys_in_readme:
        eprint("[warn] No PROJECTS markers found in README. Nothing to update.")
        marker_keys_in_readme = set()  # still proceed for repo creation

    # 4) Group badges by marker key (preserve order, dedupe by repo per key)
    badges_by_key = {}  # key -> [badges...]
    seen_by_key = {}    # key -> set(repos)
    repos_needed = []   # list of (owner, repo) to ensure exist

    for idx, item in enumerate(data, 1):
        name = item.get("name")
        lang = item.get("language")
        repo = item.get("repo")

        if not name or not lang or not repo:
            eprint(f"[warn] entry #{idx} is incomplete (requires name, language, repo) – skipped.")
            continue

        norm = normalize_language(lang)
        if not norm:
            eprint(f"[warn] language '{lang}' is not mapped – skipped.")
            continue
        _display, key = norm

        # Badge for this project
        badge = build_badge_md(name=name, owner=GH_OWNER, repo=repo)

        # Initialize structures
        if key not in badges_by_key:
            badges_by_key[key] = []
            seen_by_key[key] = set()

        # Dedupe same repo within the same key
        if repo not in seen_by_key[key]:
            badges_by_key[key].append(badge)
            seen_by_key[key].add(repo)

        # Track repo to ensure existence
        repos_needed.append((GH_OWNER, repo))

    # 5) Ensure repositories exist (if GH_PAT provided)
    if repos_needed:
        if GH_PAT:
            for owner, repo in repos_needed:
                try:
                    exists = github_repo_exists(owner, repo, GH_PAT)
                except Exception:
                    # On transient errors, don't fail the whole job; continue
                    continue
                if not exists:
                    created = github_create_repo(owner, repo, GH_PAT, private=False, description="")
                    if created:
                        print(f"[info] Created repository: {owner}/{repo}")
                    else:
                        eprint(f"[warn] Could not create repository: {owner}/{repo}")
        else:
            eprint("[warn] GH_PAT not set; skipping repository existence/creation step.")

    # 6) Inject badges into README for all marker keys present
    new_readme = readme
    for key in marker_keys_in_readme:
        badges = badges_by_key.get(key, [])
        inner = " ".join(badges) if badges else ""
        new_readme = replace_between_markers(new_readme, key, inner)

    # 7) Write README only if changed
    if new_readme != readme:
        with open(README_PATH, "w", encoding="utf-8") as f:
            f.write(new_readme)
        print("[info] README.md updated.")
    else:
        print("[info] No changes to README.md.")


if __name__ == "__main__":
    main()
