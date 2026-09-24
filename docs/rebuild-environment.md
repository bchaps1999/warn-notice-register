# Python environment for the source-only rebuild

`requirements-rebuild.lock` pins the index packages and their archive hashes
for Python 3.13. It was generated from `requirements.txt` with the exact
versions in `requirements-rebuild.constraints.txt`, which records the package
set used for the staged replay. The lock SHA-256 is
`d75335c6ec22e804f7dfdb2141cc42409ce1c8fc1e2543462a10f19bf1e8d9e3`.
The two Big Local News packages are installed
separately from the Git commits already pinned in `install.sh`:

| Package | Git commit |
| --- | --- |
| `warn-transformer` | `82454b5b767e2b7fa42085f23799f34292996b90` |
| `warn-scraper` | `f7b3dd26af1f3ad700762504bd7c5c0d23979507` |

The staged environment used CPython 3.13.2 on macOS arm64. Production CI and
scheduled workflows now request Python 3.13 and `install.sh` consumes this
hash-checked lock, installs the pinned upstream commits without dependency
resolution, and runs `pip check`. The target Ubuntu runner still needs a fresh
installation/replay check on the exact release commit. Two independent
temporary environments each installed 76 hash-checked index packages, then
built the two pinned Git packages at exactly those commits and installed this
project from the working tree. The installed source code for the two upstream
packages matches the pinned Git trees byte for byte aside from Python cache
files; `uv pip check` passed in both environments.

Preparation needs network access for package archives and the two Git
repositories. The production bootstrap from the repository root is
`./install.sh` with Python 3.13. The following manual sequence records how
the two isolated replay environments were prepared:

```sh
uv venv --python /opt/homebrew/bin/python3.13 /private/tmp/warn-rebuild-env
uv pip install --python /private/tmp/warn-rebuild-env/bin/python \
  --require-hashes -r requirements-rebuild.lock
git clone https://github.com/biglocalnews/warn-transformer /private/tmp/warn-transformer-pinned
git -C /private/tmp/warn-transformer-pinned checkout 82454b5b767e2b7fa42085f23799f34292996b90
git clone https://github.com/biglocalnews/warn-scraper /private/tmp/warn-scraper-pinned
git -C /private/tmp/warn-scraper-pinned checkout f7b3dd26af1f3ad700762504bd7c5c0d23979507
uv pip install --python /private/tmp/warn-rebuild-env/bin/python \
  --no-deps --no-build-isolation --offline /private/tmp/warn-transformer-pinned
PYTHONPATH=/private/tmp/warn-scraper-pinned uv pip install \
  --python /private/tmp/warn-rebuild-env/bin/python \
  --no-deps --no-build-isolation --offline /private/tmp/warn-scraper-pinned
uv pip install --python /private/tmp/warn-rebuild-env/bin/python \
  --no-deps --no-build-isolation --offline -e .
```

Prepare the environment first, then run the source-only replay with network
access disabled. The pinned input is
`data/source_snapshots/2026-09-23-ia-la-ny-tx-annual-reviewed.tar.gz`,
SHA-256 `4f9ccb698ea9d035514bb2c18d65d3d39ad66a7a9001691544580379ed9dc752`.
Use `python -m warnlive.migrate.source_bundle verify <bundle>` before the
rebuild and `--observed-at 2026-09-22 --source-only` for the rebuild. The
site data build also requires `--as-of 2026-09-22`. The latest v10
Rhode Island range checkpoint has 85,954 notices, 94,238 versions, zero
inferred links, and 8,898,111 reported affected workers in both replays. Its
40,069-row exception ledger SHA-256 is
`bfc7fe59cdff3f5958f1e82dd21761ac76afa769b66a56436b490651b7dc71ab`.
The 49-file CSV tree SHA-256 is
`c4089aaac136cbd9c28d25ad555ebee5dc5cfc582f5bed2e9da5b06c1429bba2`
and the 568-file site tree SHA-256 is
`8a23ae6faf99c52ab2a4076723f3281a33ba1c6fc8c4748364b8081faf47ed1c`.

This lock and the fresh temporary environment narrow the dependency gap.
The current validation still uses the shared working tree, which contains
uncommitted parallel changes. The final release gate requires a frozen code
commit, a clean checkout, and repeat replay from that exact code state. The
committed site `package-lock.json` supports `npm ci`. The current local site
check used Node 25.9.0 and npm 11.12.1; the final clean build should record
and pin its Node/npm runtime as well.

The [v11 Kansas worker-evidence checkpoint](ks-prior-worker-evidence-2026-09-23.md)
postdates the fresh-environment v10 replay above. Two v11 replays in the
current environment match at 85,954 notices, 94,369 versions, and 8,923,252
workers, with matching CSV/site/timing/dump outputs. V11 still requires the
same fresh Ubuntu and clean-release-commit replay gate; the earlier v10
fresh-environment result does not prove those later code changes.

The later [source-row accounting checkpoint](source-row-accounting-2026-09-23.md)
has the same canonical fingerprints as v11. Its two local replays match on a
135,036-entry exception manifest (SHA-256
`e131970c38761cf290239a290360f98ff6b9c26cf48a9e22005ffb69be67b861`)
and the full Python suite passes 292 tests. A final local replay with an
automatic accounting gate preserved those hashes. This too needs the clean release
commit and target-platform replay before promotion.
