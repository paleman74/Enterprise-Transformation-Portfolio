# Fabric notebook: OneLake -> GitHub (handles large files)
# Cell 1 - config ---------------------------------------------------------
import os, shutil, subprocess, pathlib

SRC_ROOT   = "/lakehouse/default/Files/New"         # Lakehouse > Files/New (must be the attached default lakehouse)
REPO_URL   = "github.com/paleman74/Enterprise-Transformation-Portfolio.git"
BRANCH     = "main"
DEST_SUBDIR = "EvidenceBox"                          # folder inside the repo
WORKDIR    = "/tmp/gh_repo"

LFS_THRESHOLD = 50 * 1024**2     # >= 50 MB goes to Git LFS (GitHub warns at 50 MB)
HARD_LIMIT    = 2 * 1024**3      # LFS max per file on GitHub (2 GB; confirm for your plan)
BATCH_BYTES   = 500 * 1024**2    # push in ~500 MB batches to avoid timeouts/pack limits
BATCH_FILES   = 200

# Token: store in Azure Key Vault, never in the notebook
TOKEN = notebookutils.credentials.getSecret("https://<vault>.vault.azure.net/", "github-pat")

# Cell 2 - helpers --------------------------------------------------------
def git(*args, cwd=WORKDIR, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}\n{r.stderr.replace(TOKEN, '***')}")
    return r.stdout

def setup_repo():
    if os.path.exists(WORKDIR):
        shutil.rmtree(WORKDIR)
    url = f"https://x-access-token:{TOKEN}@{REPO_URL}"
    subprocess.run(["git", "clone", "--depth", "1", "-b", BRANCH, url, WORKDIR], check=True,
                   capture_output=True)
    git("config", "user.name", "fabric-migration")
    git("config", "user.email", "fabric-migration@example.com")
    git("config", "http.postBuffer", "524288000")
    lfs_ok = subprocess.run(["git", "lfs", "version"], capture_output=True).returncode == 0
    if lfs_ok:
        git("lfs", "install", "--local")
    return lfs_ok

# Cell 3 - run ------------------------------------------------------------
lfs_ok = setup_repo()
files = sorted(p for p in pathlib.Path(SRC_ROOT).rglob("*") if p.is_file())
print(f"{len(files)} files found; git-lfs available: {lfs_ok}")

# Resume support: skip files already in the repo with the same size
def already_done(src, dst):
    return dst.exists() and dst.stat().st_size == src.stat().st_size

batch, batch_bytes, pushed, skipped, failed = [], 0, 0, 0, []

def flush():
    global batch, batch_bytes, pushed
    if not batch:
        return
    git("add", "-A")
    if git("status", "--porcelain").strip():
        git("commit", "-m", f"Migrate {len(batch)} files from OneLake")
        git("push", "origin", BRANCH)
    pushed += len(batch)
    print(f"pushed {pushed} files so far")
    batch, batch_bytes = [], 0

for src in files:
    rel = src.relative_to(SRC_ROOT)
    dst = pathlib.Path(WORKDIR) / DEST_SUBDIR / rel
    size = src.stat().st_size

    if already_done(src, dst):
        skipped += 1
        continue
    if size >= HARD_LIMIT or (size >= LFS_THRESHOLD and not lfs_ok):
        failed.append((str(rel), size, "too large / no LFS"))
        continue

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)                 # streamed copy, no full-file memory load
    if size >= LFS_THRESHOLD:
        git("lfs", "track", f"{DEST_SUBDIR}/{rel.as_posix()}")
        git("add", ".gitattributes")

    batch.append(rel)
    batch_bytes += size
    if batch_bytes >= BATCH_BYTES or len(batch) >= BATCH_FILES:
        flush()

flush()
print(f"done. pushed={pushed} skipped={skipped} failed={len(failed)}")
for f in failed:
    print("NOT MIGRATED:", f)
