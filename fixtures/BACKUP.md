# Backup of the fixtures' uncommitted data

What `fetch.py` pulls, and the caches, logs and run outputs behind the results the READMEs quote, are not committed.
On 3 October 2026 they existed only on mac-studio, so at the author's request they were archived on k11, where the
nightly Borg run copies `/mnt/storage` to the cold backup disk.

**Where.** `k11:/mnt/storage/archive/dossier/fixtures-data-2026-10-03/fixtures/`, with a provenance `README.md` one
level up. 2,497 files, about 83 MB: every file under `fixtures/` that git does not track, apart from `__pycache__`.

**Checked at copy time.** All 2,497 files on k11 matched the hashes taken on the Mac, a copy of one file with one byte
changed failed the same check, and the originals still matched afterwards. The manifest is committed here as
`backup/2026-10-03.MANIFEST.sha256` and also sits in the archive as `fixtures/MANIFEST.sha256`.

**Restore.**

```
rsync -a --exclude MANIFEST.sha256 k11:/mnt/storage/archive/dossier/fixtures-data-2026-10-03/fixtures/ fixtures/
cd fixtures && shasum -a 256 -c --quiet backup/2026-10-03.MANIFEST.sha256
```

Every fixture can also fetch its data again at its pinned version (`fetch.py`, which checks a digest), as long as the
upstream source stays up. The caches and run outputs cannot be fetched again.

**Still to do.** After the Borg run of 4 October 02:00, confirm with `borg list` and a one-file extract-and-compare.
